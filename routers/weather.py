"""
Router /api/weather — fonte única APAC.

Endpoints públicos consumidos pelo frontend e por outros serviços:
  GET /api/weather?lat=&lon=        snapshot consolidado para um ponto
  GET /api/weather/stations?kind=   lista estações cacheadas (debug/UI admin)
"""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from services.apac_official import (
    RMR_BBOX,
    list_stations,
    weather_at,
)
from services.weather_enrich import (
    enrich_weather,
    nearest_rmr_stations,
    top_rmr_stations,
    monthly_climatology,
)

router = APIRouter()


@router.get("/api/weather")
async def get_weather(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
):
    snap = await weather_at(lat, lon)
    if snap is None:
        raise HTTPException(
            status_code=404,
            detail="Nenhuma estação APAC dentro do raio para este ponto.",
        )
    return await enrich_weather(snap)


@router.get("/api/weather/outlook")
async def get_outlook(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
):
    """
    Cenário APAC para o ponto:
      - nearest_stations: pluviômetros CEMADEN mais próximos (até 4)
      - top_rmr: top 3 da RMR por intensidade (pra detectar chuva em outro bairro)
      - climatology: média histórica do mês corrente
    """
    nearest = await nearest_rmr_stations(lat, lon, limit=4)
    top     = await top_rmr_stations(limit=3)
    climatology = await monthly_climatology(lat, lon)
    return {
        "nearest_stations": nearest,
        "top_rmr":          top,
        "climatology":      climatology,
    }


@router.get("/api/weather/stations")
async def get_stations(
    kind: Literal["cemaden", "meteorologia24h", "climatologico"] = Query(...),
    bbox: str | None = Query(
        None,
        description="min_lat,max_lat,min_lon,max_lon — default: RMR Recife",
    ),
):
    if bbox:
        try:
            parts = [float(x) for x in bbox.split(",")]
            if len(parts) != 4:
                raise ValueError
            box = tuple(parts)
        except ValueError:
            raise HTTPException(status_code=400, detail="bbox inválido")
    else:
        box = RMR_BBOX

    items = await list_stations(kind, bbox=box)
    return {"kind": kind, "bbox": box, "count": len(items), "stations": items}
