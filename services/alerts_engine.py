import logging
from datetime import datetime, timezone, timedelta
from services.supabase_client import get_service_client

logger = logging.getLogger(__name__)


def check_and_create_alerts(bairro: str) -> list[dict]:
    client = get_service_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    try:
        res = client.table("reports").select("id,type,severity").eq("bairro", bairro).eq("resolved", False).gte("created_at", cutoff).execute()
        reports = res.data or []
    except Exception as e:
        logger.warning(f"alerts_engine fetch failed: {e}")
        return []

    from collections import Counter
    counts = Counter(r["type"] for r in reports)
    created = []
    for tipo, cnt in counts.items():
        if cnt < 3:
            continue
        severity = "severo" if cnt >= 5 else "alto" if cnt >= 3 else "moderado"
        msg = f"{cnt} reports de {tipo} confirmados em {bairro} na última hora."
        expires = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        try:
            existing = client.table("alerts").select("id").eq("bairro", bairro).eq("type", tipo).eq("active", True).execute()
            if existing.data:
                continue
            alert = client.table("alerts").insert({
                "bairro": bairro, "type": tipo, "message": msg,
                "severity": severity,
                "triggered_by_report_ids": [r["id"] for r in reports if r["type"] == tipo],
                "expires_at": expires,
            }).execute()
            created.append(alert.data[0] if alert.data else {})
        except Exception as e:
            logger.warning(f"alert insert failed: {e}")
    return created
