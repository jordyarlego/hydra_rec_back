import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from services.risk_score import calculate_risk_score_v2, calc_rain_points


# ── Curva logística ────────────────────────────────────────────────────────────

def test_calc_rain_zero():
    assert calc_rain_points(0) == 0.0
    assert calc_rain_points(-5) == 0.0


def test_calc_rain_curve_shape():
    assert 11 <= calc_rain_points(5) <= 13
    assert 21 <= calc_rain_points(10) <= 23
    assert 25 <= calc_rain_points(13.4) <= 28
    assert 27 <= calc_rain_points(15) <= 30
    assert 39 <= calc_rain_points(30) <= 42
    assert 47 <= calc_rain_points(60) <= 50


def test_calc_rain_plateau():
    """Score não ultrapassa max_pts."""
    assert calc_rain_points(1000) <= 50.0
    assert calc_rain_points(1000, max_pts=25.0) <= 25.0


# ── Caso do professor (critério principal do feedback) ────────────────────────

def test_caso_professor_13_4mm_jordao():
    """13.4mm em Jordão (vuln 0.85) NÃO pode ser SEGURO. Era o bug do v1."""
    result = calculate_risk_score_v2(
        weather_consensus={
            "rain_next_24h_mm": 13.4,
            "rain_past_24h_mm": 0,
            "humidity": 75,
            "pressure": 1012,
            "confidence": "ALTA",
        },
        elevation=8.0,
        tide={"height": 1.5, "trend": "Alta"},
        bairro="Jordão",
        reports_nearby_count=0,
    )
    assert result["nivel"] in ("ATENCAO", "MODERADO", "ALTO", "SEVERO"), (
        f"13.4mm em Jordão classificado como {result['nivel']} — inaceitável!"
    )
    assert result["score"] >= 35, f"Score muito baixo para 13.4mm: {result['score']}"


# ── Cenários extremos ──────────────────────────────────────────────────────────

def test_sem_chuva_sem_risco():
    result = calculate_risk_score_v2(
        weather_consensus={
            "rain_next_24h_mm": 0,
            "rain_past_24h_mm": 0,
            "humidity": 60,
            "pressure": 1015,
            "confidence": "ALTA",
        },
        elevation=20,
        tide={"height": 1.0, "trend": "Baixa"},
        bairro="Casa Forte",
        reports_nearby_count=0,
    )
    assert result["nivel"] == "SEGURO"
    assert result["score"] < 25


def test_chuva_extrema_severo():
    result = calculate_risk_score_v2(
        weather_consensus={
            "rain_next_24h_mm": 80,
            "rain_past_24h_mm": 40,
            "humidity": 95,
            "pressure": 1003,
            "confidence": "ALTA",
        },
        elevation=3,
        tide={"height": 2.5, "trend": "Alta"},
        bairro="Brasília Teimosa",
        reports_nearby_count=5,
    )
    assert result["nivel"] == "SEVERO"
    assert result["score"] >= 80


# ── Componentes isolados ───────────────────────────────────────────────────────

def test_reports_comunidade_eleva_score():
    base_args = dict(
        weather_consensus={
            "rain_next_24h_mm": 8,
            "rain_past_24h_mm": 0,
            "humidity": 70,
            "pressure": 1014,
            "confidence": "ALTA",
        },
        elevation=10,
        tide={"height": 1.2, "trend": "Baixa"},
        bairro="Boa Viagem",
    )
    sem_reports = calculate_risk_score_v2(**base_args, reports_nearby_count=0)
    com_reports = calculate_risk_score_v2(**base_args, reports_nearby_count=4)
    assert com_reports["score"] - sem_reports["score"] >= 10


def test_score_nao_ultrapassa_100():
    result = calculate_risk_score_v2(
        weather_consensus={
            "rain_next_24h_mm": 999,
            "rain_past_24h_mm": 999,
            "humidity": 100,
            "pressure": 990,
            "confidence": "ALTA",
        },
        elevation=0,
        tide={"height": 3.5, "trend": "Alta"},
        bairro="Brasília Teimosa",
        reports_nearby_count=100,
    )
    assert result["score"] <= 100


def test_version_v2():
    result = calculate_risk_score_v2(
        weather_consensus={"rain_next_24h_mm": 5, "rain_past_24h_mm": 0,
                           "humidity": 70, "pressure": 1013, "confidence": "ALTA"},
        elevation=10, tide={"height": 1.2, "trend": "Baixa"},
        bairro="Derby",
    )
    assert result["version"] == "v2"
    assert "rain_next" in result["components"]
    assert "soil" not in str(result["components"])  # solo removido
