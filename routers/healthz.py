import os
import time
import httpx
from fastapi import APIRouter

router = APIRouter()


async def _check_apac() -> dict:
    """Pinga o endpoint cemaden da APAC — fonte única de chuva no V3."""
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get("http://dados.apac.pe.gov.br:41120/cemaden/")
            resp.raise_for_status()
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
        return {"status": "not_configured", "detail": "IA usa fallback local quando ausente"}
    return {"status": "ok"}


async def _check_storage() -> dict:
    try:
        from services.storage import bucket_exists, _BUCKET  # type: ignore
        exists = bucket_exists()
        if exists is None:
            return {"status": "unknown", "bucket": _BUCKET, "detail": "SDK não suporta listagem"}
        return {"status": "ok" if exists else "missing", "bucket": _BUCKET}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@router.get("/api/healthz")
async def healthz():
    import asyncio
    apac, supabase, gemini, storage = await asyncio.gather(
        _check_apac(),
        _check_supabase(),
        _check_gemini(),
        _check_storage(),
    )

    deps = {
        "apac":     apac,
        "supabase": supabase,
        "gemini":   gemini,
        "storage":  storage,
        "web_push": {
            "status": "ok"
            if os.getenv("VAPID_PUBLIC_KEY") and os.getenv("VAPID_PRIVATE_KEY")
            else "not_configured"
        },
    }

    required_ok = apac.get("status") == "ok" and supabase.get("status") == "ok"
    return {
        "status": "healthy" if required_ok else "degraded",
        "version": "v3",
        "dependencies": deps,
        "timestamp": time.time(),
    }
