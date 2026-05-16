from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# Thresholds (alinhados ao spec docs/superpowers/specs/2026-05-16-triagem-v2-design.md)
GATE_NOT_URBAN_MAX_SCORE = 0.10        # foto explicitamente não-urbana
GATE_LOW_CONFIDENCE_MAX_SCORE = 0.15   # foto com confidence < threshold
GATE_LOW_CONFIDENCE_THRESHOLD = 0.40

BUCKET_FILTRADO_MAX = 0.20             # < 20% -> filtrado
BUCKET_AUTOVAL_MIN = 0.75              # >= 75% + prioridade alta + sem reincidência -> auto-validado


def _bucket_from_score(score: float, priority: str | None = None, recurrence: int = 0) -> str:
    """Classifica o report no bucket de triagem.

    - filtrado:       score < 0.20 (provavel spam/foto invalida)
    - auto_validado:  score >= 0.75 + prioridade urgente/alta + sem reincidência
    - revisar:        zona cinzenta — admin precisa decidir
    """
    if score < BUCKET_FILTRADO_MAX:
        return "filtrado"
    if (
        score >= BUCKET_AUTOVAL_MIN
        and priority in ("alta", "urgente")
        and recurrence == 0
    ):
        return "auto_validado"
    return "revisar"


def validate_report(report: dict[str, Any]) -> dict[str, Any]:
    """
    Aplica gates de qualidade da IA, depois score heurístico.
    Retorna {score, notes, flags, bucket_hint}.
    """
    tipo = report.get("type") or report.get("tipo") or "outro"
    desc = (report.get("description") or "").lower()
    photo_desc = (report.get("photo_ai_description") or "").lower()
    weather = report.get("weather") or report.get("weather_snapshot") or {}
    rain_24h = float(weather.get("rain_24h_mm") or 0)
    rain_1h = float(weather.get("rain_1h_mm") or 0)

    has_photo = bool(report.get("photo_url"))
    is_urban = report.get("photo_ai_is_urban_problem")  # True | False | None
    photo_conf = report.get("photo_ai_confidence")

    # ───── Gate 1: foto explicitamente não é problema urbano ─────
    if has_photo and is_urban is False:
        return {
            "score": GATE_NOT_URBAN_MAX_SCORE,
            "notes": "Foto não corresponde a problema urbano reconhecível pela IA.",
            "flags": ["nao_urbano"],
            "bucket_hint": "filtrado",
        }

    # ───── Gate 2: foto com confidence baixíssima ─────
    if has_photo and photo_conf is not None and float(photo_conf) < GATE_LOW_CONFIDENCE_THRESHOLD:
        return {
            "score": GATE_LOW_CONFIDENCE_MAX_SCORE,
            "notes": "IA não reconheceu o conteúdo da foto com segurança suficiente.",
            "flags": ["baixa_confianca_foto"],
            "bucket_hint": "filtrado",
        }

    # ───── Heurística (passou pelos gates) ─────
    # Base reduzida quando sem foto: texto sozinho vale menos
    score = 0.5 if has_photo else 0.4
    flags: list[str] = []
    notes: list[str] = []

    combined = f"{desc} {photo_desc}"

    if tipo in combined:
        score += 0.10   # antes era 0.20; reduzido pra não inflar score com 1 palavra solta
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
        score += 0.15   # antes 0.20
        notes.append("Foto descrita pela IA reforça o tipo informado.")

    # Bonus reduzido por confidence — só se já passou dos gates
    if photo_conf is not None:
        score += (float(photo_conf) - 0.5) * 0.15   # antes 0.2

    # Penalidade: tipo "outro" com foto = categoria genérica sem evidência
    if has_photo and tipo == "outro":
        score -= 0.20
        flags.append("tipo_generico_com_foto")
        notes.append("Categoria 'outro' com foto: descrição não bate com categoria específica.")

    # Quando is_urban=True explícito, bonus pequeno
    if has_photo and is_urban is True:
        score += 0.05

    score = max(0.0, min(round(score, 2), 1.0))

    # Bucket hint usa só o score por padrão; quem chama pode refinar com priority/recurrence
    return {
        "score": score,
        "notes": " ".join(notes) or "Sem evidência adicional além dos dados básicos do report.",
        "flags": flags,
        "bucket_hint": _bucket_from_score(score),
    }


async def persist_validation(report_id: str, report: dict[str, Any]) -> dict[str, Any]:
    """
    Persiste score + bucket. Bucket usa priority/recurrence do report (se vierem).
    """
    result = validate_report(report)

    # Refina bucket usando priority + recurrence se disponíveis
    priority_obj = report.get("priority_result") or {}
    priority = priority_obj.get("priority") if isinstance(priority_obj, dict) else None
    recurrence = int((report.get("recurrence_score") or 0))
    bucket = _bucket_from_score(result["score"], priority=priority, recurrence=recurrence)
    result["bucket"] = bucket

    from services.supabase_client import get_service_client
    client = get_service_client()

    # Tenta com bucket; cai pra sem-bucket se coluna não existe (V4 não aplicada)
    payload = {
        "ai_validation_score": result["score"],
        "ai_validation_notes": result["notes"],
    }
    try:
        client.table("reports").update({**payload, "bucket": bucket}).eq("id", report_id).execute()
    except Exception as col_err:
        logger.warning("bucket column missing (V4 not applied?): %s", col_err)
        client.table("reports").update(payload).eq("id", report_id).execute()

    return result
