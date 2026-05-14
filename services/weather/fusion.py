import asyncio
import statistics
import logging
from services.weather.open_meteo import fetch_weather_data
from services.weather.inmet import fetch_inmet_nearest
from services.weather.owm import fetch_owm

logger = logging.getLogger(__name__)


def _extract_open_meteo(weather: dict) -> dict:
    hourly = weather.get("hourly", {}).get("precipitation", [])
    current = weather.get("current", {})
    return {
        "source": "Open-Meteo",
        "rain_next_24h_mm": round(sum(h for h in hourly[24:48] if h is not None), 2),
        "rain_past_24h_mm": round(sum(h for h in hourly[:24] if h is not None), 2),
        "current": current,
    }


async def fetch_weather_consensus(lat: float, lon: float, bairro: str = "") -> dict:
    om_raw, inmet, owm = await asyncio.gather(
        fetch_weather_data(lat, lon),
        fetch_inmet_nearest(lat, lon),
        fetch_owm(lat, lon),
        return_exceptions=True,
    )

    rain_next_values: list[float] = []
    rain_past_values: list[float] = []
    valid_sources: list[str] = []
    current = {}

    if isinstance(om_raw, dict):
        om = _extract_open_meteo(om_raw)
        rain_next_values.append(om["rain_next_24h_mm"])
        rain_past_values.append(om["rain_past_24h_mm"])
        valid_sources.append("Open-Meteo")
        current = om["current"]
    else:
        logger.warning(f"Open-Meteo falhou: {om_raw}")

    if isinstance(inmet, dict):
        rain_past_values.append(min(inmet["rain_last_hour_mm"] * 6, 50))
        valid_sources.append(inmet["source"])

    if isinstance(owm, dict):
        rain_next_values.append(owm["rain_next_24h_mm"])
        valid_sources.append("OpenWeatherMap")

    if not rain_next_values:
        rain_next_values = [0.0]

    mean_next = statistics.mean(rain_next_values)
    mean_past = statistics.mean(rain_past_values) if rain_past_values else 0.0
    stdev_next = statistics.stdev(rain_next_values) if len(rain_next_values) > 1 else 0.0

    if stdev_next < 3:
        confidence = "ALTA"
    elif stdev_next < 8:
        confidence = "MEDIA"
    else:
        confidence = "BAIXA"

    return {
        "rain_next_24h_mm": round(mean_next, 1),
        "rain_past_24h_mm": round(mean_past, 1),
        "rain_next_range": (round(min(rain_next_values), 1), round(max(rain_next_values), 1)),
        "stdev_mm": round(stdev_next, 1),
        "confidence": confidence,
        "sources": valid_sources,
        "sources_count": len(valid_sources),
        "humidity": current.get("relative_humidity_2m", 75),
        "pressure": current.get("surface_pressure", 1013),
        "temperature": current.get("temperature_2m", 28),
        "apparent_temperature": current.get("apparent_temperature", 28),
        "uv_index": current.get("uv_index", 0),
        "wind_speed_kmh": current.get("wind_speed_10m", 0),
        "wind_direction": current.get("wind_direction_10m", 0),
        "weather_code": current.get("weather_code", 0),
        "raw_open_meteo": om_raw if isinstance(om_raw, dict) else None,
    }
