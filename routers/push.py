import os
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from services.push_service import broadcast_alert, save_subscription, remove_subscription
from services.security import hash_ip

router = APIRouter()


class PushSub(BaseModel):
    endpoint: str
    keys: dict
    expirationTime: float | None = None


@router.get("/api/push/vapid-public-key")
async def vapid_key():
    return {"key": os.getenv("VAPID_PUBLIC_KEY", "")}


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
