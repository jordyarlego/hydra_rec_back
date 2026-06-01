from datetime import datetime, timezone

from services.report_validation import (
    calculate_validation_verdict,
    filter_nearby_subscriptions,
    validation_deadline_from,
)


def test_deadline_default_quinze_minutos():
    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

    assert validation_deadline_from(now) == "2026-06-01T12:15:00+00:00"


def test_veredito_confirmado_com_ia_chuva_e_confirmacoes():
    result = calculate_validation_verdict({
        "type": "alagamento",
        "photo_url": "https://example/report.jpg",
        "photo_ai_is_urban_problem": True,
        "ai_validation_score": 0.82,
        "likes_up": 3,
        "likes_down": 0,
        "confirmed_count": 2,
        "weather": {"rain_1h_mm": 12, "rain_24h_mm": 35},
    })

    assert result["status"] == "confirmado"
    assert result["score"] >= 75
    assert "chuva APAC/CEMADEN compatível" in result["summary"]


def test_veredito_suspeito_com_foto_nao_urbana_e_votos_negativos():
    result = calculate_validation_verdict({
        "type": "alagamento",
        "photo_url": "https://example/fake.jpg",
        "photo_ai_is_urban_problem": False,
        "ai_validation_score": 0.1,
        "likes_up": 0,
        "likes_down": 4,
        "confirmed_count": 0,
        "weather": {"rain_1h_mm": 0, "rain_24h_mm": 0},
    })

    assert result["status"] == "suspeito"
    assert result["score"] < 35
    assert "foto não parece problema urbano" in result["summary"]


def test_filtra_subscriptions_proximas_por_raio():
    subs = [
        {"endpoint": "a", "lat": -8.056, "lon": -34.889},
        {"endpoint": "b", "lat": -8.11, "lon": -34.95},
        {"endpoint": "sem-geo"},
    ]

    nearby = filter_nearby_subscriptions(subs, -8.0568, -34.8891, radius_m=500)

    assert [s["endpoint"] for s in nearby] == ["a"]
