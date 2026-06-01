from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

VALIDATION_WINDOW_MINUTES = 15
NEARBY_VALIDATION_RADIUS_M = 2000


def validation_deadline_from(
    now: datetime | None = None,
    minutes: int = VALIDATION_WINDOW_MINUTES,
) -> str:
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return (base + timedelta(minutes=minutes)).isoformat()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def calculate_validation_verdict(report: dict[str, Any]) -> dict[str, Any]:
    """Fecha o ciclo de validação cidadão + IA + APAC.

    A IA continua sendo insumo; a decisão final é determinística e auditável.
    """
    score = 25.0
    reasons: list[str] = []

    ai_score = report.get("ai_validation_score")
    if ai_score is not None:
        score += _clamp(_to_float(ai_score), 0.0, 1.0) * 40
        reasons.append("score da IA incorporado")
    else:
        reasons.append("sem score da IA no fechamento")

    likes_up = max(int(report.get("likes_up") or 0), 0)
    likes_down = max(int(report.get("likes_down") or 0), 0)
    confirmed_count = max(int(report.get("confirmed_count") or 0), 0)

    score += min(likes_up, 5) * 5
    score -= min(likes_down, 5) * 8
    score += min(confirmed_count, 4) * 6

    if likes_up:
        reasons.append(f"{likes_up} voto(s) positivo(s)")
    if likes_down:
        reasons.append(f"{likes_down} voto(s) negativo(s)")
    if confirmed_count:
        reasons.append(f"{confirmed_count} confirmação(ões)")

    weather = report.get("weather") or report.get("weather_snapshot") or {}
    rain_1h = _to_float(weather.get("rain_1h_mm"))
    rain_24h = _to_float(weather.get("rain_24h_mm"))
    report_type = report.get("type") or report.get("tipo")
    if report_type == "alagamento" and (rain_1h >= 5 or rain_24h >= 15):
        score += 12
        reasons.append("chuva APAC/CEMADEN compatível")

    if report.get("photo_url"):
        score += 6
    if report.get("photo_ai_is_urban_problem") is True:
        score += 7
        reasons.append("foto reconhecida como problema urbano")
    elif report.get("photo_ai_is_urban_problem") is False:
        score -= 35
        reasons.append("foto não parece problema urbano")

    score = round(_clamp(score, 0, 100))

    if score >= 75:
        status = "confirmado"
        verdict = "confirmado"
    elif score >= 55:
        status = "provavel"
        verdict = "provável"
    elif score >= 35:
        status = "pouca_evidencia"
        verdict = "pouca evidência"
    else:
        status = "suspeito"
        verdict = "suspeito"

    return {
        "score": score,
        "status": status,
        "verdict": verdict,
        "summary": "; ".join(reasons) or "Sem sinais adicionais no prazo de validação.",
    }


async def finalize_due_reports(limit: int = 20) -> int:
    from services.supabase_client import get_service_client

    client = get_service_client()
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        res = await asyncio.to_thread(
            lambda: client.table("reports")
            .select("*")
            .eq("status", "em_validacao")
            .lte("validation_deadline", now_iso)
            .limit(limit)
            .execute()
        )
    except Exception as e:
        logger.debug("report validation fetch skipped: %s", e)
        return 0

    count = 0
    for report in res.data or []:
        try:
            if report.get("weather_snapshot_id"):
                ws = await asyncio.to_thread(
                    lambda: client.table("weather_snapshots")
                    .select("*")
                    .eq("id", report["weather_snapshot_id"])
                    .limit(1)
                    .execute()
                )
                report["weather"] = (ws.data or [None])[0]

            verdict = calculate_validation_verdict(report)
            await asyncio.to_thread(
                lambda: client.table("reports")
                .update(
                    {
                        "status": verdict["status"],
                        "validation_verdict": verdict["verdict"],
                        "validation_score": verdict["score"],
                        "validation_summary": verdict["summary"],
                        "validated_at": now_iso,
                    }
                )
                .eq("id", report["id"])
                .execute()
            )
            count += 1
        except Exception as e:
            logger.warning("report validation close failed for %s: %s", report.get("id"), e)
    return count


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return 2 * 6371 * math.asin(math.sqrt(a)) * 1000


def filter_nearby_subscriptions(
    subscriptions: list[dict[str, Any]],
    lat: float,
    lon: float,
    radius_m: int = NEARBY_VALIDATION_RADIUS_M,
) -> list[dict[str, Any]]:
    nearby: list[dict[str, Any]] = []
    for sub in subscriptions:
        sub_lat = sub.get("lat")
        sub_lon = sub.get("lon")
        if sub_lat is None or sub_lon is None:
            continue
        if distance_m(lat, lon, _to_float(sub_lat), _to_float(sub_lon)) <= radius_m:
            nearby.append(sub)
    return nearby
