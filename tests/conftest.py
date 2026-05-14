import pytest
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient
from main import app


@pytest.fixture
def mock_weather_data():
    hourly_precip = [0.0] * 24 + [1.5] * 24
    return {
        "current": {
            "temperature_2m": 28.0,
            "apparent_temperature": 32.0,
            "relative_humidity_2m": 75,
            "precipitation": 0.0,
            "weather_code": 0,
            "wind_speed_10m": 15.0,
            "wind_direction_10m": 90,
            "wind_gusts_10m": 25.0,
            "surface_pressure": 1012.0,
            "uv_index": 6,
            "is_day": 1,
        },
        "hourly": {
            "time": [f"2026-05-13T{h:02d}:00" for h in range(48)],
            "precipitation": hourly_precip,
            "temperature_2m": [28.0] * 48,
            "weather_code": [0] * 48,
        },
        "daily": {
            "time": [f"2026-05-{13+i}" for i in range(7)],
            "temperature_2m_max": [31.0] * 7,
            "temperature_2m_min": [24.0] * 7,
            "precipitation_probability_max": [20] * 7,
        },
    }


@pytest.fixture
def mock_tide_data():
    return {"height": 1.5, "trend": "Alta"}


@pytest.fixture
async def async_client():
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client
