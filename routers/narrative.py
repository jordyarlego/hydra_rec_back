from fastapi import APIRouter
from models.schemas import NarrativeRequest
from services.ai_narrative import generate_narrative

router = APIRouter()


@router.post("/api/narrative")
async def get_narrative(request: NarrativeRequest):
    narrative, model_used = await generate_narrative(
        bairro=request.cityName,
        risk=request.riskData,
        consensus=request.consensusData or {},
        nearby_reports=request.nearbyReports or [],
        apac_boletim=request.apacBoletim,
        weather=request.weather,
    )
    return {"narrative": narrative, "model_used": model_used}
