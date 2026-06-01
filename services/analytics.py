"""Motor analítico cívico — agrega tendências dos reports e gera recomendações.

Regras determinísticas, sem chamada externa. A IA (Gemini) só narra o
resultado em services/ai_recommender.py. Fonte da verdade = estas regras.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
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
    from datetime import timedelta
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
    rising.sort(key=lambda e: e["delta"], reverse=True)

    return {
        "window_hours": window_hours,
        "current_total": len(current),
        "prior_total": len(prior),
        "by_category": [{"category": c, "count": n} for c, n in cat_counts.most_common()],
        "by_bairro": [{"bairro": b, "count": n} for b, n in bairro_counts.most_common()],
        "rising": rising,
    }
