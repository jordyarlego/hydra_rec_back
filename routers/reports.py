import asyncio
import math
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from models.schemas import CreateReportPayload, LikePayload
from services.security import hash_ip
from services.rate_limit import can_report
from services.alerts_engine import check_and_create_alerts
from services.weather_cross import snapshot_for_point
from services.storage import upload_photo, PhotoError, MAX_BYTES as PHOTO_MAX_BYTES
from services.severity import infer_initial_severity, resolve_severity_from_vision

router = APIRouter()
logger = logging.getLogger(__name__)
MAX_REPORT_DISTANCE_M = 1500


def _haversine(lat1, lon1, lat2, lon2):
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return 2 * 6371 * math.asin(math.sqrt(a)) * 1000  # metros


async def _create_report_core(
    *,
    request: Request,
    tipo: str,
    severidade: Optional[str] = None,
    lat: float,
    lon: float,
    user_lat: float,
    user_lon: float,
    bairro: Optional[str],
    descricao: Optional[str],
    photo_url: Optional[str] = None,
):
    """Implementação compartilhada (JSON + multipart). Valida, cruza APAC, insere."""
    distance_m = _haversine(user_lat, user_lon, lat, lon)
    if distance_m > MAX_REPORT_DISTANCE_M:
        raise HTTPException(
            status_code=400,
            detail=f"O report precisa estar a até {MAX_REPORT_DISTANCE_M / 1000:.1f}km da sua localização atual.",
        )

    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown").split(",")[0].strip()
    ip_hash = hash_ip(ip)

    if not can_report(ip_hash):
        raise HTTPException(status_code=429, detail="Aguarde 5 minutos entre reports.")

    weather_snapshot = await snapshot_for_point(lat, lon)
    weather_snapshot_id = (weather_snapshot or {}).get("id")

    # Gravidade NUNCA vem do usuário: derivamos do tipo + chuva APAC.
    # Se houver foto, o pipeline de visão promove a severity_hint depois.
    if severidade is None:
        severidade = infer_initial_severity(tipo, weather_snapshot)

    from services.supabase_client import get_service_client
    client = get_service_client()

    row = {
        "type":                tipo,
        "severity":            severidade,
        "lat":                 lat,
        "lon":                 lon,
        "bairro":              bairro,
        "description":         descricao,
        "ip_hash":             ip_hash,
        "user_agent":          request.headers.get("User-Agent", "")[:200],
        "weather_snapshot_id": weather_snapshot_id,
        "photo_url":           photo_url,
    }

    # Lista de colunas V3 que devem ser dropadas caso o schema ainda seja V2
    v3_only_cols = ("weather_snapshot_id", "photo_url")
    photo_persisted = True

    try:
        res = client.table("reports").insert(row).execute()
    except Exception as e:
        msg = str(e).lower()
        if any(col in msg for col in v3_only_cols) or "column" in msg or "42703" in msg:
            logger.error(
                "🚨 SCHEMA V3 AUSENTE no Supabase — coluna photo_url/weather_snapshot_id não existe.\n"
                "    Aplique back_end_hydrarec/migrations/v3_civic_reports.sql no SQL Editor.\n"
                "    Por enquanto a foto NÃO está sendo persistida no banco (apenas no Storage)."
            )
            stripped = {k: v for k, v in row.items() if k not in v3_only_cols}
            photo_persisted = False
            try:
                res = client.table("reports").insert(stripped).execute()
            except Exception as e2:
                logger.error(f"report insert retry failed: {e2}")
                raise HTTPException(status_code=500, detail="Erro ao salvar report.")
        else:
            logger.error(f"report insert failed: {e}")
            raise HTTPException(status_code=500, detail="Erro ao salvar report.")

    if bairro:
        check_and_create_alerts(bairro)

    report_id = res.data[0]["id"]

    # Cruzamento com dados oficiais (fire-and-forget)
    asyncio.create_task(_cross_official(report_id))
    if photo_url and photo_persisted:
        asyncio.create_task(_run_ai_pipeline(report_id, photo_url, weather_snapshot))
    elif photo_url and not photo_persisted:
        logger.warning(f"AI pipeline pulado para {report_id} — schema V3 ausente.")

    return {
        "id": report_id,
        "status": "criado",
        "type": tipo,
        "severity": severidade,
        "lat": lat,
        "lon": lon,
        "bairro": bairro,
        "description": descricao,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "confirmed_count": 0,
        "likes_up": 0,
        "likes_down": 0,
        "weather": weather_snapshot,
        "photo_url": photo_url if photo_persisted else None,
        "photo_persisted": photo_persisted,
        "schema_warning": None if photo_persisted else "Migration V3 não aplicada — foto perdida.",
    }


