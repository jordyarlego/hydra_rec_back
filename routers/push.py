import os
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from services.push_service import broadcast_alert, save_subscription, remove_subscription, load_subscriptions
from services.security import hash_ip

router = APIRouter()


class PushSub(BaseModel):
    endpoint: str
    keys: dict
    expirationTime: float | None = None
    lat: float | None = None
    lon: float | None = None


@router.get("/api/push/vapid-public-key")
async def vapid_key():
    # Strip removes whitespace/newlines acidentais do .env
    key = os.getenv("VAPID_PUBLIC_KEY", "").strip()

    # Valida o formato antes de servir — uma key truncada gera
    # "applicationServerKey is not valid" no PushManager do browser,
    # erro confuso e difícil de rastrear sem essa validação.
    if key and len(key) != 87:
        return {
            "key": "",
            "error": (
                f"VAPID_PUBLIC_KEY tem {len(key)} caracteres, mas o padrão exige 87. "
                "Verifique se foi colada inteira no .env (sem quebra de linha)."
            ),
        }

    return {"key": key}


@router.post("/api/push/subscribe")
async def subscribe(sub: PushSub, request: Request):
    ip = request.headers.get(
        "X-Forwarded-For",
        request.client.host if request.client else "unknown",
    ).split(",")[0].strip()
    await save_subscription(sub.model_dump(), ip_hash=hash_ip(ip))
    return {"ok": True}


@router.delete("/api/push/subscribe")
async def unsubscribe(sub: PushSub):
    await remove_subscription(sub.endpoint)
    return {"ok": True}


@router.post("/api/push/test")
async def test_push(request: Request):
    token = os.getenv("PUSH_TEST_TOKEN", "")
    if not token or request.headers.get("X-Push-Test-Token") != token:
        raise HTTPException(status_code=403, detail="Push test desabilitado.")
    sent = await broadcast_alert("Teste", 70, "TESTE")
    return {"ok": True, "sent": sent}


@router.get("/api/push/status")
async def push_status(request: Request):
    """Quantas subscriptions estão salvas (debug). Protegido pelo mesmo token."""
    token = os.getenv("PUSH_TEST_TOKEN", "")
    if not token or request.headers.get("X-Push-Test-Token") != token:
        raise HTTPException(status_code=403, detail="Endpoint desabilitado.")
    subs = await load_subscriptions()
    return {
        "subscriptions_count": len(subs),
        "vapid_public_configured": bool(os.getenv("VAPID_PUBLIC_KEY", "").strip()),
        "vapid_private_configured": bool(os.getenv("VAPID_PRIVATE_KEY", "").strip()),
    }
