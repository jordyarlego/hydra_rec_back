import logging
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

from services.ai_assistant import assist_report
from services.ai_validator import persist_validation

router = APIRouter(prefix="/api/ai", tags=["ai-reports"])
logger = logging.getLogger(__name__)


class AssistPayload(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


@router.post("/report-assist")
async def report_assist(payload: AssistPayload):
    return await assist_report(payload.lat, payload.lon)


@router.post("/describe-photo")
async def describe_photo_now(photo: UploadFile = File(...)):
    """
    IA analisa foto na hora — usado pelo PhotoCapture pra mostrar
    descrição automática enquanto o usuário ainda está montando o report.
    Não persiste nada. Apenas roda o classificador e retorna.
    """
    if not photo or not photo.filename:
        raise HTTPException(status_code=400, detail="Foto ausente.")
    try:
        data = await photo.read()
    except Exception as e:
        logger.error(f"read photo failed: {e}")
        raise HTTPException(status_code=400, detail="Falha ao ler a foto.")

    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Foto maior que 5 MB.")

    try:
        from services.ai_vision import describe_photo
        result = await describe_photo(data)
        return {
            "description": result.get("description"),
            "suggested_type": result.get("type") or result.get("suggested_type"),
            "confidence": result.get("confidence"),
            "ai_used": bool(result.get("ai_used")),
            "fallback_reason": result.get("fallback_reason"),
        }
    except Exception as e:
        logger.warning(f"describe_photo failed: {e}")
        return {"description": None, "suggested_type": None, "confidence": None, "ai_used": False}


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
