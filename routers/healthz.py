import os
import time
import httpx
from fastapi import APIRouter

router = APIRouter()


async def _check_open_meteo() -> dict:
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast?latitude=-8.05&longitude=-34.88&current=temperature_2m"
            )
        return {"status": "ok", "latency_ms": round((time.time() - start) * 1000)}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


async def _check_supabase() -> dict:
    start = time.time()
    try:
        from services.supabase_client import get_client
        client = get_client()
        client.table("reports").select("id").limit(1).execute()
        return {"status": "ok", "latency_ms": round((time.time() - start) * 1000)}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


async def _check_gemini() -> dict:
    key = os.getenv("GEMINI_API_KEY", "")
    if not key:
        return {"status": "error", "detail": "GEMINI_API_KEY não configurada"}
    return {"status": "ok"}


@router.get("/api/healthz")
async def healthz():
    import asyncio
    open_meteo, supabase, gemini = await asyncio.gather(
        _check_open_meteo(),
        _check_supabase(),
        _check_gemini(),
    )

    deps = {
        "open_meteo": open_meteo,
        "supabase": supabase,
        "gemini": gemini,
        "openweather": {"status": "ok" if os.getenv("OPENWEATHER_KEY") else "not_configured"},
        "openrouteservice": {"status": "ok" if os.getenv("OPENROUTESERVICE_KEY") else "not_configured"},
    }

    all_ok = all(v.get("status") == "ok" for v in deps.values())
    return {
        "status": "healthy" if all_ok else "degraded",
        "dependencies": deps,
        "timestamp": time.time(),
    }