async def _cross_official(report_id: str) -> None:
    """Cruza report com dados oficiais em background. Falhas são logadas."""
    try:
        from services.geo_cross import cross_report_with_official_data
        await cross_report_with_official_data(report_id)
    except Exception as e:
        logger.warning(f"official crossing failed for {report_id}: {e}")


async def _run_ai_pipeline(report_id: str, photo_url: str, weather_snapshot: Optional[dict]) -> None:
    try:
        from services.ai_vision import describe_photo
        from services.ai_validator import persist_validation
        from services.supabase_client import get_service_client

        vision = await describe_photo(photo_url)
        client = get_service_client()
        vision_update = {
            "photo_ai_description": vision.get("description"),
            "photo_ai_confidence": vision.get("confidence"),
        }
        # IA é fonte da verdade da gravidade: promove severity_hint quando válido.
        promoted_sev = resolve_severity_from_vision("", vision.get("severity_hint"))
        if promoted_sev:
            vision_update["severity"] = promoted_sev
        # is_urban_problem + severity_hint só persistem se as colunas existirem (V4)
        try:
            update_with_v4 = dict(vision_update)
            update_with_v4["photo_ai_is_urban_problem"] = vision.get("is_urban_problem")
            update_with_v4["photo_ai_severity_hint"] = vision.get("severity_hint")
            client.table("reports").update(update_with_v4).eq("id", report_id).execute()
        except Exception as col_err:
            logger.warning("V4 vision columns missing, fallback: %s", col_err)
            client.table("reports").update(vision_update).eq("id", report_id).execute()

        res = client.table("reports").select("*").eq("id", report_id).execute()
        if not res.data:
            return
        report = res.data[0]
        report["weather"] = weather_snapshot
        await persist_validation(report_id, report)
    except Exception as e:
        logger.warning("AI pipeline failed for %s: %s", report_id, e)


@router.post("/api/reports", status_code=201)
async def create_report(payload: CreateReportPayload, request: Request):
    """Cria report SEM foto (JSON). Backward-compatible com clientes V2."""
    return await _create_report_core(
        request=request,
        tipo=payload.tipo,
        severidade=payload.severidade,
        lat=payload.lat,
        lon=payload.lon,
        user_lat=payload.user_lat,
        user_lon=payload.user_lon,
        bairro=payload.bairro,
        descricao=payload.descricao,
    )


@router.post("/api/reports/with-photo", status_code=201)
async def create_report_with_photo(
    request: Request,
    tipo: str = Form(..., pattern="^(alagamento|deslizamento|queda_arvore|via_intransitavel|poste_caido|buraco|lixo|iluminacao|outro)$"),
    severidade: Optional[str] = Form(None, pattern="^(leve|moderado|grave)$"),
    lat: float = Form(..., ge=-8.16, le=-7.93),
    lon: float = Form(..., ge=-35.02, le=-34.83),
    user_lat: float = Form(..., ge=-8.16, le=-7.93),
    user_lon: float = Form(..., ge=-35.02, le=-34.83),
    bairro: Optional[str] = Form(None),
    descricao: Optional[str] = Form(None, max_length=280),
    photo: Optional[UploadFile] = File(None),
):
    """
    Cria report COM foto (multipart). Usado pelo flow Fase 2 (QuickReportSheet).
    Campo `photo` opcional — endpoint funciona mesmo sem foto.
    """
    photo_url: Optional[str] = None

    # Log do que chegou pra diagnóstico
    logger.info(
        f"create_report_with_photo: photo={'present' if photo else 'None'} "
        f"filename={getattr(photo, 'filename', None)} "
        f"content_type={getattr(photo, 'content_type', None)}"
    )

    if photo is not None and photo.filename:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > PHOTO_MAX_BYTES + 4096:
            raise HTTPException(status_code=413, detail=f"Foto maior que {PHOTO_MAX_BYTES // (1024*1024)}MB.")
        data = await photo.read()
        logger.info(f"photo bytes lidos: {len(data)} bytes")
        try:
            photo_url = upload_photo(data, photo.content_type or "image/jpeg")
            logger.info(f"✅ photo upload OK → {photo_url}")
        except PhotoError as e:
            logger.error(f"❌ PhotoError no upload: {e}")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"❌ photo upload exception: {type(e).__name__}: {e}")
            raise HTTPException(status_code=500, detail=f"Falha ao enviar foto: {type(e).__name__}")
    else:
        logger.warning("⚠️  /api/reports/with-photo chamado SEM foto (photo=None ou filename vazio)")

    return await _create_report_core(
        request=request,
        tipo=tipo,
        severidade=severidade,
        lat=lat,
        lon=lon,
        user_lat=user_lat,
        user_lon=user_lon,
        bairro=bairro,
        descricao=descricao,
        photo_url=photo_url,
    )


