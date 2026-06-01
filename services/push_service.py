import os
import json
import logging
import asyncio

logger = logging.getLogger(__name__)

VAPID_PUBLIC_KEY  = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_EMAIL       = os.getenv("VAPID_EMAIL", "mailto:hydrarec@example.com")

# In-memory fallback: persiste enquanto o server está up quando Supabase não está configurado.
_subscriptions: list[dict] = []


def _get_supabase():
    try:
        from supabase import create_client
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_SERVICE_KEY", "")
        if url and key:
            return create_client(url, key)
    except Exception:
        pass
    return None


def _to_webpush_subscription(row: dict) -> dict:
    return {
        "endpoint": row["endpoint"],
        "keys": {
            "p256dh": row["p256dh"],
            "auth": row["auth"],
        },
    }


async def save_subscription(sub: dict, ip_hash: str = "unknown") -> None:
    endpoint = sub.get("endpoint", "")
    keys = sub.get("keys") or {}
    p256dh = keys.get("p256dh", "")
    auth = keys.get("auth", "")
    if not endpoint or not p256dh or not auth:
        logger.warning("push save ignored: subscription incompleta")
        return

    if not any(s.get("endpoint") == endpoint for s in _subscriptions):
        _subscriptions.append(sub)
    db = _get_supabase()
    if db:
        try:
            row = {
                "ip_hash": ip_hash,
                "endpoint": endpoint,
                "p256dh": p256dh,
                "auth": auth,
            }
            if sub.get("lat") is not None and sub.get("lon") is not None:
                row["lat"] = sub.get("lat")
                row["lon"] = sub.get("lon")
            await asyncio.to_thread(
                lambda: db.table("push_subscriptions")
                    .upsert(row, on_conflict="endpoint")
                    .execute()
            )
        except Exception as e:
            logger.warning(f"push save supabase: {e}")


async def remove_subscription(endpoint: str) -> None:
    global _subscriptions
    _subscriptions = [s for s in _subscriptions if s.get("endpoint") != endpoint]
    db = _get_supabase()
    if db:
        try:
            await asyncio.to_thread(
                lambda: db.table("push_subscriptions")
                    .delete().eq("endpoint", endpoint).execute()
            )
        except Exception as e:
            logger.warning(f"push remove supabase: {e}")


async def load_subscriptions() -> list[dict]:
    db = _get_supabase()
    if db:
        try:
            rows = await asyncio.to_thread(
                lambda: db.table("push_subscriptions")
                    .select("endpoint,p256dh,auth,bairro,min_severity")
                    .execute()
            )
            return [_to_webpush_subscription(r) for r in (rows.data or []) if r.get("endpoint") and r.get("p256dh") and r.get("auth")]
        except Exception as e:
            logger.warning(f"push load supabase: {e}")
    return list(_subscriptions)


async def load_subscriptions_with_location() -> list[dict]:
    db = _get_supabase()
    if db:
        try:
            rows = await asyncio.to_thread(
                lambda: db.table("push_subscriptions")
                    .select("endpoint,p256dh,auth,lat,lon")
                    .not_.is_("lat", "null")
                    .not_.is_("lon", "null")
                    .execute()
            )
            items = []
            for row in rows.data or []:
                if row.get("endpoint") and row.get("p256dh") and row.get("auth"):
                    item = _to_webpush_subscription(row)
                    item["lat"] = row.get("lat")
                    item["lon"] = row.get("lon")
                    items.append(item)
            return items
        except Exception as e:
            logger.warning(f"push load localized supabase: {e}")
    return [s for s in _subscriptions if s.get("lat") is not None and s.get("lon") is not None]


def _send_one(sub: dict, payload: dict) -> bool:
    if not VAPID_PRIVATE_KEY:
        return False
    try:
        from pywebpush import webpush
        webpush(
            subscription_info=sub,
            data=json.dumps(payload),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_EMAIL},
        )
        return True
    except Exception as e:
        logger.warning(f"push send: {e}")
        return False


async def send_to_endpoint(endpoint: str, payload: dict) -> bool:
    """Manda push pra um endpoint específico (não broadcast).

    Busca os keys (p256dh/auth) da subscription salva e dispara.
    Usado pelo worker pra notificar dono de report sobre mudança no ticket.
    """
    if not VAPID_PRIVATE_KEY or not endpoint:
        return False
    db = _get_supabase()
    if not db:
        return False
    try:
        res = await asyncio.to_thread(
            lambda: db.table("push_subscriptions")
                .select("endpoint,p256dh,auth")
                .eq("endpoint", endpoint)
                .limit(1)
                .execute()
        )
        rows = res.data or []
        if not rows:
            return False
        sub = _to_webpush_subscription(rows[0])
        return await asyncio.to_thread(_send_one, sub, payload)
    except Exception as e:
        logger.warning(f"send_to_endpoint: {e}")
        return False


async def broadcast_alert(bairro: str, score: int, nivel: str) -> int:
    if not VAPID_PRIVATE_KEY:
        return 0
    payload = {
        "title": f"HydraRec — {bairro}",
        "body":  f"Risco {nivel} detectado: {score}/100. Verifique o app.",
        "url":   "/",
    }
    subs = await load_subscriptions()
    sent = 0
    for sub in subs:
        if await asyncio.to_thread(_send_one, sub, payload):
            sent += 1
    return sent


async def notify_nearby_validation(report: dict, radius_m: int = 2000) -> int:
    if not VAPID_PRIVATE_KEY:
        return 0

    lat = report.get("lat")
    lon = report.get("lon")
    if lat is None or lon is None:
        return 0

    from services.report_validation import filter_nearby_subscriptions

    subs = filter_nearby_subscriptions(
        await load_subscriptions_with_location(),
        float(lat),
        float(lon),
        radius_m=radius_m,
    )
    payload = {
        "title": "HydraRec — valide um report próximo",
        "body": f"{report.get('bairro') or 'Recife'}: alguém reportou {report.get('type') or 'ocorrência'}.",
        "url": f"/?report={report.get('id')}",
    }
    sent = 0
    for sub in subs:
        if await asyncio.to_thread(_send_one, sub, payload):
            sent += 1
    return sent
