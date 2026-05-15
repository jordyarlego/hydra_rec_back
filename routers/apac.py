from fastapi import APIRouter
from services.apac_scraper import fetch_apac_boletim

router = APIRouter()


@router.get("/api/apac/boletim")
async def get_apac_boletim():
    result = await fetch_apac_boletim()
    if not result or result.get("_empty"):
        return {"boletim": None}
    return {"boletim": result}
