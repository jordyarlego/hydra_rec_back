"""Gravidade do report decidida pelo sistema (nunca pelo usuário).

Fase 3.1 do Ciclo 3: o cidadão não escolhe gravidade. Na criação derivamos
uma gravidade inicial por categoria + chuva APAC; quando há foto, o pipeline
de visão promove a severity_hint da IA a fonte da verdade.
"""
from typing import Optional

_VALID = ("leve", "moderado", "grave")

_BASELINE = {
    "deslizamento":      "grave",
    "alagamento":        "moderado",
    "queda_arvore":      "moderado",
    "poste_caido":       "moderado",
    "via_intransitavel": "moderado",
    "buraco":            "moderado",
    "lixo":              "leve",
    "iluminacao":        "leve",
    "outro":             "moderado",
}

# Chuva forte agrava categorias sensíveis a água
_RAIN_SENSITIVE = ("alagamento", "deslizamento", "via_intransitavel")


def infer_initial_severity(tipo: str, weather_snapshot: Optional[dict]) -> str:
    """Gravidade inicial determinística. Sempre retorna leve|moderado|grave."""
    base = _BASELINE.get(tipo, "moderado")
    rain = 0.0
    if weather_snapshot:
        rain = float(
            weather_snapshot.get("rain_1h_mm")
            or weather_snapshot.get("rain_24h_mm")
            or 0
        )
    if rain >= 30 and tipo in _RAIN_SENSITIVE:
        return "grave"
    return base


def resolve_severity_from_vision(current: str, severity_hint: Optional[str]) -> str:
    """IA é fonte da verdade: usa o hint quando válido, senão mantém o atual."""
    hint = (severity_hint or "").strip().lower()
    if hint in _VALID:
        return hint
    return current