_NEARBY_FIELDS_V3 = (
    "id,type,severity,lat,lon,bairro,description,confirmed_count,created_at,"
    "photo_url,likes_up,likes_down,status,ai_validation_score"
)
_NEARBY_FIELDS_V2 = "id,type,severity,lat,lon,bairro,description,confirmed_count,created_at"


@router.get("/api/reports/nearby")
async def get_nearby_reports(lat: float, lon: float, radius: float = 2000):
    from services.supabase_client import get_client
    client = get_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    delta_lat = radius / 111000
    delta_lon = radius / (111000 * abs(math.cos(math.radians(lat))) or 1)

    def _query(fields: str):
        return (client.table("reports")
                .select(fields)
                .or_("resolved.is.false,resolved.is.null")
                .gte("created_at", cutoff)
                .gte("lat", lat - delta_lat).lte("lat", lat + delta_lat)
                .gte("lon", lon - delta_lon).lte("lon", lon + delta_lon)
                .order("created_at", desc=True)
                .execute())
    try:
        try:
            res = _query(_NEARBY_FIELDS_V3)
        except Exception as e:
            msg = str(e).lower()
            if "column" in msg or "does not exist" in msg or "42703" in msg:
                res = _query(_NEARBY_FIELDS_V2)
            else:
                raise
        reports = [r for r in (res.data or []) if _haversine(lat, lon, r["lat"], r["lon"]) <= radius]
    except Exception as e:
        logger.error(f"nearby reports fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Erro ao buscar reports.")
    return {"reports": reports, "count": len(reports)}


@router.get("/api/reports/week-stats")
async def get_week_stats(days: int = 7):
    """Stats agregadas pra transparência cívica:
       total de reports, resolvidos, tempo médio de resolução, top categoria.
    """
    from services.supabase_client import get_client
    client = get_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
    try:
        # 1. total de reports criados na janela
        rep_res = (
            client.table("reports")
            .select("id,type,created_at", count="exact")
            .gte("created_at", cutoff)
            .limit(2000)
            .execute()
        )
        reports_total = rep_res.count or len(rep_res.data or [])

        # Top categoria
        by_cat = {}
        for r in (rep_res.data or []):
            t = r.get("type") or "outro"
            by_cat[t] = by_cat.get(t, 0) + 1
        top_cat = max(by_cat.items(), key=lambda x: x[1])[0] if by_cat else None

        # 2. tickets resolvidos na janela + tempo médio
        use_kanban = True
        try:
            client.table("tickets").select("kanban_state").limit(1).execute()
        except Exception:
            use_kanban = False

        q = client.table("tickets").select(
            "id,report_id,updated_at,created_at," + ("kanban_state" if use_kanban else "status")
        ).gte("updated_at", cutoff).limit(500)
        if use_kanban:
            q = q.eq("kanban_state", "resolvido")
        else:
            q = q.eq("status", "resolvido")
        tk_res = q.execute()

        resolved = len(tk_res.data or [])
        deltas = []
        for t in (tk_res.data or []):
            try:
                a = datetime.fromisoformat(t["created_at"].replace("Z", "+00:00"))
                b = datetime.fromisoformat(t["updated_at"].replace("Z", "+00:00"))
                deltas.append((b - a).total_seconds() / 86400)
            except Exception:
                continue
        avg_days = round(sum(deltas) / len(deltas), 1) if deltas else None

        return {
            "days":            days,
            "reports_total":   reports_total,
            "resolved":        resolved,
            "avg_resolution_days": avg_days,
            "top_category":    top_cat,
        }
    except Exception as e:
        logger.warning("week-stats falhou: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/reports/resolved-week")
