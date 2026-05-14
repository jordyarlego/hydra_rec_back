import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import statistics
from unittest.mock import AsyncMock, patch
import pytest


def _make_consensus(rain_next_vals: list[float], rain_past_vals: list[float] = None):
    """Simula o comportamento do fusion manualmente para testes unitários."""
    rain_past_vals = rain_past_vals or [0.0]
    mean_next = statistics.mean(rain_next_vals)
    stdev_next = statistics.stdev(rain_next_vals) if len(rain_next_vals) > 1 else 0.0
    if stdev_next < 3:
        confidence = "ALTA"
    elif stdev_next < 8:
        confidence = "MEDIA"
    else:
        confidence = "BAIXA"
    return {
        "rain_next_24h_mm": round(mean_next, 1),
        "rain_past_24h_mm": round(statistics.mean(rain_past_vals), 1),
        "stdev_mm": round(stdev_next, 1),
        "confidence": confidence,
        "sources_count": len(rain_next_vals),
    }


def test_fusion_alta_confianca():
    """Fontes concordando dentro de 3mm → ALTA."""
    r = _make_consensus([18.0, 19.0, 20.0])
    assert r["confidence"] == "ALTA"


def test_fusion_media_confianca():
    """Stdev entre 3 e 8 → MEDIA."""
    r = _make_consensus([10.0, 15.0])
    assert r["confidence"] == "MEDIA"


def test_fusion_baixa_confianca():
    """Fontes discordando >8mm → BAIXA."""
    r = _make_consensus([5.0, 28.0])
    assert r["confidence"] == "BAIXA"


def test_fusion_media_correta():
    r = _make_consensus([10.0, 20.0, 30.0])
    assert r["rain_next_24h_mm"] == 20.0


def test_fusion_fontes_count():
    r = _make_consensus([10.0, 12.0])
    assert r["sources_count"] == 2


@pytest.mark.asyncio
async def test_fusion_uma_fonte_falha():
    """INMET falha → fusion continua com Open-Meteo + OWM."""
    om_data = {
        "hourly": {"precipitation": [0.0] * 24 + [1.5] * 24},
        "current": {"temperature_2m": 28, "relative_humidity_2m": 75,
                    "surface_pressure": 1013, "uv_index": 5,
                    "apparent_temperature": 32, "wind_speed_10m": 10,
                    "wind_direction_10m": 90, "weather_code": 0},
    }
    owm_data = {"source": "OpenWeatherMap", "rain_next_24h_mm": 38.0,
                "temp_c": 28, "humidity": 75, "pressure": 1013}

    with patch("services.weather.fusion.fetch_weather_data", new=AsyncMock(return_value=om_data)), \
         patch("services.weather.fusion.fetch_inmet_nearest", new=AsyncMock(return_value=None)), \
         patch("services.weather.fusion.fetch_owm", new=AsyncMock(return_value=owm_data)):
        from services.weather.fusion import fetch_weather_consensus
        result = await fetch_weather_consensus(-8.11, -34.90, "Boa Viagem")

    assert result["sources_count"] >= 1
    assert result["rain_next_24h_mm"] >= 0
    assert "Open-Meteo" in result["sources"]
    assert result["confidence"] in ("ALTA", "MEDIA", "BAIXA")
