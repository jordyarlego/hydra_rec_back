import math
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Request
from models.schemas import CreateReportPayload
from services.security import hash_ip
from services.rate_limit import can_report
from services.alerts_engine import check_and_create_alerts

router = APIRouter()
logger = logging.getLogger(__name__)


def _haversine(lat1, lon1, lat2, lon2):
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return 2 * 6371 * math.asin(math.sqrt(a)) * 1000  # metros


@router.post("/api/reports", status_code=201)
async def create_report(payload: CreateReportPayload, request: Request):
    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown").split(",")[0].strip()
    ip_hash = hash_ip(ip)

    if not can_report(ip_hash):
        raise HTTPException(status_code=429, detail="Aguarde 5 minutos entre reports.")

    from services.supabase_client import get_service_client
    client = get_service_client()

    row = {
        "type":        payload.tipo,
        "severity":    payload.severidade,
        "lat":         payload.lat,
        "lon":         payload.lon,
        "bairro":      payload.bairro,
        "description": payload.descricao,
        "ip_hash":     ip_hash,
        "user_agent":  request.headers.get("User-Agent", "")[:200],
    }
    try:
        res = client.table("reports").insert(row).execute()
    except Exception as e:
        logger.error(f"report insert failed: {e}")
        raise HTTPException(status_code=500, detail="Erro ao salvar report.")

    if payload.bairro:
        check_and_create_alerts(payload.bairro)

    return {"id": res.data[0]["id"], "status": "criado"}


@router.get("/api/reports/nearby")
async def get_nearby_reports(lat: float, lon: float, radius: float = 2000):
    from services.supabase_client import get_client
    client = get_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    delta_lat = radius / 111000
    delta_lon = radius / (111000 * abs(math.cos(math.radians(lat))) or 1)
    try:
        res = (client.table("reports")
               .select("id,type,severity,lat,lon,bairro,description,confirmed_count,created_at")
               .eq("resolved", False)
               .gte("created_at", cutoff)
               .gte("lat", lat - delta_lat).lte("lat", lat + delta_lat)
               .gte("lon", lon - delta_lon).lte("lon", lon + delta_lon)
               .order("created_at", desc=True)
               .execute())
        reports = [r for r in (res.data or []) if _haversine(lat, lon, r["lat"], r["lon"]) <= radius]
    except Exception as e:
        logger.error(f"nearby reports fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Erro ao buscar reports.")
    return {"reports": reports, "count": len(reports)}


@router.post("/api/reports/{report_id}/confirm", status_code=200)
async def confirm_report(report_id: str, request: Request):
    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown").split(",")[0].strip()
    ip_hash = hash_ip(ip)
    from services.supabase_client import get_service_client
    client = get_service_client()
    try:
        res = client.table("reports").select("id,confirmed_count,ip_hash,bairro").eq("id", report_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Report não encontrado.")
        r = res.data[0]
        if r["ip_hash"] == ip_hash:
            raise HTTPException(status_code=403, detail="Não pode confirmar o próprio report.")
        client.table("reports").update({"confirmed_count": r["confirmed_count"] + 1}).eq("id", report_id).execute()
        if r.get("bairro"):
            check_and_create_alerts(r["bairro"])
        return {"confirmed_count": r["confirmed_count"] + 1}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/alerts/active")
async def get_active_alerts(bairro: str | None = None):
    from services.supabase_client import get_client
    client = get_client()
    try:
        q = client.table("alerts").select("*").eq("active", True).gt("expires_at", datetime.now(timezone.utc).isoformat())
        if bairro:
            q = q.eq("bairro", bairro)
        res = q.order("created_at", desc=True).execute()
        return {"alerts": res.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
