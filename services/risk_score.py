import math
from typing import TypedDict


class RiskBreakdown(TypedDict):
    score: int
    nivel: str
    components: dict
    confidence: str
    raw_values: dict


def calc_rain_points(mm: float, max_pts: float = 50.0) -> float:
    """Curva logística: f(0)=0, f(13.4)≈26, f(60)≈48, plateau perto de max_pts."""
    if mm <= 0:
        return 0.0
    return max_pts * (1.0 - math.exp(-mm / 18.0))


def calculate_risk_score_v2(
    weather_consensus: dict,
    elevation: float,
    tide: dict,
    bairro: str,
    reports_nearby_count: int = 0,
    vulnerability: float | None = None,
) -> RiskBreakdown:
    next_24h = weather_consensus["rain_next_24h_mm"]
    past_24h = weather_consensus["rain_past_24h_mm"]
    humidity  = weather_consensus.get("humidity", 70)
    pressure  = weather_consensus.get("pressure", 1013)
    confidence = weather_consensus.get("confidence", "ALTA")

    if vulnerability is None:
        from data.vulnerability import FLOOD_VULNERABILITY, DEFAULT_VULNERABILITY
        vulnerability = FLOOD_VULNERABILITY.get(bairro, DEFAULT_VULNERABILITY)

    # Chuva é o gatilho principal — sem ela, risco estrutural não se materializa
    has_rain = (next_24h >= 1.0 or past_24h >= 1.0)

    components: dict[str, float] = {}
    components["rain_next"] = calc_rain_points(next_24h, max_pts=50.0)
    components["rain_past"] = calc_rain_points(past_24h, max_pts=25.0)

    # Maré: risco autônomo reduzido sem chuva (storm surge precisa de chuva intensa)
    tide_pts = min(tide["height"] / 3.0, 1.0) * 15.0
    components["tide"] = tide_pts if has_rain else tide_pts * 0.25

    # Vulnerabilidade e altitude amplificam risco pluvial — sem chuva, não contribuem
    components["vulnerability"] = (vulnerability * 15.0) if has_rain else 0.0

    if has_rain:
        if elevation < 5:
            components["altitude"] = 8.0
        elif elevation < 15:
            components["altitude"] = 4.0
        else:
            components["altitude"] = 0.0
    else:
        components["altitude"] = 0.0

    # Instabilidade atmosférica: precursor, mas só pesa quando há chuva prevista
    if has_rain and humidity >= 85 and pressure < 1008:
        components["atmospheric"] = 7.0
    elif has_rain and humidity >= 80 and pressure < 1012:
        components["atmospheric"] = 3.0
    else:
        components["atmospheric"] = 0.0

    # Reports da comunidade: sempre contam (inundação pode ocorrer por drenagem/pipe)
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
            "chuva_prevista_24h":       round(next_24h, 1),
            "chuva_acumulada_24h":      round(past_24h, 1),
            "mare_altura":              tide.get("height"),
            "mare_trend":               tide.get("trend"),
            "umidade":                  humidity,
            "pressao":                  pressure,
            "vulnerabilidade_bairro":   round(vulnerability, 2),
            "reports_comunidade_2km":   reports_nearby_count,
            "altitude_m":               elevation,
        },
    }