async def get_resolved_week(days: int = 7, limit: int = 200):
    """Reports cujos tickets vinculados viraram 'resolvido' nos últimos N dias.

    Loop cívico de impacto visual: cidadão vê no mapa a cidade trabalhando.
    Retorna lista enxuta (id, lat, lon, type, bairro, resolved_at, original_at).
    """
    from services.supabase_client import get_client
    client = get_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
    try:
        # Detecta se kanban_state existe (v4)
        use_kanban = True
        try:
            client.table("tickets").select("kanban_state").limit(1).execute()
        except Exception:
            use_kanban = False

        q = client.table("tickets").select(
            "id,report_id,updated_at," + ("kanban_state" if use_kanban else "status")
        ).gte("updated_at", cutoff).not_.is_("report_id", "null").limit(limit)
        if use_kanban:
            q = q.eq("kanban_state", "resolvido")
        else:
            q = q.eq("status", "resolvido")
        tickets_res = q.execute()

        tickets = tickets_res.data or []
        if not tickets:
            return {"reports": [], "count": 0, "days": days}

        report_ids = [t["report_id"] for t in tickets if t.get("report_id")]
        ids_csv = ",".join(f'"{rid}"' for rid in report_ids)
        rep_res = (
            client.table("reports")
            .select("id,type,lat,lon,bairro,created_at")
            .or_(f"id.in.({ids_csv})")
            .execute()
        )
        rep_by_id = {r["id"]: r for r in (rep_res.data or [])}

        results = []
        for t in tickets:
            r = rep_by_id.get(t["report_id"])
            if not r or r.get("lat") is None or r.get("lon") is None:
                continue
            results.append({
                "id":            r["id"],
                "type":          r.get("type"),
                "lat":           r["lat"],
                "lon":           r["lon"],
                "bairro":        r.get("bairro"),
                "resolved_at":   t.get("updated_at"),
                "original_at":   r.get("created_at"),
            })

        results.sort(key=lambda r: r.get("resolved_at") or "", reverse=True)
        return {"reports": results, "count": len(results), "days": days}
    except Exception as e:
        logger.warning("resolved-week falhou: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/reports/{report_id}/confirm", status_code=200)
