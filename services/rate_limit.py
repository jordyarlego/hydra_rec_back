import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_WINDOW_SEC = int(os.getenv("RATE_LIMIT_REPORTS_SECONDS", "300"))


def can_report(ip_hash: str) -> bool:
    from services.supabase_client import get_service_client
    client = get_service_client()
    try:
        res = client.table("rate_limits").select("last_action").eq("ip_hash", ip_hash).execute()
        if not res.data:
            client.table("rate_limits").insert({"ip_hash": ip_hash}).execute()
            return True
        last = datetime.fromisoformat(res.data[0]["last_action"].replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        if elapsed < _WINDOW_SEC:
            return False
        client.table("rate_limits").update({
            "last_action": datetime.now(timezone.utc).isoformat(),
            "action_count": res.data[0].get("action_count", 1) + 1,
        }).eq("ip_hash", ip_hash).execute()
        return True
    except Exception as e:
        logger.warning(f"Rate limit check failed: {e}")
        return True  # fail open — não bloqueia se Supabase estiver lento
