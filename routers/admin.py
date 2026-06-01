"""
Endpoints /api/admin/* — todos protegidos por require_admin (JWT Supabase + role=admin).
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from services.auth_guard import require_admin
from services.dispatch_router import (
    suggest_org,
    list_orgs,
    auto_title,
    sla_deadline,
    find_duplicates,
    build_dispatch_email,
    org_contact,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = logging.getLogger(__name__)


def _db():
    from services.supabase_client import get_service_client
    return get_service_client()


# ── Official Data Hub ──────────────────────────────────────────────────────

@router.get("/official-data/status")
async def official_data_status(_admin=Depends(require_admin)):
    """Status das últimas importações por fonte."""
    from services.official_importer import get_import_status
    return {"sources": await get_import_status()}


_import_state: dict = {"running": False, "started_at": None, "result": None, "sources": None}


@router.post("/official-data/import")
async def trigger_official_import(
    sources: Optional[list[str]] = Query(default=None),
    _admin=Depends(require_admin),
):
    """
    Dispara importação em BACKGROUND e retorna na hora.
    Status: GET /api/admin/official-data/import-status.
    """
    import asyncio
    import time

    if _import_state["running"]:
        return {
            "status": "already_running",
            "started_at": _import_state["started_at"],
            "sources": _import_state["sources"],
        }

    async def _run():
        from services.official_importer import import_all
        try:
            res = await import_all(sources or None)
            _import_state["result"] = res
        except Exception as e:
            _import_state["result"] = {"error": str(e)}
        finally:
            _import_state["running"] = False

    _import_state["running"] = True
    _import_state["started_at"] = time.time()
    _import_state["sources"] = sources or "all"
    _import_state["result"] = None
    asyncio.create_task(_run())

    return {"status": "started", "started_at": _import_state["started_at"], "sources": _import_state["sources"]}


@router.get("/official-data/import-status")
async def official_import_status(_admin=Depends(require_admin)):
    """Status do import em andamento (ou último resultado)."""
    import time
    elapsed = None
    if _import_state["started_at"]:
        elapsed = round(time.time() - _import_state["started_at"], 1)
    return {
        "running":    _import_state["running"],
        "started_at": _import_state["started_at"],
        "elapsed_s":  elapsed,
        "sources":    _import_state["sources"],
        "result":     _import_state["result"],
    }


@router.post("/official-data/import-seed")
async def trigger_official_seed_import(_admin=Depends(require_admin)):
    """
    Importa um dataset estático pré-curado (~120 registros).
    Roda em primeiro plano (rápido, <1s) — não bloqueia.
    Usado quando o Portal de Dados Abertos do Recife falha (timeout,
    schema mudou, etc) ou quando o admin quer popular pra MVP/demo
    sem depender de fonte externa.
    """
    try:
        from services.official_importer import import_from_seed
        result = await import_from_seed()
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.exception("import_from_seed failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/official-data/coverage")
async def official_data_coverage(_admin=Depends(require_admin)):
    """
    Mostra o que está mapeado AGORA no banco — pra admin saber transparente
    quais bases foram importadas com sucesso e quais bairros estão cobertos.

    Esse painel evita a frustração de "Importar agora" sem saber se deu certo.
    """
    out: dict = {
        "neighborhoods": {"count": 0, "sample": [], "rpas": []},
        "official_requests": {"total": 0, "by_category": [], "by_neighborhood_top10": [], "last_opened_at": None},
        "roads": {"count": 0},
        "import_logs": [],
    }

    db = _db()

    # 1. Bairros oficiais (point-in-polygon usado pra detecção)
    try:
        res = db.table("official_neighborhoods").select("name,rpa").limit(200).execute()
        rows = res.data or []
        out["neighborhoods"]["count"] = len(rows)
        out["neighborhoods"]["sample"] = sorted([r["name"] for r in rows[:20] if r.get("name")])
        rpas = sorted({r.get("rpa") for r in rows if r.get("rpa")})
        out["neighborhoods"]["rpas"] = list(rpas)
    except Exception as e:
        out["neighborhoods"]["error"] = str(e)

    # 2. Chamados oficiais importados (EMLURB 156, Defesa Civil)
    try:
        res = db.table("official_service_requests").select(
            "id,category,neighborhood,opened_at"
        ).order("opened_at", desc=True).limit(2000).execute()
        rows = res.data or []
        out["official_requests"]["total"] = len(rows)
        if rows:
            out["official_requests"]["last_opened_at"] = rows[0].get("opened_at")
        # Agregações
        by_cat: dict = {}
        by_nb: dict = {}
        for r in rows:
            c = r.get("category") or "outro"
            n = r.get("neighborhood") or "Desconhecido"
            by_cat[c] = by_cat.get(c, 0) + 1
            by_nb[n] = by_nb.get(n, 0) + 1
        out["official_requests"]["by_category"] = sorted(
            [{"category": k, "count": v} for k, v in by_cat.items()],
            key=lambda x: -x["count"],
        )[:10]
        out["official_requests"]["by_neighborhood_top10"] = sorted(
            [{"neighborhood": k, "count": v} for k, v in by_nb.items()],
            key=lambda x: -x["count"],
        )[:10]
    except Exception as e:
        out["official_requests"]["error"] = str(e)

    # 3. Vias (logradouros)
    try:
        res = db.table("official_roads").select("id", count="exact").limit(1).execute()
        out["roads"]["count"] = res.count or 0
    except Exception as e:
        out["roads"]["error"] = str(e)

    # 4. Logs das últimas importações
    try:
        res = db.table("official_import_log").select(
            "source,started_at,finished_at,records_ok,records_err,duration_s"
        ).order("started_at", desc=True).limit(10).execute()
        out["import_logs"] = res.data or []
    except Exception as e:
        out["import_logs_error"] = str(e)

    return out


@router.get("/official-data/service-requests")
async def list_service_requests(
    category: Optional[str] = Query(None),
    neighborhood: Optional[str] = Query(None),
    rpa: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    _admin=Depends(require_admin),
):
    """Lista chamados oficiais importados com filtros."""
    q = _db().table("official_service_requests").select(
        "id,external_id,source,agency,service_type,category,status,"
        "neighborhood,rpa,street_name,lat,lon,opened_at,closed_at"
    )
    if category:
        q = q.eq("category", category)
    if neighborhood:
        q = q.ilike("neighborhood", f"%{neighborhood}%")
    if rpa:
        q = q.eq("rpa", rpa)
    q = q.order("opened_at", desc=True).range(offset, offset + limit - 1)
    try:
        res = q.execute()
        return {"data": res.data or [], "count": len(res.data or [])}
    except Exception as e:
        logger.error(f"service_requests query: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Reports admin ──────────────────────────────────────────────────────────

@router.get("/reports/counts-by-bucket")
async def reports_counts_by_bucket(_admin=Depends(require_admin)):
    """
    Contagem de reports por bucket (filtrado | revisar | auto_validado | sem_bucket).
    Usado pelos 3 cards-bucket no topo da AdminReportsTable.
    """
    try:
        res = _db().table("reports").select("bucket,status").limit(5000).execute()
        rows = res.data or []
        counts = {"filtrado": 0, "revisar": 0, "auto_validado": 0, "sem_bucket": 0}
        for r in rows:
            # Ignora reports já resolvidos/rejeitados do count visual
            if r.get("status") in ("resolved", "rejected"):
                continue
            b = r.get("bucket") or "sem_bucket"
            counts[b] = counts.get(b, 0) + 1
        return counts
    except Exception as e:
        # Coluna bucket pode não existir se V4 não foi aplicada — devolve zerado
        logger.warning("counts-by-bucket fallback: %s", e)
        return {"filtrado": 0, "revisar": 0, "auto_validado": 0, "sem_bucket": 0, "v4_missing": True}


@router.get("/dispatch/orgs")
async def admin_dispatch_orgs(_admin=Depends(require_admin)):
    """Lista de órgãos destino pra dropdown do form de chamado."""
    return {"data": list_orgs()}


@router.get("/reports")
async def list_reports(
    status: Optional[str] = Query(None),
    tipo: Optional[str] = Query(None),
    bairro: Optional[str] = Query(None),
    bucket: Optional[str] = Query(None, pattern="^(filtrado|revisar|auto_validado|sem_bucket)?$"),
    q: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    limit: int = Query(15, le=100),
    offset: int = Query(0),
    _admin=Depends(require_admin),
):
    # Tenta com bucket; cai pra sem-bucket no select se coluna não existe
    select_with_bucket = (
        "id,type,severity,lat,lon,bairro,description,"
        "likes_up,likes_down,status,ai_validation_score,"
        "photo_url,confirmed_count,created_at,bucket"
    )
    select_without_bucket = (
        "id,type,severity,lat,lon,bairro,description,"
        "likes_up,likes_down,status,ai_validation_score,"
        "photo_url,confirmed_count,created_at"
    )

    use_bucket_col = True
    try:
        # Probe rápido pra ver se coluna existe
        _db().table("reports").select("bucket").limit(1).execute()
    except Exception:
        use_bucket_col = False

    query = _db().table("reports").select(select_with_bucket if use_bucket_col else select_without_bucket)
    if status:
        query = query.eq("status", status)
    if tipo:
        query = query.eq("type", tipo)
    if bairro:
        query = query.ilike("bairro", f"%{bairro}%")
    if bucket and use_bucket_col:
        if bucket == "sem_bucket":
            query = query.is_("bucket", "null")
        else:
            query = query.eq("bucket", bucket)
    if q:
        query = query.ilike("description", f"%{q}%")
    if from_date:
        query = query.gte("created_at", from_date)
    if to_date:
        query = query.lte("created_at", to_date)
    query = query.order("created_at", desc=True).range(offset, offset + limit - 1)
    try:
        res = query.execute()
        rows = res.data or []
        from services.priority_engine import batch_prioritize

        report_ids = [r["id"] for r in rows if r.get("id")]
        crossings = {}
        if report_ids:
            try:
                cr = _db().table("report_official_crossings").select("*").in_("report_id", report_ids).execute()
                crossings = {r["report_id"]: r for r in cr.data or []}
            except Exception:
                crossings = {}

        prioritized = batch_prioritize([
            {
                **r,
                "tipo": r.get("type"),
                "severidade": r.get("severity"),
            }
            for r in rows
        ], crossings=crossings)
        return {"data": prioritized, "count": len(prioritized), "limit": limit, "offset": offset}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/analytics")
async def admin_analytics(
    window_hours: int = Query(24, ge=1, le=168),
    narrate: bool = Query(False),
    _admin=Depends(require_admin),
):
    """Tendências + recomendações (regras) + top prioridades. Gemini só narra."""
    from datetime import datetime, timezone, timedelta
    from services.analytics import aggregate_trends, build_recommendations
    from services.priority_engine import batch_prioritize

    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=window_hours * 2)).isoformat()
    fields = (
        "id,type,severity,bairro,created_at,likes_up,likes_down,"
        "status,ai_validation_score,photo_url,photo_ai_severity_hint"
    )
    try:
        res = (
            _db().table("reports").select(fields)
            .gte("created_at", since)
            .order("created_at", desc=True)
            .execute()
        )
        rows = res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    trends = aggregate_trends(rows, now, window_hours)
    recommendations = build_recommendations(trends, now)

    top = batch_prioritize([
        {**r, "tipo": r.get("type"), "severidade": r.get("severity")}
        for r in rows
    ])[:10]

    narration = None
    if narrate:
        from services.ai_recommender import narrate_recommendations
        text, model = await narrate_recommendations(recommendations)
        narration = {"text": text, "model": model}

    return {
        "window_hours": window_hours,
        "trends": trends,
        "recommendations": recommendations,
        "top_priorities": top,
        "narration": narration,
    }


@router.get("/reports/{report_id}/official-crossing")
async def get_official_crossing(report_id: str, _admin=Depends(require_admin)):
    """Cruzamento oficial de um report específico."""
    try:
        res = _db().table("report_official_crossings").select("*").eq(
            "report_id", report_id
        ).execute()
        if not res.data:
            from services.geo_cross import cross_report_with_official_data
            crossing = await cross_report_with_official_data(report_id)
            if not crossing:
                return {
                    "available": False,
                    "reason": "Cruzamento ainda indisponível. Importe dados oficiais ou tente novamente após novos reports.",
                    "priority_result": None,
                }
        else:
            crossing = res.data[0]
        crossing["available"] = True
        try:
            cov = _db().table("official_service_requests").select("source", count="exact").limit(1).execute()
            crossing["official_request_count"] = cov.count or 0
            src = _db().table("official_service_requests").select("source").limit(500).execute()
            crossing["official_sources"] = sorted({r.get("source") for r in (src.data or []) if r.get("source")})
        except Exception:
            crossing["official_request_count"] = None
            crossing["official_sources"] = []

        # Hidrata priority engine
        report_res = _db().table("reports").select(
            "type,severity,likes_up,likes_down,ai_validation_score,status"
        ).eq("id", report_id).execute()
        weather_res = None
        report_row = (report_res.data or [None])[0]

        if report_row:
            from services.priority_engine import calculate_priority
            priority = calculate_priority(
                {
                    "tipo": report_row.get("type"),
                    "severidade": report_row.get("severity"),
                    "likes_up": report_row.get("likes_up", 0),
                    "likes_down": report_row.get("likes_down", 0),
                    "ai_validation_score": report_row.get("ai_validation_score"),
                    "status": report_row.get("status"),
                },
                official_crossing=crossing,
            )
            crossing["priority_result"] = priority

        return crossing
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/reports/{report_id}")
async def get_report_detail(report_id: str, _admin=Depends(require_admin)):
    """Detalhe completo de report + weather snapshot + audit."""
    try:
        res = _db().table("reports").select("*").eq("id", report_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Report não encontrado.")
        report = res.data[0]

        weather = None
        if report.get("weather_snapshot_id"):
            try:
                ws = _db().table("weather_snapshots").select("*").eq(
                    "id", report["weather_snapshot_id"]
                ).execute()
                weather = (ws.data or [None])[0]
            except Exception:
                weather = None

        crossing = None
        try:
            cr = _db().table("report_official_crossings").select("*").eq(
                "report_id", report_id
            ).execute()
            crossing = (cr.data or [None])[0]
        except Exception:
            crossing = None

        try:
            from services.priority_engine import calculate_priority
            report["priority_result"] = calculate_priority(
                {
                    **report,
                    "tipo": report.get("type"),
                    "severidade": report.get("severity"),
                },
                weather_snapshot=weather,
                official_crossing=crossing,
            )
        except Exception:
            report["priority_result"] = None

        audit = []
        try:
            ar = _db().table("admin_audit").select("*").eq(
                "target_id", report_id
            ).order("created_at", desc=True).limit(20).execute()
            audit = ar.data or []
        except Exception:
            audit = []

        report["weather"] = weather
        report["audit"] = audit
        return report
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/reports/{report_id}")
async def update_report(
    report_id: str,
    payload: dict,
    admin=Depends(require_admin),
):
    """Muda status, adiciona notas, registra motivo de rejeição. Audita a ação."""
    allowed = {"status", "ai_validation_notes", "bucket", "rejection_reason"}
    update = {k: v for k, v in payload.items() if k in allowed}
    if not update:
        raise HTTPException(status_code=400, detail="Nenhum campo editável fornecido.")

    # Se foi rejeitado, exige motivo
    if update.get("status") == "rejected" and "rejection_reason" not in update:
        raise HTTPException(status_code=400, detail="Rejeição exige rejection_reason.")
    if update.get("rejection_reason") and update["rejection_reason"] not in (
        "duplicado", "foto_invalida", "fora_escopo", "trote"
    ):
        raise HTTPException(status_code=400, detail="rejection_reason inválido.")

    try:
        # Tenta com todas as colunas; se V4 não aplicada, faz update parcial
        try:
            _db().table("reports").update(update).eq("id", report_id).execute()
        except Exception as col_err:
            logger.warning("update_report V4 cols missing, fallback: %s", col_err)
            safe_update = {k: v for k, v in update.items() if k not in ("bucket", "rejection_reason")}
            if safe_update:
                _db().table("reports").update(safe_update).eq("id", report_id).execute()

        # Audit log
        _db().table("admin_audit").insert({
            "action": "patch_report",
            "target_table": "reports",
            "target_id": report_id,
            "diff": update,
        }).execute()

        return {"ok": True, "updated": update}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/reports/{report_id}")
async def delete_report(report_id: str, admin=Depends(require_admin)):
    """Soft-delete: marca report como rejected e audita."""
    try:
        _db().table("reports").update({"status": "rejected"}).eq("id", report_id).execute()
        _db().table("admin_audit").insert({
            "user_id": admin.get("sub"),
            "action": "delete_report",
            "target_table": "reports",
            "target_id": report_id,
            "diff": {"status": "rejected"},
        }).execute()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/reports/{report_id}/address")
async def get_report_address(report_id: str, _admin=Depends(require_admin)):
    """
    Resolve endereço humano (rua, número, bairro) + pontos de referência
    próximos para o report. Cache em memória 24h. Falha gracioso → null.
    """
    try:
        res = _db().table("reports").select("id,lat,lon,bairro").eq("id", report_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Report não encontrado.")
        r = res.data[0]
        lat, lon = r.get("lat"), r.get("lon")
        if lat is None or lon is None:
            return {"address": None, "landmarks": [], "reason": "Sem coordenadas no report."}

        from services.geocoding import reverse_geocode, nearby_landmarks
        # roda em paralelo
        import asyncio as _asyncio
        addr, landmarks = await _asyncio.gather(
            reverse_geocode(lat, lon),
            nearby_landmarks(lat, lon, radius_m=200),
            return_exceptions=True,
        )
        if isinstance(addr, Exception):
            addr = {"source": "fallback", "full_address": None}
        if isinstance(landmarks, Exception):
            landmarks = []
        return {"address": addr, "landmarks": landmarks}
    except HTTPException:
        raise
    except Exception as e:
        return {"address": None, "landmarks": [], "error": str(e)}


@router.get("/reports/{report_id}/duplicates")
async def get_duplicates(report_id: str, _admin=Depends(require_admin)):
    """Retorna candidatos de duplicata (mesma categoria, raio 100m, últimas 24h)."""
    try:
        res = _db().table("reports").select("id,type,lat,lon,bairro,created_at").eq("id", report_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Report não encontrado.")
        candidates = await find_duplicates(res.data[0])
        return {"data": candidates, "count": len(candidates)}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("get_duplicates failed: %s", e)
        return {"data": [], "count": 0, "error": str(e)}


@router.post("/reports/{report_id}/aggregate-to/{ticket_id}")
async def aggregate_report_to_ticket(
    report_id: str,
    ticket_id: str,
    admin=Depends(require_admin),
):
    """
    Agrega este report a um chamado existente (em vez de criar chamado novo).
    Marca o report como validated, vincula ao ticket, e adiciona ao
    array aggregated_from do ticket.
    """
    try:
        ticket_res = _db().table("tickets").select("id,aggregated_from").eq("id", ticket_id).execute()
        if not ticket_res.data:
            raise HTTPException(status_code=404, detail="Ticket não encontrado.")

        # Append no array aggregated_from (tolerante a coluna ausente)
        existing = ticket_res.data[0].get("aggregated_from") or []
        if report_id not in existing:
            existing = list(existing) + [report_id]
        try:
            _db().table("tickets").update({"aggregated_from": existing}).eq("id", ticket_id).execute()
        except Exception as col_err:
            logger.warning("aggregated_from column missing (V4 not applied?): %s", col_err)

        _db().table("reports").update({
            "ticket_id": ticket_id,
            "status": "validated",
        }).eq("id", report_id).execute()

        _db().table("admin_audit").insert({
            "user_id": admin.get("sub"),
            "action": "aggregate_to_ticket",
            "target_table": "reports",
            "target_id": report_id,
            "diff": {"ticket_id": ticket_id},
        }).execute()
        return {"ok": True, "ticket_id": ticket_id, "report_id": report_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reports/batch-approve")
async def batch_approve_reports(payload: dict, admin=Depends(require_admin)):
    """
    Aprova em lote vários reports do bucket auto_validado.
    Cria 1 ticket por report (com org + título + SLA auto-preenchidos)
    e registra na auditoria.

    Body: {"report_ids": ["uuid1", "uuid2", ...]}
    """
    report_ids = payload.get("report_ids") or []
    if not isinstance(report_ids, list) or not report_ids:
        raise HTTPException(status_code=400, detail="report_ids deve ser lista não-vazia.")
    if len(report_ids) > 100:
        raise HTTPException(status_code=400, detail="Máximo 100 reports por lote.")

    created = []
    errors = []
    for rid in report_ids:
        try:
            r_res = _db().table("reports").select(
                "id,bairro,type,severity,ticket_id,lat,lon"
            ).eq("id", rid).execute()
            if not r_res.data:
                errors.append({"id": rid, "error": "not_found"})
                continue
            report = r_res.data[0]
            if report.get("ticket_id"):
                # Já tinha ticket: pula
                continue

            geo = {}
            try:
                cr = _db().table("report_official_crossings").select("*").eq("report_id", rid).execute()
                if cr.data:
                    geo = cr.data[0]
            except Exception:
                pass

            tipo = report.get("type") or "outro"
            org = suggest_org(tipo)
            title = auto_title({**report, "tipo": tipo}, geo)
            priority = "media"  # batch-approve usa media por default; admin individual pode customizar
            row = {
                "report_id": rid,
                "bairro": report.get("bairro"),
                "type": tipo,
                "priority": priority,
                "status": "aberto",
                "notes": title,
                "created_by": admin.get("sub"),
            }
            # Campos V4 — tolerante se coluna não existir
            try:
                row_v4 = {**row, "assigned_org": org, "kanban_state": "aberto",
                          "sla_deadline": sla_deadline(priority).isoformat()}
                ticket = _db().table("tickets").insert(row_v4).execute()
            except Exception:
                ticket = _db().table("tickets").insert(row).execute()

            tid = ticket.data[0]["id"]
            _db().table("reports").update({
                "ticket_id": tid,
                "status": "validated",
            }).eq("id", rid).execute()
            created.append({"report_id": rid, "ticket_id": tid, "title": title, "org": org})
        except Exception as e:
            errors.append({"id": rid, "error": str(e)})

    _db().table("admin_audit").insert({
        "user_id": admin.get("sub"),
        "action": "batch_approve",
        "target_table": "reports",
        "target_id": None,
        "diff": {"approved": len(created), "errors": len(errors), "report_ids": report_ids},
    }).execute()

    return {"ok": True, "created": created, "errors": errors, "approved_count": len(created)}


@router.post("/reports/{report_id}/ticket")
async def create_ticket_from_report(
    report_id: str,
    payload: dict,
    admin=Depends(require_admin),
):
    """
    Cria ticket administrativo a partir de um report.
    Body opcional: {priority, assigned_to, external_ref, notes, assigned_org, title}.
    Se assigned_org ou title não vierem, são auto-gerados.
    """
    try:
        report_res = _db().table("reports").select(
            "id,bairro,type,severity,ticket_id,lat,lon"
        ).eq("id", report_id).execute()
        if not report_res.data:
            raise HTTPException(status_code=404, detail="Report não encontrado.")
        report = report_res.data[0]
        if report.get("ticket_id"):
            existing = _db().table("tickets").select("*").eq("id", report["ticket_id"]).execute()
            if existing.data:
                return existing.data[0]

        # Geo crossing pra auto_title
        geo = {}
        try:
            cr = _db().table("report_official_crossings").select("*").eq("report_id", report_id).execute()
            if cr.data:
                geo = cr.data[0]
        except Exception:
            pass

        tipo = report.get("type") or "outro"
        priority = (payload.get("priority") or "media").lower()
        assigned_org = payload.get("assigned_org") or suggest_org(tipo)
        title = payload.get("title") or auto_title({**report, "tipo": tipo}, geo)
        notes = payload.get("notes") or title

        row = {
            "report_id": report_id,
            "bairro": report.get("bairro"),
            "type": tipo,
            "priority": priority,
            "status": "aberto",
            "assigned_to": payload.get("assigned_to"),
            "external_ref": payload.get("external_ref"),
            "notes": notes,
            "created_by": admin.get("sub"),
        }
        # Insere com campos V4 (assigned_org, kanban_state, sla_deadline) — fallback se coluna não existe
        try:
            row_v4 = {
                **row,
                "assigned_org": assigned_org,
                "kanban_state": "aberto",
                "sla_deadline": sla_deadline(priority).isoformat(),
            }
            ticket = _db().table("tickets").insert(row_v4).execute()
        except Exception as col_err:
            logger.warning("V4 columns missing on ticket insert, fallback: %s", col_err)
            ticket = _db().table("tickets").insert(row).execute()

        _db().table("reports").update({
            "ticket_id": ticket.data[0]["id"],
            "status": "validated",
        }).eq("id", report_id).execute()
        _db().table("admin_audit").insert({
            "user_id": admin.get("sub"),
            "action": "create_ticket",
            "target_table": "reports",
            "target_id": report_id,
            "diff": {"ticket_id": ticket.data[0]["id"], "org": assigned_org, "title": title},
        }).execute()
        return ticket.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Tickets ────────────────────────────────────────────────────────────────

@router.get("/tickets")
async def list_tickets(
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    limit: int = Query(50, le=100),
    offset: int = Query(0),
    _admin=Depends(require_admin),
):
    try:
        q = _db().table("tickets").select("*")
        if status:
            q = q.eq("status", status)
        if priority:
            q = q.eq("priority", priority)
        res = q.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
        return {"data": res.data or [], "count": len(res.data or [])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/tickets/{ticket_id}")
async def update_ticket(ticket_id: str, payload: dict, admin=Depends(require_admin)):
    allowed = {
        "status", "priority", "assigned_to", "external_ref", "notes",
        "assigned_org", "kanban_state",
    }
    update = {k: v for k, v in payload.items() if k in allowed}
    if not update:
        raise HTTPException(status_code=400, detail="Nenhum campo editável fornecido.")

    if update.get("kanban_state") and update["kanban_state"] not in (
        "aberto", "em_atendimento", "resolvido", "fechado"
    ):
        raise HTTPException(status_code=400, detail="kanban_state inválido.")

    try:
        try:
            _db().table("tickets").update(update).eq("id", ticket_id).execute()
        except Exception as col_err:
            logger.warning("update_ticket V4 cols missing, fallback: %s", col_err)
            safe = {k: v for k, v in update.items() if k not in ("assigned_org", "kanban_state")}
            if safe:
                _db().table("tickets").update(safe).eq("id", ticket_id).execute()
        _db().table("admin_audit").insert({
            "user_id": admin.get("sub"),
            "action": "patch_ticket",
            "target_table": "tickets",
            "target_id": ticket_id,
            "diff": update,
        }).execute()
        return {"ok": True, "updated": update}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tickets/{ticket_id}/dispatch-draft")
async def ticket_dispatch_draft(ticket_id: str, _admin=Depends(require_admin)):
    """
    Gera um e-mail/SMS pré-formatado pra encaminhar o chamado ao órgão
    responsável. Frontend pode abrir mailto: direto no cliente de e-mail
    do admin OU copiar pra colar em outro canal.

    NÃO envia nada automaticamente — só monta o conteúdo.
    Integração real (API EMLURB 156) é trabalho futuro; por ora o admin
    aprova manualmente o despacho de cada chamado.
    """
    try:
        t = _db().table("tickets").select("*").eq("id", ticket_id).execute()
        if not t.data:
            raise HTTPException(status_code=404, detail="Chamado não encontrado.")
        ticket = t.data[0]

        report = None
        address = None
        if ticket.get("report_id"):
            r = _db().table("reports").select(
                "id,bairro,type,lat,lon,description,photo_url"
            ).eq("id", ticket["report_id"]).execute()
            report = (r.data or [None])[0]
            if report and report.get("lat") and report.get("lon"):
                try:
                    from services.geocoding import reverse_geocode
                    address = await reverse_geocode(report["lat"], report["lon"])
                except Exception:
                    address = None

        draft = build_dispatch_email(ticket, report=report, address=address)
        return draft
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tickets/{ticket_id}/mark-dispatched")
async def mark_ticket_dispatched(ticket_id: str, payload: dict | None = None, admin=Depends(require_admin)):
    """
    Registra que o admin encaminhou o chamado externamente (por e-mail,
    telefone, etc). Move kanban_state pra em_atendimento, salva nota
    de auditoria com canal usado.

    Body opcional: {channel: "EMLURB 156" | ..., notes: "...", external_ref: "..."}.
    """
    payload = payload or {}
    channel = (payload.get("channel") or "manual").strip()[:120]
    external_ref = (payload.get("external_ref") or "").strip()[:120] or None
    notes = (payload.get("notes") or "").strip()[:500] or None

    try:
        update = {"kanban_state": "em_atendimento"}
        if external_ref:
            update["external_ref"] = external_ref
        if notes:
            update["notes"] = notes
        try:
            _db().table("tickets").update(update).eq("id", ticket_id).execute()
        except Exception as col_err:
            logger.warning("mark_dispatched V4 cols fallback: %s", col_err)
            safe = {k: v for k, v in update.items() if k != "kanban_state"}
            if safe:
                _db().table("tickets").update(safe).eq("id", ticket_id).execute()

        _db().table("admin_audit").insert({
            "user_id": admin.get("sub"),
            "action": "dispatch_external",
            "target_table": "tickets",
            "target_id": ticket_id,
            "diff": {"channel": channel, "external_ref": external_ref},
        }).execute()
        return {"ok": True, "ticket_id": ticket_id, "channel": channel}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tickets/{ticket_id}/close")
async def close_ticket(ticket_id: str, payload: dict | None = None, admin=Depends(require_admin)):
    try:
        res = _db().table("tickets").select("id,report_id").eq("id", ticket_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Ticket não encontrado.")
        ticket = res.data[0]
        resolution_note = ((payload or {}).get("resolution_note") or "").strip()
        if len(resolution_note) < 8:
            raise HTTPException(status_code=400, detail="Informe uma observação de resolução.")
        _db().table("tickets").update({
            "status": "resolvido",
            "notes": resolution_note,
        }).eq("id", ticket_id).execute()
        if ticket.get("report_id"):
            _db().table("reports").update({"status": "resolved"}).eq("id", ticket["report_id"]).execute()
        _db().table("admin_audit").insert({
            "user_id": admin.get("sub"),
            "action": "close_ticket",
            "target_table": "tickets",
            "target_id": ticket_id,
            "diff": {"status": "resolvido"},
        }).execute()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Metrics ────────────────────────────────────────────────────────────────

@router.get("/metrics/by-rpa")
async def metrics_by_rpa(_admin=Depends(require_admin)):
    """Contagem de reports agrupada por RPA."""
    try:
        res = _db().table("report_official_crossings").select("rpa").execute()
        rows = res.data or []
        counts: dict[str, int] = {}
        for r in rows:
            rpa = r.get("rpa") or "Desconhecido"
            counts[rpa] = counts.get(rpa, 0) + 1
        return {"data": [{"rpa": k, "count": v} for k, v in sorted(counts.items(), key=lambda x: -x[1])]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/metrics")
async def metrics(_admin=Depends(require_admin)):
    """KPIs principais para a AdminPage."""
    try:
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        reports = _db().table("reports").select(
            "id,status,type,bairro,created_at"
        ).order("created_at", desc=True).limit(5000).execute()
        rows = reports.data or []
        last24 = [r for r in rows if (r.get("created_at") or "") >= since]
        by_bairro: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for r in rows:
            by_bairro[r.get("bairro") or "Desconhecido"] = by_bairro.get(r.get("bairro") or "Desconhecido", 0) + 1
            by_type[r.get("type") or "outro"] = by_type.get(r.get("type") or "outro", 0) + 1
        return {
            "total": len(rows),
            "last24h": len(last24),
            "pending": sum(1 for r in rows if r.get("status") in (None, "pending")),
            "validated": sum(1 for r in rows if r.get("status") == "validated"),
            "resolved": sum(1 for r in rows if r.get("status") == "resolved"),
            "top_bairros": [{"bairro": k, "count": v} for k, v in sorted(by_bairro.items(), key=lambda x: -x[1])[:10]],
            "by_type": [{"type": k, "count": v} for k, v in sorted(by_type.items(), key=lambda x: -x[1])],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/audit")
async def audit_log(limit: int = Query(50, le=200), _admin=Depends(require_admin)):
    try:
        res = _db().table("admin_audit").select("*").order("created_at", desc=True).limit(limit).execute()
        return {"data": res.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/metrics/by-neighborhood")
async def metrics_by_neighborhood(_admin=Depends(require_admin)):
    """Contagem de reports por bairro (top 20)."""
    try:
        res = _db().table("report_official_crossings").select("neighborhood").execute()
        rows = res.data or []
        counts: dict[str, int] = {}
        for r in rows:
            nb = r.get("neighborhood") or "Desconhecido"
            counts[nb] = counts.get(nb, 0) + 1
        top = sorted(counts.items(), key=lambda x: -x[1])[:20]
        return {"data": [{"neighborhood": k, "count": v} for k, v in top]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/metrics/recurrent-hotspots")
async def recurrent_hotspots(
    min_score: float = Query(2.0),
    limit: int = Query(20, le=100),
    _admin=Depends(require_admin),
):
    """Bairros/ruas com recurrence_score alto."""
    try:
        res = (
            _db()
            .table("report_official_crossings")
            .select("neighborhood,rpa,nearest_road_name,recurrence_score,report_id")
            .gte("recurrence_score", min_score)
            .order("recurrence_score", desc=True)
            .limit(limit)
            .execute()
        )
        return {"data": res.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Export ─────────────────────────────────────────────────────────────────

@router.get("/export/reports.csv")
async def export_reports_csv(_admin=Depends(require_admin)):
    """Exporta todos os reports como CSV."""
    import csv, io
    try:
        res = _db().table("reports").select(
            "id,type,severity,bairro,lat,lon,description,status,"
            "likes_up,likes_down,ai_validation_score,created_at"
        ).order("created_at", desc=True).limit(5000).execute()
        rows = res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=reports.csv"},
    )


@router.get("/export/reports.geojson")
async def export_reports_geojson(_admin=Depends(require_admin)):
    """Exporta reports com lat/lon como GeoJSON."""
    import json
    try:
        res = _db().table("reports").select(
            "id,type,severity,bairro,lat,lon,description,status,created_at"
        ).not_.is_("lat", "null").order("created_at", desc=True).limit(5000).execute()
        rows = res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    features = []
    for r in rows:
        if r.get("lat") is None or r.get("lon") is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
            "properties": {k: v for k, v in r.items() if k not in ("lat", "lon")},
        })

    geojson = {"type": "FeatureCollection", "features": features}
    return StreamingResponse(
        iter([json.dumps(geojson, ensure_ascii=False)]),
        media_type="application/geo+json",
        headers={"Content-Disposition": "attachment; filename=reports.geojson"},
    )