async def confirm_report(report_id: str, request: Request):
    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown").split(",")[0].strip()
    ip_hash = hash_ip(ip)
    from services.supabase_client import get_service_client
    client = get_service_client()
    try:
        res = client.table("reports").select("id,confirmed_count,ip_hash,bairro").eq("id", report_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Report não encontrado.")
        r = res.data[0]
        if r["ip_hash"] == ip_hash:
            raise HTTPException(status_code=403, detail="Não pode confirmar o próprio report.")
        client.table("reports").update({"confirmed_count": r["confirmed_count"] + 1}).eq("id", report_id).execute()
        if r.get("bairro"):
            check_and_create_alerts(r["bairro"])
        return {"confirmed_count": r["confirmed_count"] + 1}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/reports/{report_id}/like", status_code=200)
async def like_report(report_id: str, payload: LikePayload, request: Request):
    if payload.vote not in (-1, 1):
        raise HTTPException(status_code=400, detail="Voto inválido.")

    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown").split(",")[0].strip()
    ip_hash = hash_ip(ip)
    from services.supabase_client import get_service_client
    client = get_service_client()

    try:
        res = client.table("reports").select("id,ip_hash,likes_up,likes_down,bairro").eq("id", report_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Report não encontrado.")
        report = res.data[0]
        if report.get("ip_hash") == ip_hash:
            raise HTTPException(status_code=403, detail="Não pode votar no próprio report.")

        previous = (client.table("report_likes")
                    .select("vote")
                    .eq("report_id", report_id)
                    .eq("ip_hash", ip_hash)
                    .execute())
        old_vote = previous.data[0]["vote"] if previous.data else None

        if old_vote is None:
            client.table("report_likes").insert({
                "report_id": report_id,
                "ip_hash": ip_hash,
                "vote": payload.vote,
                "weight": 1.0,
            }).execute()
        elif old_vote != payload.vote:
            (client.table("report_likes")
             .update({"vote": payload.vote})
             .eq("report_id", report_id)
             .eq("ip_hash", ip_hash)
             .execute())

        likes_up = int(report.get("likes_up") or 0)
        likes_down = int(report.get("likes_down") or 0)
        if old_vote == 1:
            likes_up -= 1
        elif old_vote == -1:
            likes_down -= 1
        if payload.vote == 1:
            likes_up += 1
        else:
            likes_down += 1

        client.table("reports").update({
            "likes_up": max(likes_up, 0),
            "likes_down": max(likes_down, 0),
        }).eq("id", report_id).execute()

        if report.get("bairro"):
            check_and_create_alerts(report["bairro"])

        return {"likes_up": max(likes_up, 0), "likes_down": max(likes_down, 0), "vote": payload.vote}
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e).lower()
        if "report_likes" in msg or "likes_up" in msg or "column" in msg or "does not exist" in msg or "42703" in msg:
            raise HTTPException(status_code=503, detail="Likes exigem a migration V3 aplicada.")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/alerts/active")
async def get_active_alerts(bairro: str | None = None):
    from services.supabase_client import get_client
    client = get_client()
    try:
        q = client.table("alerts").select("*").eq("active", True).gt("expires_at", datetime.now(timezone.utc).isoformat())
        if bairro:
            q = q.eq("bairro", bairro)
        res = q.order("created_at", desc=True).execute()
        return {"alerts": res.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/reports/{report_id}/subscribe-push", status_code=200)
async def subscribe_report_push(report_id: str, payload: dict):
    """Vincula um push endpoint do cidadão a um report específico,
    pra notificá-lo quando o ticket muda de estado.

    Body: { "endpoint": "<browser push endpoint URL>" }
    Idempotente — chamar 2x não duplica.
    """
    endpoint = (payload or {}).get("endpoint", "").strip()
    if not endpoint:
        raise HTTPException(status_code=400, detail="endpoint obrigatório.")

    from services.supabase_client import get_service_client
    client = get_service_client()
    try:
        # Valida que o report existe
        rep = client.table("reports").select("id").eq("id", report_id).limit(1).execute()
        if not rep.data:
            raise HTTPException(status_code=404, detail="Report não encontrado.")

        client.table("report_push_subscriptions").upsert(
            {"report_id": report_id, "push_endpoint": endpoint},
            on_conflict="report_id,push_endpoint",
        ).execute()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("subscribe_report_push falhou: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# IMPORTANTE: `/api/reports/{report_id}` precisa ficar por ÚLTIMO entre os GET
# do router para não capturar rotas específicas como `/api/reports/nearby`.

_REPORT_FIELDS_V3 = (
    "id,type,severity,lat,lon,bairro,description,"
    "photo_url,photo_ai_description,photo_ai_confidence,"
    "ai_validation_score,ai_validation_notes,"
    "likes_up,likes_down,status,confirmed_count,"
    "weather_snapshot_id,created_at"
)
_REPORT_FIELDS_V2 = "id,type,severity,lat,lon,bairro,description,confirmed_count,created_at"


@router.get("/api/reports/{report_id}")
async def get_report(report_id: str):
    """Detalhe completo de um report — usado pelo popup do pin no mapa."""
    from services.supabase_client import get_client
    client = get_client()

    def _fetch(fields: str):
        return (client.table("reports").select(fields).eq("id", report_id).execute())

    try:
        res = _fetch(_REPORT_FIELDS_V3)
    except Exception as e:
        # Schema V2 — fallback (migration V3 ainda não aplicada)
        msg = str(e).lower()
        if "column" in msg or "does not exist" in msg or "42703" in msg:
            try:
                res = _fetch(_REPORT_FIELDS_V2)
            except Exception as e2:
                logger.error(f"get_report fallback failed: {e2}")
                raise HTTPException(status_code=500, detail="Erro ao buscar report.")
        else:
            logger.error(f"get_report failed: {e}")
            raise HTTPException(status_code=500, detail="Erro ao buscar report.")

    if not res.data:
        raise HTTPException(status_code=404, detail="Report não encontrado.")
    report = res.data[0]

    # Hidrata snapshot meteorológico (se houver e tabela existir)
    weather = None
    if report.get("weather_snapshot_id"):
        try:
            ws = (client.table("weather_snapshots")
                  .select("*")
                  .eq("id", report["weather_snapshot_id"])
                  .execute())
            weather = ws.data[0] if ws.data else None
        except Exception as e:
            logger.debug(f"weather snapshot hydrate failed: {e}")

    report["weather"] = weather
    return report


@router.get("/api/reports/{report_id}/address")
async def get_report_address_public(report_id: str):
    """
    Resolve endereço (rua, bairro, pontos de referência) do pin no mapa.
    Endpoint público — usado pelo popup do mapa pra mostrar contexto local.
    """
    from services.supabase_client import get_client
    client = get_client()
    try:
        res = client.table("reports").select("id,lat,lon,bairro").eq("id", report_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not res.data:
        raise HTTPException(status_code=404, detail="Report não encontrado.")
    r = res.data[0]
    lat, lon = r.get("lat"), r.get("lon")
    if lat is None or lon is None:
        return {"address": None, "landmarks": [], "reason": "Sem coordenadas."}

    from services.geocoding import reverse_geocode, nearby_landmarks
    import asyncio as _a
    addr, landmarks = await _a.gather(
        reverse_geocode(lat, lon),
        nearby_landmarks(lat, lon, radius_m=200),
        return_exceptions=True,
    )
    if isinstance(addr, Exception):
        addr = {"source": "fallback", "full_address": None}
    if isinstance(landmarks, Exception):
        landmarks = []
    return {"address": addr, "landmarks": landmarks}
