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


async def _check_schema() -> dict:
    """
    Verifica se as colunas/tabelas da migration V3 existem.
    Usado pelo banner de diagnóstico no app.
    """
    try:
        from services.supabase_client import get_client
        client = get_client()

        checks = {}

        # Coluna photo_url em reports
        try:
            client.table("reports").select("photo_url").limit(1).execute()
            checks["reports.photo_url"] = True
        except Exception:
            checks["reports.photo_url"] = False

        # Coluna ai_validation_score
        try:
            client.table("reports").select("ai_validation_score").limit(1).execute()
            checks["reports.ai_validation_score"] = True
        except Exception:
            checks["reports.ai_validation_score"] = False

        # Tabela weather_snapshots
        try:
            client.table("weather_snapshots").select("id").limit(1).execute()
            checks["table.weather_snapshots"] = True
        except Exception:
            checks["table.weather_snapshots"] = False

        # Tabela tickets
        try:
            client.table("tickets").select("id").limit(1).execute()
            checks["table.tickets"] = True
        except Exception:
            checks["table.tickets"] = False

        # Tabela official_service_requests (ODH)
        try:
            client.table("official_service_requests").select("id").limit(1).execute()
            checks["table.official_service_requests"] = True
        except Exception:
            checks["table.official_service_requests"] = False

        # V4 — Triagem v2
        for col in (
            "photo_ai_is_urban_problem",
            "bucket",
            "rejection_reason",
        ):
            try:
                client.table("reports").select(col).limit(1).execute()
                checks[f"reports.{col}"] = True
            except Exception:
                checks[f"reports.{col}"] = False

        for col in ("assigned_org", "kanban_state", "sla_deadline"):
            try:
                client.table("tickets").select(col).limit(1).execute()
                checks[f"tickets.{col}"] = True
            except Exception:
                checks[f"tickets.{col}"] = False

        v3_civic_keys = (
            "reports.photo_url",
            "reports.ai_validation_score",
            "table.weather_snapshots",
            "table.tickets",
        )
        v4_keys = (
            "reports.photo_ai_is_urban_problem",
            "reports.bucket",
            "reports.rejection_reason",
            "tickets.assigned_org",
            "tickets.kanban_state",
            "tickets.sla_deadline",
        )

        missing = [k for k, v in checks.items() if not v]
        return {
            "v3_civic_applied": all(checks.get(k) for k in v3_civic_keys),
            "v3_odh_applied":   checks.get("table.official_service_requests", False),
            "v4_triagem_applied": all(checks.get(k) for k in v4_keys),
            "missing":          missing,
            "checks":           checks,
        }
    except Exception as e:
        return {"v3_civic_applied": False, "v3_odh_applied": False, "error": str(e)}


@router.get("/api/healthz/schema")
async def healthz_schema():
    """Diagnóstico do schema — usado pelo banner do app."""
    return await _check_schema()


@router.get("/api/healthz/photo-debug")
async def healthz_photo_debug():
    """
    Lista o bucket + faz um upload TESTE pra detectar se as credenciais
    e o bucket estão corretos. Útil quando 'a foto não aparece'.
    """
    from services.supabase_client import get_service_client
    from services.storage import _BUCKET  # type: ignore
    import io, uuid
    from PIL import Image

    diagnostico = {"bucket": _BUCKET, "items": [], "tests": {}}
    try:
        client = get_service_client()
        # 1. Listar bucket inteiro (sem subpasta)
        try:
            listed_root = client.storage.from_(_BUCKET).list("")
            diagnostico["tests"]["list_root"] = {
                "ok": True,
                "count": len(listed_root or []),
                "first": [getattr(x, "name", None) or (x.get("name") if isinstance(x, dict) else None) for x in (listed_root or [])[:5]],
            }
        except Exception as e:
            diagnostico["tests"]["list_root"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

        # 2. Listar subpasta reports/
        try:
            listed = client.storage.from_(_BUCKET).list("reports")
            diagnostico["tests"]["list_reports"] = {
                "ok": True,
                "count": len(listed or []),
                "first": [getattr(x, "name", None) or (x.get("name") if isinstance(x, dict) else None) for x in (listed or [])[:5]],
            }
            for entry in (listed or [])[:5]:
                name = getattr(entry, "name", None) or (entry.get("name") if isinstance(entry, dict) else None)
                if not name:
                    continue
                path = f"reports/{name}"
                url = client.storage.from_(_BUCKET).get_public_url(path)
                if isinstance(url, dict):
                    url = url.get("publicUrl") or url.get("publicURL")
                url = str(url).rstrip("?")
                status = "unknown"
                try:
                    async with httpx.AsyncClient(timeout=4.0) as c:
                        r = await c.head(url, follow_redirects=True)
                        status = f"HTTP {r.status_code}"
                except Exception as e:
                    status = f"err: {type(e).__name__}"
                diagnostico["items"].append({"path": path, "url": url, "status": status})
        except Exception as e:
            diagnostico["tests"]["list_reports"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

        # 3. UPLOAD TESTE: tenta subir uma imagem mínima
        try:
            img = Image.new("RGB", (32, 32), color="purple")
            buf = io.BytesIO()
            img.save(buf, "JPEG")
            test_name = f"_test/probe-{uuid.uuid4().hex}.jpg"
            client.storage.from_(_BUCKET).upload(
                path=test_name,
                file=buf.getvalue(),
                file_options={"content-type": "image/jpeg", "upsert": "false"},
            )
            url = client.storage.from_(_BUCKET).get_public_url(test_name)
            if isinstance(url, dict):
                url = url.get("publicUrl") or url.get("publicURL")
            url = str(url).rstrip("?")
            status = "unknown"
            try:
                async with httpx.AsyncClient(timeout=4.0) as c:
                    r = await c.head(url, follow_redirects=True)
                    status = f"HTTP {r.status_code}"
            except Exception as e:
                status = f"err: {type(e).__name__}"
            diagnostico["tests"]["upload_probe"] = {"ok": True, "path": test_name, "url": url, "fetch_status": status}
            # Tenta deletar pra não poluir
            try:
                client.storage.from_(_BUCKET).remove([test_name])
            except Exception:
                pass
        except Exception as e:
            diagnostico["tests"]["upload_probe"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    except Exception as e:
        diagnostico["error"] = f"{type(e).__name__}: {e}"
    return diagnostico


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
