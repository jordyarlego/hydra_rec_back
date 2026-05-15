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
        return {"status": "not_configured", "detail": "usa fallback local quando necessário"}
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
        "nvidia": {"status": "ok" if os.getenv("NVIDIA_API_KEY") else "not_configured"},
        "openweather": {"status": "ok" if os.getenv("OPENWEATHER_KEY") else "not_configured"},
        "web_push": {"status": "ok" if os.getenv("VAPID_PUBLIC_KEY") and os.getenv("VAPID_PRIVATE_KEY") else "not_configured"},
        "routing": {"status": "ok", "provider": "OSRM público, sem chave"},
    }

    required_ok = open_meteo.get("status") == "ok" and supabase.get("status") == "ok"
    return {
        "status": "healthy" if required_ok else "degraded",
        "dependencies": deps,
        "timestamp": time.time(),
    }
