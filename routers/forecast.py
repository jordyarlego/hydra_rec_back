"""
Previsão do Hydra Score para as próximas 6 horas.

Para cada hora futura monta um consensus multi-fonte:
  - Open-Meteo: chuva horária (fonte primária, resolução 1h)
  - OpenWeatherMap: blocos 3h distribuídos por hora (segunda fonte)
  - INMET A301/A357: chuva observada na última hora (ancora o passado)
  - Maré atual (tabuademares.com) — varia lentamente, usada para todos os slots

A fórmula real calculate_risk_score_v2() é aplicada a cada slot com o
consensus calculado — mesma lógica do dashboard, sem aproximações.
"""
import asyncio
import statistics
from datetime import datetime, timedelta
from fastapi import APIRouter

from services.weather.open_meteo import geocode_city, fetch_weather_data, fetch_elevation
from services.weather.owm import fetch_owm_hourly_slots
from services.weather.inmet import fetch_inmet_nearest
from services.weather.tides import scrape_tide_data
from services.risk_score import calculate_risk_score_v2
from services.cache import cache_get, cache_set
from data.vulnerability import FLOOD_VULNERABILITY, DEFAULT_VULNERABILITY

router = APIRouter()


def _fallback_forecast(bairro: str, reason: str) -> dict:
    """Retorna uma previsão degradada em vez de derrubar a UI quando APIs externas falham."""
    consensus = {
        "rain_next_24h_mm": 0.0,
        "rain_past_24h_mm": 0.0,
        "humidity": 75,
        "pressure": 1013,
        "confidence": "BAIXA",
        "sources_count": 0,
    }
    tide = {"height": 1.5, "trend": "Desconhecido"}
    risk = calculate_risk_score_v2(consensus, 10.0, tide, bairro)
    now = datetime.utcnow()
    forecast = [
        {
            "hour_offset": i + 1,
            "time": (now + timedelta(hours=i + 1)).isoformat(timespec="minutes") + "Z",
            "score": risk["score"],
            "nivel": risk["nivel"],
            "confidence": "BAIXA",
            "sources": 0,
            "precip_om_mm": 0.0,
            "precip_owm_mm": None,
            "fallback": True,
        }
        for i in range(6)
    ]
    return {
        "bairro": bairro,
        "generated_at": now.isoformat() + "Z",
        "sources_used": ["Fallback local"],
        "forecast": forecast,
        "fallback": True,
        "warning": f"Previsão externa indisponível: {reason}",
    }


def _consensus_for_slot(
    om_rain_1h: float,
    owm_rain_1h: float | None,
    rain_window_6h_om: float,
    rain_window_6h_owm: float | None,
    rain_past_24h: float,
    inmet_rain_h: float | None,
    humidity: float,
    pressure: float,
) -> dict:
    """Monta consensus de chuva para um slot horário usando todas as fontes disponíveis."""
    next_values: list[float] = [rain_window_6h_om]
    if owm_rain_1h is not None:
        next_values.append(rain_window_6h_owm or 0.0)

    past_values: list[float] = [rain_past_24h]
    if inmet_rain_h is not None:
        # INMET dá chuva da última hora — extrapola para 24h (conservador: ×6)
        past_values.append(min(inmet_rain_h * 6, 80.0))

    mean_next = statistics.mean(next_values)
    mean_past = statistics.mean(past_values)
    stdev = statistics.stdev(next_values) if len(next_values) > 1 else 0.0
    confidence = "ALTA" if stdev < 3 else ("MEDIA" if stdev < 8 else "BAIXA")

    return {
        "rain_next_24h_mm": round(mean_next, 2),
        "rain_past_24h_mm": round(mean_past, 2),
        "humidity":         humidity,
        "pressure":         pressure,
        "confidence":       confidence,
        "sources_count":    len(next_values) + (1 if inmet_rain_h is not None else 0),
    }


@router.get("/api/forecast/{bairro}")
async def get_risk_forecast(bairro: str):
    cache_key = f"forecast_{bairro}"
    cached = cache_get(cache_key, 300)
    if cached:
        return cached

    try:
        geo = await geocode_city(bairro)
        lat, lon = geo["latitude"], geo["longitude"]

        # Busca todas as fontes em paralelo
        weather_data, elevation, tide, owm_slots, inmet = await asyncio.gather(
            fetch_weather_data(lat, lon),
            fetch_elevation(lat, lon),
            scrape_tide_data(),
            fetch_owm_hourly_slots(lat, lon, n_hours=6),
            fetch_inmet_nearest(lat, lon),
            return_exceptions=True,
        )

        if isinstance(weather_data, Exception):
            raise weather_data
        if isinstance(elevation, Exception):
            elevation = 10.0
        if isinstance(tide, Exception):
            tide = {"height": 1.5, "trend": "Desconhecido"}
        if isinstance(owm_slots, Exception):
            owm_slots = None
        if isinstance(inmet, Exception):
            inmet = None

        hourly  = weather_data.get("hourly", {})
        precip  = hourly.get("precipitation", [])
        temps   = hourly.get("temperature_2m", [])
        times   = hourly.get("time", [])
        current = weather_data.get("current", {})

        past24h  = sum(p or 0.0 for p in precip[:24])
        humidity = current.get("relative_humidity_2m", 70)
        pressure = current.get("surface_pressure", 1013)
        vuln     = FLOOD_VULNERABILITY.get(bairro, DEFAULT_VULNERABILITY)
        inmet_h  = inmet.get("rain_last_hour_mm") if isinstance(inmet, dict) else None

        sources_used = ["Open-Meteo"]
        if owm_slots:
            sources_used.append("OpenWeatherMap")
        if inmet_h is not None:
            sources_used.append(f"INMET-{inmet.get('station','')}")

        forecast = []
        for i in range(6):
            om_1h = float(precip[24 + i] or 0.0) if 24 + i < len(precip) else 0.0
            om_6h = sum(float(precip[24 + i + j] or 0.0) for j in range(6)
                        if 24 + i + j < len(precip))

            owm_1h = float(owm_slots[i]) if owm_slots and i < len(owm_slots) else None
            owm_6h = sum(float(owm_slots[i + j]) for j in range(6)
                         if owm_slots and i + j < len(owm_slots)) if owm_slots else None

            consensus = _consensus_for_slot(
                om_rain_1h=om_1h,
                owm_rain_1h=owm_1h,
                rain_window_6h_om=om_6h,
                rain_window_6h_owm=owm_6h,
                rain_past_24h=past24h,
                inmet_rain_h=inmet_h,
                humidity=humidity,
                pressure=pressure,
            )

            risk = calculate_risk_score_v2(consensus, elevation, tide, bairro)

            slot: dict = {
                "hour_offset":    i + 1,
                "score":          risk["score"],
                "nivel":          risk["nivel"],
                "confidence":     consensus["confidence"],
                "sources":        consensus["sources_count"],
                "precip_om_mm":   round(om_1h, 1),
                "precip_owm_mm":  round(owm_1h, 1) if owm_1h is not None else None,
            }
            if 24 + i < len(times):
                slot["time"] = times[24 + i]
            if 24 + i < len(temps) and temps[24 + i] is not None:
                slot["temp"] = round(float(temps[24 + i]))

            forecast.append(slot)

        result = {
            "bairro":        bairro,
            "generated_at":  datetime.utcnow().isoformat() + "Z",
            "sources_used":  sources_used,
            "forecast":      forecast,
        }
        cache_set(cache_key, result)
        return result

    except Exception as e:
        result = _fallback_forecast(bairro, str(e))
        cache_set(cache_key, result)
        return result
