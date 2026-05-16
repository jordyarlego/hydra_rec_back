"""
Motor de priorização cívica para EMPREL / Prefeitura do Recife.

Combina: severidade declarada, likes, IA de validação, chuva APAC,
recorrência de chamados oficiais e tipo do problema para gerar um
score 0-100 e uma prioridade legível.

Não chama nenhum serviço externo — apenas processa dicts já hidratados.
"""
from __future__ import annotations
from typing import Optional


# ── Pesos ─────────────────────────────────────────────────────────────────

_SEVERITY_SCORES = {
    "leve":      5,
    "moderado": 15,
    "grave":    30,
}

_TYPE_BASE = {
    "deslizamento":      35,  # risco de vida
    "alagamento":        30,
    "poste_caido":       28,
    "via_intransitavel": 20,
    "queda_arvore":      18,
    "buraco":            12,
    "iluminacao":        10,
    "lixo":               6,
    "outro":              8,
}

_PRIORITY_THRESHOLDS = [
    (75, "urgente"),
    (50, "alta"),
    (25, "media"),
    (0,  "baixa"),
]


# ── Helpers ───────────────────────────────────────────────────────────────

def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _score_to_priority(score: float) -> str:
    for threshold, label in _PRIORITY_THRESHOLDS:
        if score >= threshold:
            return label
    return "baixa"


# ── API pública ───────────────────────────────────────────────────────────

def calculate_priority(
    report: dict,
    weather_snapshot: Optional[dict] = None,
    official_crossing: Optional[dict] = None,
) -> dict:
    """
    Calcula prioridade operacional de um report.

    Parâmetros:
        report             — campos do report (tipo, severidade, likes_up, likes_down,
                             ai_validation_score, status)
        weather_snapshot   — snapshot APAC cruzado no momento do report
                             {rain_1h_mm, rain_24h_mm, wind_kmh, ...}
        official_crossing  — cruzamento com dados oficiais
                             {recurrence_score, nearest_official_request_type, ...}

    Retorno:
        {priority, score, reasons}
    """
    score = 0.0
    reasons: list[str] = []

    tipo = report.get("tipo", "outro")
    severidade = report.get("severidade", "moderado")

    # 1. Base por tipo de ocorrência
    type_pts = _TYPE_BASE.get(tipo, 8)
    score += type_pts
    if type_pts >= 28:
        reasons.append(f"Tipo de alto risco ({tipo})")

    # 2. Severidade declarada pelo usuário
    sev_pts = _SEVERITY_SCORES.get(severidade, 15)
    score += sev_pts
    if severidade == "grave":
        reasons.append("Usuário reportou severidade grave")

    # 3. Likes líquidos (up - down)
    likes_up = report.get("likes_up") or 0
    likes_down = report.get("likes_down") or 0
    net_likes = likes_up - likes_down
    if net_likes >= 5:
        pts = min(15, net_likes * 2)
        score += pts
        reasons.append(f"Múltiplos confirmaram ({likes_up} likes positivos)")
    elif net_likes <= -3:
        score -= 10
        reasons.append("Usuários questionaram o report")

    # 4. Score de validação por IA
    ai_score = report.get("ai_validation_score")
    has_photo = bool(report.get("photo_url") or report.get("photo_ai_description") or report.get("photo_ai_confidence") is not None)
    if ai_score is not None:
        if ai_score >= 0.75:
            score += 15
            reasons.append("Evidências do report têm alta coerência" if not has_photo else "Foto validada pela IA com alta confiança")
        elif ai_score >= 0.5:
            score += 7
            reasons.append("Report parcialmente coerente com os dados disponíveis" if not has_photo else "Foto parcialmente validada pela IA")
        elif ai_score < 0.25:
            score -= 15
            reasons.append("IA detectou possível inconsistência no report")

    # 5. Clima APAC cruzado
    if weather_snapshot:
        rain_1h = weather_snapshot.get("rain_1h_mm") or 0
        rain_24h = weather_snapshot.get("rain_24h_mm") or 0
        wind = weather_snapshot.get("wind_kmh") or 0

        if rain_1h >= 20:
            score += 12
            reasons.append(f"Chuva forte registrada pela APAC ({rain_1h:.1f} mm/h)")
        elif rain_1h >= 5:
            score += 6
            reasons.append(f"Chuva registrada pela APAC ({rain_1h:.1f} mm/h)")

        if rain_24h >= 50:
            score += 8
            reasons.append(f"Acumulado de 24h alto ({rain_24h:.1f} mm)")

        if wind >= 60 and tipo in ("deslizamento", "queda_arvore", "poste_caido"):
            score += 8
            reasons.append(f"Vento forte ({wind:.0f} km/h) agrava o tipo de ocorrência")

    # 6. Recorrência por dados oficiais
    if official_crossing:
        recurrence = official_crossing.get("recurrence_score") or 0
        nearest_type = official_crossing.get("nearest_official_request_type")

        if recurrence >= 3:
            pts = min(12, recurrence * 2)
            score += pts
            reasons.append(f"Histórico de chamados similares na área (score {recurrence:.1f})")
        elif recurrence >= 1:
            score += 4
            reasons.append("Chamado(s) oficial(is) similar(es) já registrado(s) próximo")

        if nearest_type:
            reasons.append(f"Chamado EMLURB/Defesa Civil relacionado: {nearest_type}")

    # 7. Status influencia: validated sobe, flagged desce
    status = report.get("status", "pending")
    if status == "validated":
        score += 5
    elif status == "flagged":
        score -= 20
        reasons.append("Report marcado como duvidoso")

    final_score = int(_clamp(score))
    priority = _score_to_priority(final_score)

    if not reasons:
        reasons.append("Ocorrência reportada pela comunidade")

    return {
        "priority": priority,
        "score": final_score,
        "reasons": reasons,
    }


def batch_prioritize(reports: list[dict], snapshots: dict = None, crossings: dict = None) -> list[dict]:
    """
    Prioriza uma lista de reports e retorna em ordem decrescente de score.
    snapshots e crossings são dicts keyed por report_id.
    """
    snapshots = snapshots or {}
    crossings = crossings or {}
    results = []
    for report in reports:
        rid = str(report.get("id", ""))
        result = calculate_priority(
            report,
            weather_snapshot=snapshots.get(rid),
            official_crossing=crossings.get(rid),
        )
        results.append({**report, "priority_result": result})
    results.sort(key=lambda r: r["priority_result"]["score"], reverse=True)
    return results
