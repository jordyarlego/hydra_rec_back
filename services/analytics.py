"""Motor analítico cívico — agrega tendências dos reports e gera recomendações.

Regras determinísticas, sem chamada externa. A IA (Gemini) só narra o
resultado em services/ai_recommender.py. Fonte da verdade = estas regras.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Optional

# Categorias de alto risco para priorização das recomendações
_HIGH_RISK = ("deslizamento", "alagamento", "poste_caido")

# Volume mínimo numa janela para uma combinação (bairro, categoria) virar tendência
_RISING_MIN = 3


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """ISO string → datetime aware (UTC). Tolera sufixo 'Z'."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def aggregate_trends(reports: list[dict], now: datetime, window_hours: int = 24) -> dict:
    """Agrega contagens da janela atual vs janela anterior e detecta altas."""
    win = timedelta(hours=window_hours)
    cur_start = now - win
    prior_start = now - 2 * win

    current: list[dict] = []
    prior: list[dict] = []
    for r in reports:
        dt = _parse_dt(r.get("created_at"))
        if dt is None:
            continue
        if dt >= cur_start:
            current.append(r)
        elif dt >= prior_start:
            prior.append(r)

    cat_counts = Counter(r.get("type") for r in current if r.get("type"))
    bairro_counts = Counter(r.get("bairro") for r in current if r.get("bairro"))

    # (bairro, categoria) atual vs anterior
    cur_pairs = Counter((r.get("bairro"), r.get("type")) for r in current if r.get("bairro") and r.get("type"))
    prior_pairs = Counter((r.get("bairro"), r.get("type")) for r in prior if r.get("bairro") and r.get("type"))

    rising = []
    for (bairro, category), cur_n in cur_pairs.items():
        prior_n = prior_pairs.get((bairro, category), 0)
        if cur_n >= _RISING_MIN and cur_n > prior_n:
            rising.append({
                "bairro": bairro, "category": category,
                "current": cur_n, "prior": prior_n, "delta": cur_n - prior_n,
            })
    rising.sort(key=lambda e: (-e["delta"], -e["current"], e["bairro"], e["category"]))

    return {
        "window_hours": window_hours,
        "current_total": len(current),
        "prior_total": len(prior),
        "by_category": [{"category": c, "count": n} for c, n in cat_counts.most_common()],
        "by_bairro": [{"bairro": b, "count": n} for b, n in bairro_counts.most_common()],
        "rising": rising,
    }


# Ação recomendada por categoria (texto base; o bairro entra por formatação)
_ACTION_BY_CATEGORY = {
    "alagamento":        "Acionar Defesa Civil e monitorar pontos de alagamento em {bairro}",
    "deslizamento":      "Inspeção de encosta e alerta de Defesa Civil em {bairro}",
    "poste_caido":       "Acionar concessionária de energia em {bairro} (risco elétrico)",
    "queda_arvore":      "Acionar EMLURB para remoção de árvore em {bairro}",
    "via_intransitavel": "Avaliar interdição/desvio de tráfego em {bairro}",
    "buraco":            "Encaminhar tapa-buraco para {bairro}",
    "iluminacao":        "Acionar manutenção de iluminação em {bairro}",
    "lixo":              "Acionar coleta/limpeza urbana em {bairro}",
    "outro":             "Avaliar ocorrências reportadas em {bairro}",
}


def _priority_for(current: int, category: str) -> str:
    if current >= 5:
        return "urgente"
    if current >= _RISING_MIN:
        return "alta" if category in _HIGH_RISK else "media"
    return "media"


def build_recommendations(trends: dict, now: datetime) -> list[dict]:
    """Recomendações determinísticas. Cada uma carrega a regra (cause) que disparou."""
    window = trends.get("window_hours", 24)
    recs: list[dict] = []

    for e in trends.get("rising", []):
        bairro, category = e["bairro"], e["category"]
        action_tpl = _ACTION_BY_CATEGORY.get(category, _ACTION_BY_CATEGORY["outro"])
        recs.append({
            "id": f"{category}:{bairro}",
            "scope": {"bairro": bairro, "category": category},
            "action": action_tpl.format(bairro=bairro),
            "cause": (
                f"{e['current']} reports de {category} em {bairro} "
                f"nas últimas {window}h (janela anterior: {e['prior']})"
            ),
            "priority": _priority_for(e["current"], category),
        })

    # Pico de volume geral da cidade
    cur, prior = trends.get("current_total", 0), trends.get("prior_total", 0)
    if cur >= 10 and prior > 0 and cur >= 1.5 * prior:
        pct = round((cur / prior - 1) * 100)
        recs.append({
            "id": "cidade:volume",
            "scope": "cidade",
            "action": "Reforçar plantão operacional — volume acima do normal na cidade",
            "cause": (
                f"{cur} reports nas últimas {window}h vs {prior} na janela anterior "
                f"(+{pct}%)"
            ),
            "priority": "alta",
        })

    return recs
