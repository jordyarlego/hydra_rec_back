"""
Endpoints públicos /api/official/* — dados urbanos oficiais do Recife.
Apenas leitura. Nenhum dado interno da prefeitura exposto.
"""
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/official", tags=["official-data"])
logger = logging.getLogger(__name__)


def _db():
    from services.supabase_client import get_client
    return get_client()


def _haversine_m(lat1, lon1, lat2, lon2):
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * 6371 * math.asin(math.sqrt(a)) * 1000


@router.get("/neighborhoods")
async def list_neighborhoods(rpa: Optional[str] = Query(None)):
    """Lista bairros normalizados, opcionalmente filtrados por RPA."""
    try:
        q = _db().table("official_neighborhoods").select(
            "id,name,rpa,rpa_code,microregion"
        )
        if rpa:
            q = q.eq("rpa", rpa)
        res = q.order("name").execute()
        return {"data": res.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rpas")
async def list_rpas():
    """Lista as RPAs únicas presentes nos dados."""
    try:
        res = _db().table("official_neighborhoods").select("rpa,rpa_code").execute()
        seen: dict[str, int] = {}
        for r in (res.data or []):
            if r.get("rpa"):
                seen[r["rpa"]] = r.get("rpa_code") or 0
        data = [{"rpa": k, "code": v} for k, v in sorted(seen.items(), key=lambda x: x[1])]
        return {"data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/microregions")
async def list_microregions():
    """Lista as microrregiões únicas."""
    try:
        res = _db().table("official_neighborhoods").select("microregion,microregion_code").execute()
        seen: dict[str, int] = {}
        for r in (res.data or []):
            if r.get("microregion"):
                seen[r["microregion"]] = r.get("microregion_code") or 0
        data = [{"microregion": k, "code": v} for k, v in sorted(seen.items(), key=lambda x: x[1])]
        return {"data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/roads/search")
async def search_roads(
    q: str = Query(..., min_length=2),
    limit: int = Query(20, le=50),
):
    """Busca logradouros por nome."""
    try:
        res = (
            _db()
            .table("official_roads")
            .select("id,name,neighborhood,rpa")
            .ilike("name", f"%{q}%")
            .limit(limit)
            .execute()
        )
        return {"data": res.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/nearby")
async def get_official_nearby(
    lat: float = Query(...),
    lon: float = Query(...),
    radius: int = Query(500, ge=50, le=5000),
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(10, ge=1, le=30),
):
    """
    Chamados oficiais ABERTOS pela prefeitura dentro do raio (m) e janela (dias).
    Público — pra cidadão ver o que já tá em andamento por perto.

    Ordenado por distância ASC.
    """
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        delta_lat = radius / 111_000
        cos_lat = abs(math.cos(math.radians(lat))) or 1
        delta_lon = radius / (111_000 * cos_lat)

        res = (
            _db()
            .table("official_service_requests")
            .select(
                "id, source, agency, service_type, category, status, "
                "neighborhood, street_name, lat, lon, opened_at, closed_at"
            )
            .gte("opened_at", cutoff)
            .not_.is_("lat", "null")
            .gte("lat", lat - delta_lat)
            .lte("lat", lat + delta_lat)
            .gte("lon", lon - delta_lon)
            .lte("lon", lon + delta_lon)
            .limit(200)
            .execute()
        )

        rows = []
        for row in (res.data or []):
            if row.get("lat") is None or row.get("lon") is None:
                continue
            d = _haversine_m(lat, lon, row["lat"], row["lon"])
            if d > radius:
                continue
            rows.append({
                "id":            row["id"],
                "source":        row.get("source"),
                "agency":        row.get("agency"),
                "service_type":  row.get("service_type"),
                "category":      row.get("category"),
                "status":        row.get("status"),
                "neighborhood":  row.get("neighborhood"),
                "street_name":   row.get("street_name"),
                "lat":           row["lat"],
                "lon":           row["lon"],
                "opened_at":     row.get("opened_at"),
                "closed_at":     row.get("closed_at"),
                "distance_m":    int(d),
            })

        rows.sort(key=lambda r: r["distance_m"])
        return {
            "data":   rows[:limit],
            "total":  len(rows),
            "radius_m": radius,
            "days":   days,
        }
    except Exception as e:
        logger.error(f"official/nearby failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/hotspots")
async def get_hotspots(
    tipo: Optional[str] = Query(None),
    bairro: Optional[str] = Query(None),
    rpa: Optional[str] = Query(None),
    limit: int = Query(30, le=100),
):
    """
    Hotspots: bairros/ruas com maior recorrência de problemas.
    Não expõe dados internos — apenas informações geográficas e de recorrência.
    """
    try:
        q = _db().table("report_official_crossings").select(
            "neighborhood,rpa,nearest_road_name,recurrence_score"
        ).gte("recurrence_score", 1.0)
        if bairro:
            q = q.ilike("neighborhood", f"%{bairro}%")
        if rpa:
            q = q.eq("rpa", rpa)
        q = q.order("recurrence_score", desc=True).limit(limit)
        res = q.execute()

        rows = res.data or []

        # Se tipo especificado, filtra via chamados oficiais parecidos
        if tipo and rows:
            neighborhoods = list({r["neighborhood"] for r in rows if r.get("neighborhood")})
            sr_res = (
                _db()
                .table("official_service_requests")
                .select("neighborhood,category")
                .in_("neighborhood", neighborhoods[:20])
                .eq("category", tipo)
                .execute()
            )
            with_type = {r["neighborhood"] for r in (sr_res.data or [])}
            rows = [r for r in rows if r.get("neighborhood") in with_type]

        return {"data": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
