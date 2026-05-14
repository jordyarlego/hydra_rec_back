"""Worker que roda a cada 5 min verificando clusters de reports por bairro ativo."""
import asyncio
import logging
from data.bairros_coords import BAIRRO_COORDS
from services.alerts_engine import check_and_create_alerts

logger = logging.getLogger(__name__)


async def run_once():
    from services.supabase_client import get_service_client
    client = get_service_client()
    try:
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        res = client.table("reports").select("bairro").eq("resolved", False).gte("created_at", cutoff).execute()
        bairros_ativos = {r["bairro"] for r in (res.data or []) if r.get("bairro")}
    except Exception as e:
        logger.warning(f"cron_alerts fetch failed: {e}")
        return
    for bairro in bairros_ativos:
        check_and_create_alerts(bairro)
    logger.info(f"cron_alerts: verificados {len(bairros_ativos)} bairros")


async def start_cron(interval_sec: int = 300):
    while True:
        try:
            await run_once()
        except Exception as e:
            logger.error(f"cron_alerts error: {e}")
        await asyncio.sleep(interval_sec)
