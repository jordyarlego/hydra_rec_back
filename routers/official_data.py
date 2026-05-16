"""
Endpoints públicos /api/official/* — dados urbanos oficiais do Recife.
Apenas leitura. Nenhum dado interno da prefeitura exposto.
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/official", tags=["official-data"])
logger = logging.getLogger(__name__)


def _db():
    from services.supabase_client import get_client
    return get_client()


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
