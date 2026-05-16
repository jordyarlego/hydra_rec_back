"""
Worker que cuida do ciclo de vida dos chamados (tickets).

Roda a cada N segundos (default 1h) e:
  • Move tickets resolvidos há mais de AUTO_CLOSE_DAYS dias → 'fechado'
  • Marca como overdue (log) tickets cujo SLA estourou
  • (Futuro) Dispara notificação push pro cidadão quando ticket muda
    de estado, se ele estava inscrito no report.

Configurável via env:
  AUTO_CLOSE_DAYS=7        # padrão: fecha resolvidos com 7+ dias
  TICKET_WORKER_INTERVAL_S=3600
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


def _auto_close_days() -> int:
    try:
        return max(1, int(os.getenv("AUTO_CLOSE_DAYS", "7")))
    except ValueError:
        return 7


async def auto_close_old_resolved() -> dict:
    """Fecha tickets resolvidos há mais de AUTO_CLOSE_DAYS dias. Retorna stats."""
    days = _auto_close_days()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        from services.supabase_client import get_service_client
        client = get_service_client()
    except Exception as e:
        logger.warning("auto_close: supabase indisponível: %s", e)
        return {"closed": 0, "error": str(e)}

    closed_count = 0
    try:
        # Usa kanban_state se existir (V4); senão usa status legacy
        # Probe rápido pra ver se coluna existe
        use_kanban = True
        try:
            client.table("tickets").select("kanban_state").limit(1).execute()
        except Exception:
            use_kanban = False

        if use_kanban:
            res = (
                client.table("tickets")
                .select("id,updated_at,kanban_state")
                .eq("kanban_state", "resolvido")
                .lte("updated_at", cutoff)
                .limit(200)
                .execute()
            )
            for t in res.data or []:
                client.table("tickets").update({"kanban_state": "fechado"}).eq("id", t["id"]).execute()
                closed_count += 1
        else:
            res = (
                client.table("tickets")
                .select("id,updated_at,status")
                .eq("status", "resolvido")
                .lte("updated_at", cutoff)
                .limit(200)
                .execute()
            )
            for t in res.data or []:
                client.table("tickets").update({"status": "fechado"}).eq("id", t["id"]).execute()
                closed_count += 1

        if closed_count:
            # Auditoria do batch
            client.table("admin_audit").insert({
                "user_id": None,
                "action": "auto_close_batch",
                "target_table": "tickets",
                "target_id": None,
                "diff": {"closed": closed_count, "older_than_days": days},
            }).execute()

    except Exception as e:
        logger.warning("auto_close: query/update falhou: %s", e)
        return {"closed": closed_count, "error": str(e)}

    return {"closed": closed_count, "older_than_days": days}


async def check_overdue_sla() -> dict:
    """Loga (e futuramente notifica) tickets com sla_deadline estourado e não fechados."""
    try:
        from services.supabase_client import get_service_client
        client = get_service_client()
    except Exception as e:
        return {"overdue": 0, "error": str(e)}

    now = datetime.now(timezone.utc).isoformat()
    overdue = 0
    try:
        res = (
            client.table("tickets")
            .select("id,sla_deadline,kanban_state,priority,bairro,type")
            .lte("sla_deadline", now)
            .neq("kanban_state", "fechado")
            .neq("kanban_state", "resolvido")
            .limit(100)
            .execute()
        )
        overdue = len(res.data or [])
        if overdue:
            logger.info(f"[sla] {overdue} ticket(s) com SLA estourado.")
    except Exception as e:
        logger.warning("check_overdue: %s", e)
        return {"overdue": 0, "error": str(e)}
    return {"overdue": overdue}


async def start(interval_s: int = None) -> None:
    """Loop principal — roda enquanto o app vive."""
    interval = interval_s or int(os.getenv("TICKET_WORKER_INTERVAL_S", "3600"))
    logger.info(f"[ticket_lifecycle] start, intervalo={interval}s, auto_close_days={_auto_close_days()}")
    while True:
        try:
            close_res = await auto_close_old_resolved()
            sla_res = await check_overdue_sla()
            logger.info(f"[ticket_lifecycle] tick: close={close_res} sla={sla_res}")
        except Exception as e:
            logger.exception("[ticket_lifecycle] tick error: %s", e)
        await asyncio.sleep(interval)
