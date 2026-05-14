import logging
from fastapi import APIRouter, HTTPException
from models.schemas import RouteRiskRequest
from services.routing import get_route, analyze_route_risk

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/route-risk")
async def route_risk(req: RouteRiskRequest):
    try:
        route_json = await get_route(
            origin=(req.origem_lat, req.origem_lon),
            destination=(req.destino_lat, req.destino_lon),
            profile=req.perfil,
        )
        result = await analyze_route_risk(route_json)
        return result
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.warning(f"route-risk error: {e}")
        raise HTTPException(status_code=502, detail="Erro ao calcular rota. Tente novamente.")
