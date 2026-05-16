"""Valida JWT Supabase e exige role=admin. Usado nos endpoints /api/admin/*."""
import os
import logging
import jwt
import httpx
from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)
_bearer = HTTPBearer(auto_error=False)


async def require_admin(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="Token de autenticação ausente")
    token = credentials.credentials
    secret = os.getenv("SUPABASE_JWT_SECRET", "")
    if not secret:
        return await _require_admin_via_supabase(token)
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="authenticated",
            options={"verify_exp": True},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Token inválido: {e}")

    meta = payload.get("user_metadata") or payload.get("app_metadata") or {}
    if meta.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores")
    return payload


async def _require_admin_via_supabase(token: str) -> dict:
    """Fallback sem JWT secret: valida o access token no próprio Supabase Auth."""
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon_key = os.getenv("SUPABASE_KEY", "")
    if not url or not anon_key:
        raise HTTPException(status_code=500, detail="Configuração Supabase incompleta")

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.get(
                f"{url}/auth/v1/user",
                headers={
                    "apikey": anon_key,
                    "Authorization": f"Bearer {token}",
                },
            )
    except httpx.HTTPError as e:
        logger.warning("Supabase auth validation failed: %s", e)
        raise HTTPException(status_code=401, detail="Não foi possível validar sessão")

    if res.status_code == 401:
        raise HTTPException(status_code=401, detail="Token inválido")
    if not res.is_success:
        raise HTTPException(status_code=401, detail=f"Token inválido: HTTP {res.status_code}")

    user = res.json()
    meta = user.get("user_metadata") or user.get("app_metadata") or {}
    if meta.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores")
    return {"sub": user.get("id"), "user_metadata": user.get("user_metadata") or {}, "app_metadata": user.get("app_metadata") or {}}
