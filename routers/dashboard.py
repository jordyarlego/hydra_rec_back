"""
Dashboard V3 — fonte única APAC + reports próximos.

Endpoints:
  GET /api/dashboard/{bairro}      retorna weather APAC + risk + reports nearby
  GET /api/explain/{bairro}        IA narra o score (curto)

A geocodificação do bairro usa a tabela estática `data/bairro_coords.py`,
removendo o boundary do Open-Meteo.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException

from services.apac_official import weather_at
from services.weather_enrich import enrich_weather
from services.risk_score import calculate_risk_score_v2
from services.heat_index import heat_index_steadman, heat_risk_label
from services.ai_explain import explain_score
from services.supabase_client import get_client
from data.vulnerability import FLOOD_VULNERABILITY, DEFAULT_VULNERABILITY  # noqa: F401  (usado indireto)
from data.bairros_coords import BAIRRO_COORDS

logger = logging.getLogger(__name__)
router = APIRouter()


def _resolve_bairro_latlon(bairro: str) -> tuple[float, float]:
    coords = BAIRRO_COORDS.get(bairro)
    if not coords:
        # tenta normalização leve case-insensitive
        norm = next((b for b in BAIRRO_COORDS if b.lower() == bairro.lower()), None)
        if not norm:
            raise HTTPException(status_code=404, detail=f"Bairro desconhecido: {bairro}")
        coords = BAIRRO_COORDS[norm]
    return coords[0], coords[1]


def _to_consensus(snap: Optional[dict]) -> dict:
    """Adapta o snapshot APAC ao contrato do calculate_risk_score_v2."""
    if not snap:
        return {
            "rain_next_24h_mm": 0.0,
            "rain_past_24h_mm": 0.0,
            "humidity":         70,
            "pressure":         1013,
            "temperature":      28,
            "wind_speed_kmh":   0,
            "confidence":       "BAIXA",
            "sources_count":    0,
            "source":           "none",
        }
    # APAC não traz previsão futura nem 24h acumulado direto. Usamos a leitura
    # corrente como proxy ponderado: 1h × 6 ≈ próximas 6h se a chuva persistir.
    rain_now = snap.get("rain_1h_mm") or 0.0
    rain_past = snap.get("rain_24h_mm") or rain_now
    # IMPORTANTE: NÃO inventamos fallback pra temperatura/umidade.
    # Se a estação APAC mais próxima não publicar essa medida, retornamos
    # null e o frontend mostra "—" em vez de 28°/70% (que era engano).
    # Por isso a leitura pode divergir do site oficial APAC: cada estação
    # tem leitura própria, e usamos a mais próxima do bairro selecionado.
    return {
        "rain_next_24h_mm": round(rain_now * 6, 2),
        "rain_past_24h_mm": round(rain_past, 2),
        "humidity":         snap.get("humidity_pct"),
        "pressure":         None,
        "temperature":      snap.get("temp_c"),
        "wind_speed_kmh":   snap.get("wind_kmh") or 0,
        "station_name":     snap.get("station_name"),
        "station_distance_m": snap.get("station_distance_m"),
        "confidence":       "ALTA" if snap.get("source") == "cemaden" else "MEDIA",
        "sources_count":    1,
        "source":           snap.get("source", "apac"),
    }


def _haversine_m(lat1, lon1, lat2, lon2):
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return 2 * 6371 * math.asin(math.sqrt(a)) * 1000


def _count_nearby_reports(lat: float, lon: float, radius_m: int = 2000) -> int:
    try:
        client = get_client()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        delta_lat = radius_m / 111000
        delta_lon = radius_m / (111000 * abs(math.cos(math.radians(lat))) or 1)
        res = (client.table("reports")
               .select("lat,lon")
               .eq("resolved", False)
               .gte("created_at", cutoff)
               .gte("lat", lat - delta_lat).lte("lat", lat + delta_lat)
               .gte("lon", lon - delta_lon).lte("lon", lon + delta_lon)
               .execute())
        return sum(1 for r in (res.data or []) if _haversine_m(lat, lon, r["lat"], r["lon"]) <= radius_m)
    except Exception as e:
        logger.debug(f"_count_nearby_reports failed: {e}")
        return 0


async def fetch_dashboard(bairro: str) -> dict:
    """Reusada também pelo WebSocket — não levanta HTTPException."""
    lat, lon = _resolve_bairro_latlon(bairro)
    snap = await weather_at(lat, lon)
    weather = await enrich_weather(snap)
    consensus = _to_consensus(snap)
    reports_count = _count_nearby_reports(lat, lon)
    apac_nivel = (weather.get("alert") or {}).get("nivel")

    risk = calculate_risk_score_v2(
        weather_consensus=consensus,
        elevation=10.0,
        tide={"height": 1.5, "trend": "Desconhecido"},
        bairro=bairro,
        reports_nearby_count=reports_count,
        apac_alert_nivel=apac_nivel,
    )

    temp = consensus["temperature"]
    humidity = consensus["humidity"]
    hi = heat_index_steadman(temp, humidity)

    return {
        "location":   {"name": bairro, "latitude": lat, "longitude": lon},
        "weather":    weather,
        "risk":       risk,
        "consensus":  consensus,
        "heatIndex":  {"value": hi, "risk": heat_risk_label(hi)},
        "reportsNearbyCount": reports_count,
    }


@router.get("/api/dashboard/{bairro}")
async def get_dashboard_data(bairro: str):
    try:
        return await fetch_dashboard(bairro)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"dashboard failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/explain/{bairro}")
async def get_score_explanation(bairro: str):
    try:
        lat, lon = _resolve_bairro_latlon(bairro)
        snap = await weather_at(lat, lon)
        consensus = _to_consensus(snap)
        risk = calculate_risk_score_v2(
            weather_consensus=consensus,
            elevation=10.0,
            tide={"height": 1.5, "trend": "Desconhecido"},
            bairro=bairro,
            reports_nearby_count=_count_nearby_reports(lat, lon),
        )
        explanation = await explain_score(bairro, risk)
        return {"explanation": explanation, "score": risk["score"], "nivel": risk["nivel"]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"explain failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
