"""
Endpoints /api/admin/* — todos protegidos por require_admin (JWT Supabase + role=admin).
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from services.auth_guard import require_admin

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


@router.post("/official-data/import")
async def trigger_official_import(
    sources: Optional[list[str]] = Query(default=None),
    _admin=Depends(require_admin),
):
    """Dispara importação de dados oficiais (assíncrono). sources=[] importa todos."""
    from services.official_importer import import_all
    results = await import_all(sources or None)
    return {"results": results}


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

@router.get("/reports")
async def list_reports(
    status: Optional[str] = Query(None),
    tipo: Optional[str] = Query(None),
    bairro: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    limit: int = Query(25, le=100),
    offset: int = Query(0),
    _admin=Depends(require_admin),
):
    query = _db().table("reports").select(
        "id,type,severity,lat,lon,bairro,description,"
        "likes_up,likes_down,status,ai_validation_score,"
        "photo_url,confirmed_count,created_at"
    )
    if status:
        query = query.eq("status", status)
    if tipo:
        query = query.eq("type", tipo)
    if bairro:
        query = query.ilike("bairro", f"%{bairro}%")
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
    """Muda status, adiciona notas. Audita a ação."""
    allowed = {"status", "ai_validation_notes"}
    update = {k: v for k, v in payload.items() if k in allowed}
    if not update:
        raise HTTPException(status_code=400, detail="Nenhum campo editável fornecido.")
    try:
        _db().table("reports").update(update).eq("id", report_id).execute()

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


@router.post("/reports/{report_id}/ticket")
async def create_ticket_from_report(
    report_id: str,
    payload: dict,
    admin=Depends(require_admin),
):
    """Cria ticket administrativo a partir de um report."""
    try:
        report_res = _db().table("reports").select("id,bairro,type,severity").eq("id", report_id).execute()
        if not report_res.data:
            raise HTTPException(status_code=404, detail="Report não encontrado.")
        report = report_res.data[0]
        row = {
            "report_id": report_id,
            "bairro": report.get("bairro"),
            "type": report.get("type"),
            "priority": payload.get("priority") or "media",
            "status": "aberto",
            "assigned_to": payload.get("assigned_to"),
            "external_ref": payload.get("external_ref"),
            "notes": payload.get("notes"),
            "created_by": admin.get("sub"),
        }
        ticket = _db().table("tickets").insert(row).execute()
        _db().table("reports").update({"ticket_id": ticket.data[0]["id"]}).eq("id", report_id).execute()
        _db().table("admin_audit").insert({
            "user_id": admin.get("sub"),
            "action": "create_ticket",
            "target_table": "reports",
            "target_id": report_id,
            "diff": {"ticket_id": ticket.data[0]["id"]},
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
    allowed = {"status", "priority", "assigned_to", "external_ref", "notes"}
    update = {k: v for k, v in payload.items() if k in allowed}
    if not update:
        raise HTTPException(status_code=400, detail="Nenhum campo editável fornecido.")
    try:
        _db().table("tickets").update(update).eq("id", ticket_id).execute()
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


@router.post("/tickets/{ticket_id}/close")
async def close_ticket(ticket_id: str, admin=Depends(require_admin)):
    try:
        res = _db().table("tickets").select("id,report_id").eq("id", ticket_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Ticket não encontrado.")
        ticket = res.data[0]
        _db().table("tickets").update({"status": "resolvido"}).eq("id", ticket_id).execute()
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
