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


_KANBAN_TO_USER_MSG = {
    "aberto":       ("Recebemos seu chamado",          "A prefeitura já está com seu report na fila."),
    "triagem":      ("Seu chamado entrou em análise",  "Estamos avaliando a melhor equipe pra atender."),
    "em_andamento": ("A prefeitura agiu no seu chamado","Equipe foi acionada pra resolver o ponto que você reportou."),
    "aguardando":   ("Seu chamado precisa de mais info","A equipe está aguardando dado externo pra prosseguir."),
    "resolvido":    ("Chamado resolvido!",             "A prefeitura marcou o ponto como atendido. Confere se ficou bom?"),
    "cancelado":    ("Chamado cancelado",              "A prefeitura encerrou esse chamado. Veja o histórico no app."),
    "fechado":      ("Chamado arquivado",              "Após o prazo de resolução foi arquivado automaticamente."),
}


async def notify_ticket_state_changes() -> dict:
    """Detecta tickets cujo kanban_state mudou desde último tick e notifica
    cidadãos inscritos (via report_push_subscriptions).

    Idempotente: usa coluna `tickets.last_pushed_state` pra evitar re-notificar
    o mesmo estado. Só dispara se `kanban_state != last_pushed_state`.
    """
    try:
        from services.supabase_client import get_service_client
        from services.push_service import send_to_endpoint
        client = get_service_client()
    except Exception as e:
        return {"notified": 0, "error": str(e)}

    notified = 0
    skipped = 0
    try:
        res = (
            client.table("tickets")
            .select("id,report_id,kanban_state,last_pushed_state,bairro,type")
            .not_.is_("report_id", "null")
            .not_.is_("kanban_state", "null")
            .limit(200)
            .execute()
        )
        for t in (res.data or []):
            current = t.get("kanban_state")
            last = t.get("last_pushed_state")
            if not current or current == last:
                continue

            title_body = _KANBAN_TO_USER_MSG.get(current)
            if not title_body:
                continue
            title, body = title_body

            subs_res = (
                client.table("report_push_subscriptions")
                .select("push_endpoint,last_notified_state")
                .eq("report_id", t["report_id"])
                .execute()
            )
            payload = {
                "title": title,
                "body":  body,
                "url":   f"/?report={t['report_id']}",
            }
            for sub in (subs_res.data or []):
                if sub.get("last_notified_state") == current:
                    skipped += 1
                    continue
                ok = await send_to_endpoint(sub["push_endpoint"], payload)
                if ok:
                    notified += 1
                    client.table("report_push_subscriptions").update(
                        {"last_notified_state": current}
                    ).eq("report_id", t["report_id"]).eq("push_endpoint", sub["push_endpoint"]).execute()

            client.table("tickets").update(
                {"last_pushed_state": current}
            ).eq("id", t["id"]).execute()

    except Exception as e:
        logger.warning("notify_ticket_state_changes: %s", e)
        return {"notified": notified, "error": str(e)}

    return {"notified": notified, "skipped": skipped}


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


async def start_push_notifier(interval_s: int = None) -> None:
    """Loop separado, cadência rápida (60s default) pra push de ticket.

    Não junta no start() porque auto_close/sla podem ser horários;
    push de ticket precisa chegar pro cidadão em <2 min.
    """
    interval = interval_s or int(os.getenv("PUSH_NOTIFIER_INTERVAL_S", "60"))
    logger.info(f"[push_notifier] start, intervalo={interval}s")
    while True:
        try:
            res = await notify_ticket_state_changes()
            if res.get("notified") or res.get("error"):
                logger.info(f"[push_notifier] tick: {res}")
        except Exception as e:
            logger.exception("[push_notifier] tick error: %s", e)
        await asyncio.sleep(interval)
