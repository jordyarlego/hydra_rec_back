import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.traffic import traffic_forecast_multiplier


def test_sem_chuva_fora_rush():
    r = traffic_forecast_multiplier(0, 14)
    assert r["label"] == "FLUIDO"
    assert r["multiplier"] == 1.0


def test_chuva_leve_rush():
    r = traffic_forecast_multiplier(5, 8)
    assert r["label"] in ("MODERADO", "LENTO")
    assert r["multiplier"] > 1.0


def test_chuva_intensa_congestionado():
    r = traffic_forecast_multiplier(25, 18)
    assert r["label"] == "CONGESTIONADO"
    assert r["multiplier"] >= 2.0


def test_estrutura_retorno():
    r = traffic_forecast_multiplier(10, 17)
    assert "multiplier" in r
    assert "label" in r
    assert "extra_minutes_per_10min" in r
    assert r["extra_minutes_per_10min"] >= 0
