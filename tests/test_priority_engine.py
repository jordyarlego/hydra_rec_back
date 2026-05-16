"""
Testes para priority_engine — sem I/O externo.
"""
import pytest
from services.priority_engine import calculate_priority, batch_prioritize


def _base_report(**overrides):
    return {
        "tipo": "buraco",
        "severidade": "moderado",
        "likes_up": 0,
        "likes_down": 0,
        "ai_validation_score": None,
        "status": "pending",
        **overrides,
    }


# ── calculate_priority ─────────────────────────────────────────────────────

def test_score_range():
    result = calculate_priority(_base_report())
    assert 0 <= result["score"] <= 100
    assert result["priority"] in ("baixa", "media", "alta", "urgente")
    assert isinstance(result["reasons"], list)
    assert len(result["reasons"]) >= 1


def test_deslizamento_scores_higher_than_lixo():
    r_desl = calculate_priority(_base_report(tipo="deslizamento"))
    r_lixo = calculate_priority(_base_report(tipo="lixo"))
    assert r_desl["score"] > r_lixo["score"]


def test_grave_scores_higher_than_leve():
    r_grave = calculate_priority(_base_report(severidade="grave"))
    r_leve = calculate_priority(_base_report(severidade="leve"))
    assert r_grave["score"] > r_leve["score"]


def test_many_likes_boost_score():
    r_liked = calculate_priority(_base_report(likes_up=10, likes_down=0))
    r_base = calculate_priority(_base_report())
    assert r_liked["score"] > r_base["score"]


def test_negative_likes_reduce_score():
    r_neg = calculate_priority(_base_report(likes_up=0, likes_down=5))
    r_base = calculate_priority(_base_report())
    assert r_neg["score"] < r_base["score"]


def test_high_ai_score_boosts():
    r_ai = calculate_priority(_base_report(ai_validation_score=0.9))
    r_base = calculate_priority(_base_report())
    assert r_ai["score"] > r_base["score"]


def test_low_ai_score_penalizes():
    r_ai = calculate_priority(_base_report(ai_validation_score=0.1))
    r_base = calculate_priority(_base_report())
    assert r_ai["score"] < r_base["score"]


def test_weather_rain_boosts_alagamento():
    r_wet = calculate_priority(
        _base_report(tipo="alagamento"),
        weather_snapshot={"rain_1h_mm": 25.0, "rain_24h_mm": 60.0, "wind_kmh": 10.0},
    )
    r_dry = calculate_priority(_base_report(tipo="alagamento"))
    assert r_wet["score"] > r_dry["score"]
    reasons_text = " ".join(r_wet["reasons"])
    assert "APAC" in reasons_text


def test_strong_wind_boosts_queda_arvore():
    r_wind = calculate_priority(
        _base_report(tipo="queda_arvore"),
        weather_snapshot={"rain_1h_mm": 0, "rain_24h_mm": 0, "wind_kmh": 70.0},
    )
    r_calm = calculate_priority(_base_report(tipo="queda_arvore"))
    assert r_wind["score"] > r_calm["score"]


def test_recurrence_boosts_score():
    crossing = {"recurrence_score": 5.0, "nearest_official_request_type": "tapa-buracos"}
    r_recur = calculate_priority(_base_report(), official_crossing=crossing)
    r_base = calculate_priority(_base_report())
    assert r_recur["score"] > r_base["score"]
    reasons_text = " ".join(r_recur["reasons"])
    assert "tapa-buracos" in reasons_text


def test_flagged_report_penalized():
    r_flag = calculate_priority(_base_report(status="flagged"))
    r_pend = calculate_priority(_base_report(status="pending"))
    assert r_flag["score"] < r_pend["score"]


def test_validated_report_bonus():
    r_val = calculate_priority(_base_report(status="validated"))
    r_pend = calculate_priority(_base_report(status="pending"))
    assert r_val["score"] >= r_pend["score"]


def test_urgente_threshold():
    r = calculate_priority(
        _base_report(tipo="deslizamento", severidade="grave", likes_up=10, ai_validation_score=0.9),
        weather_snapshot={"rain_1h_mm": 30.0, "rain_24h_mm": 80.0, "wind_kmh": 65.0},
        official_crossing={"recurrence_score": 6.0, "nearest_official_request_type": "barreira"},
    )
    assert r["priority"] == "urgente"


# ── batch_prioritize ───────────────────────────────────────────────────────

def test_batch_prioritize_sorted():
    reports = [
        {**_base_report(tipo="lixo"), "id": "a"},
        {**_base_report(tipo="deslizamento", severidade="grave"), "id": "b"},
        {**_base_report(tipo="buraco"), "id": "c"},
    ]
    result = batch_prioritize(reports)
    scores = [r["priority_result"]["score"] for r in result]
    assert scores == sorted(scores, reverse=True)
    assert result[0]["id"] == "b"
