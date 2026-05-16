import math
from typing import TypedDict


class RiskBreakdown(TypedDict):
    score: int
    nivel: str
    components: dict
    confidence: str
    raw_values: dict


def calc_rain_points(mm: float, max_pts: float = 35.0, k: float = 22.0) -> float:
    """
    Curva logística calibrada para Recife.
    Referências:
      5mm/24h  → ~7 pts  (garoa, impacto mínimo)
      11mm/24h → ~14 pts (chuva fraca-moderada, algumas poças)
      25mm/24h → ~25 pts (chuva moderada, atenção em pontos baixos)
      50mm/24h → ~31 pts (chuva forte, risco real de alagamento)
      80mm/24h → ~34 pts (crítico, plateau)
    """
    if mm <= 0:
        return 0.0
    return max_pts * (1.0 - math.exp(-mm / k))


def calculate_risk_score_v2(
    weather_consensus: dict,
    elevation: float,
    tide: dict,
    bairro: str,
    reports_nearby_count: int = 0,
    vulnerability: float | None = None,
    apac_alert_nivel: str | None = None,
) -> RiskBreakdown:
    next_24h   = weather_consensus["rain_next_24h_mm"]
    past_24h   = weather_consensus["rain_past_24h_mm"]
    humidity   = weather_consensus.get("humidity", 70)
    pressure   = weather_consensus.get("pressure", 1013)
    confidence = weather_consensus.get("confidence", "ALTA")

    if vulnerability is None:
        from data.vulnerability import FLOOD_VULNERABILITY, DEFAULT_VULNERABILITY
        vulnerability = FLOOD_VULNERABILITY.get(bairro, DEFAULT_VULNERABILITY)

    # Chuva é o gatilho principal — sem ela, risco estrutural não se materializa
    has_rain = (next_24h >= 1.0 or past_24h >= 1.0)

    components: dict[str, float] = {}

    # ── Chuva prevista: principal fator ─────────────────────────────────
    components["rain_next"] = calc_rain_points(next_24h, max_pts=35.0, k=22.0)

    # ── Chuva acumulada: solo saturado amplia risco, peso menor ─────────
    components["rain_past"] = calc_rain_points(past_24h, max_pts=10.0, k=20.0) if has_rain else 0.0

    # ── Maré: risco autônomo reduzido sem chuva ─────────────────────────
    tide_pts = min(tide["height"] / 3.0, 1.0) * 10.0
    components["tide"] = tide_pts if has_rain else tide_pts * 0.1

    # ── Vulnerabilidade histórica: amplifica risco pluvial ───────────────
    # Reduzido de 8 → 5 pts pra não inflar score em dia limpo
    components["vulnerability"] = (vulnerability * 5.0) if has_rain else 0.0

    # ── Altitude: baixadas acumulam mais (só com chuva) ────────────────
    if has_rain:
        components["altitude"] = 4.0 if elevation < 5 else (2.0 if elevation < 15 else 0.0)
    else:
        components["altitude"] = 0.0

    # ── Instabilidade atmosférica: precursor, só pesa com chuva ─────────
    if has_rain and humidity >= 88 and pressure < 1006:
        components["atmospheric"] = 6.0
    elif has_rain and humidity >= 82 and pressure < 1011:
        components["atmospheric"] = 3.0
    else:
        components["atmospheric"] = 0.0

    # ── Boletim APAC: peso forte do alerta oficial ──────────────────────
    apac_nivel = (apac_alert_nivel or "SEGURO").upper()
    apac_pts = {
        "SEVERO":   30.0,
        "ALTO":     20.0,
        "MODERADO": 10.0,
        "ATENCAO":   3.0,
        "SEGURO":    0.0,
    }
    components["apac_alert"] = apac_pts.get(apac_nivel, 0.0)

    # ── Reports da comunidade: sempre contam ────────────────────────────
    if reports_nearby_count >= 3:
        components["community"] = 10.0
    elif reports_nearby_count >= 1:
        components["community"] = 5.0
    else:
        components["community"] = 0.0

    total = sum(components.values())
    score = min(math.ceil(total), 100)

    if score >= 80:   nivel = "SEVERO"
    elif score >= 65: nivel = "ALTO"
    elif score >= 45: nivel = "MODERADO"
    elif score >= 25: nivel = "ATENCAO"
    else:             nivel = "SEGURO"

    return {
        "score": score,
        "nivel": nivel,
        "version": "v2",
        "components": {k: round(v, 1) for k, v in components.items()},
        "confidence": confidence,
        "raw_values": {
            "chuva_prevista_24h":     round(next_24h, 1),
            "chuva_acumulada_24h":    round(past_24h, 1),
            "mare_altura":            tide.get("height"),
            "mare_trend":             tide.get("trend"),
            "umidade":                humidity,
            "pressao":                pressure,
            "vulnerabilidade_bairro": round(vulnerability, 2),
            "reports_comunidade_2km": reports_nearby_count,
            "altitude_m":             elevation,
        },
    }
