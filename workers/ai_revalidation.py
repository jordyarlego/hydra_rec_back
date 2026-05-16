from __future__ import annotations

import asyncio
import logging

from services.ai_validator import persist_validation

logger = logging.getLogger(__name__)


async def run_once(limit: int = 10) -> int:
    from services.supabase_client import get_service_client
    client = get_service_client()
    try:
        res = (client.table("reports")
               .select("*")
               .is_("ai_validation_score", "null")
               .limit(limit)
               .execute())
    except Exception as e:
        logger.debug("ai_revalidation fetch skipped: %s", e)
        return 0

    count = 0
    for report in res.data or []:
        try:
            if report.get("weather_snapshot_id"):
                ws = client.table("weather_snapshots").select("*").eq("id", report["weather_snapshot_id"]).execute()
                report["weather"] = (ws.data or [None])[0]
            await persist_validation(report["id"], report)
            count += 1
        except Exception as e:
            logger.warning("ai revalidation failed for %s: %s", report.get("id"), e)
    return count


async def start(interval_s: int = 60):
    while True:
        await run_once()
        await asyncio.sleep(interval_s)
