from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.ai_assistant import assist_report
from services.ai_validator import persist_validation

router = APIRouter(prefix="/api/ai", tags=["ai-reports"])


class AssistPayload(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


@router.post("/report-assist")
async def report_assist(payload: AssistPayload):
    return await assist_report(payload.lat, payload.lon)


@router.post("/revalidate/{report_id}")
async def revalidate(report_id: str):
    from services.supabase_client import get_service_client
    client = get_service_client()
    res = client.table("reports").select("*").eq("id", report_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Report não encontrado.")
    report = res.data[0]
    if report.get("weather_snapshot_id"):
        ws = client.table("weather_snapshots").select("*").eq("id", report["weather_snapshot_id"]).execute()
        report["weather"] = (ws.data or [None])[0]
    return await persist_validation(report_id, report)
