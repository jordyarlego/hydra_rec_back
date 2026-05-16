from __future__ import annotations

from typing import Any


def validate_report(report: dict[str, Any]) -> dict[str, Any]:
    tipo = report.get("type") or report.get("tipo") or "outro"
    desc = (report.get("description") or "").lower()
    photo_desc = (report.get("photo_ai_description") or "").lower()
    weather = report.get("weather") or report.get("weather_snapshot") or {}
    rain_24h = float(weather.get("rain_24h_mm") or 0)
    rain_1h = float(weather.get("rain_1h_mm") or 0)

    score = 0.5
    flags: list[str] = []
    notes: list[str] = []

    combined = f"{desc} {photo_desc}"
    if tipo in combined:
        score += 0.2
        notes.append("Descrição textual combina com o tipo declarado.")

    if tipo == "alagamento":
        if rain_1h >= 5 or rain_24h >= 15:
            score += 0.25
            notes.append("Chuva APAC compatível com alagamento.")
        elif not any(w in combined for w in ("água", "agua", "alag", "enchente", "poça")):
            score -= 0.35
            flags.append("alagamento_sem_chuva_ou_foto")
            notes.append("Pouca chuva APAC e foto/descrição não indicam água.")

    if tipo in ("queda_arvore", "poste_caido") and float(weather.get("wind_kmh") or 0) >= 45:
        score += 0.15
        notes.append("Vento APAC aumenta coerência da ocorrência.")

    if photo_desc and any(word in photo_desc for word in tipo.split("_")):
        score += 0.2
        notes.append("Foto descrita pela IA reforça o tipo informado.")

    if report.get("photo_ai_confidence") is not None:
        score += (float(report.get("photo_ai_confidence") or 0) - 0.5) * 0.2

    score = max(0.0, min(round(score, 2), 1.0))
    return {
        "score": score,
        "notes": " ".join(notes) or "Validação heurística aplicada.",
        "flags": flags,
    }


async def persist_validation(report_id: str, report: dict[str, Any]) -> dict[str, Any]:
    result = validate_report(report)
    from services.supabase_client import get_service_client
    client = get_service_client()
    client.table("reports").update({
        "ai_validation_score": result["score"],
        "ai_validation_notes": result["notes"],
    }).eq("id", report_id).execute()
    return result
