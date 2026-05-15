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


def _avg(values: list[float]) -> float:
    return round(statistics.mean(values), 1) if values else 0.0


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
    failed_sources: list[dict] = []
    current = {}

    # Humidity, wind, pressure per source for enriched metrics
    humidity_by_src: dict[str, float] = {}
    wind_by_src:     dict[str, float] = {}
    pressure_by_src: dict[str, float] = {}
    temp_by_src:     dict[str, float] = {}

    if isinstance(om_raw, dict):
        om = _extract_open_meteo(om_raw)
        rain_next_values.append(om["rain_next_24h_mm"])
        rain_past_values.append(om["rain_past_24h_mm"])
        valid_sources.append("Open-Meteo")
        current = om["current"]
        if current:
            humidity_by_src["Open-Meteo"] = current.get("relative_humidity_2m", 0)
            wind_by_src["Open-Meteo"]     = current.get("wind_speed_10m", 0)
            pressure_by_src["Open-Meteo"] = current.get("surface_pressure", 0)
            temp_by_src["Open-Meteo"]     = current.get("temperature_2m", 0)
    else:
        err = type(om_raw).__name__ if isinstance(om_raw, Exception) else "resposta inválida"
        logger.warning(f"Open-Meteo falhou: {om_raw}")
        failed_sources.append({"source": "Open-Meteo", "reason": err})

    if isinstance(inmet, dict):
        rain_past_values.append(min(inmet["rain_last_hour_mm"] * 6, 50))
        valid_sources.append(inmet["source"])
        humidity_by_src[inmet["source"]] = inmet.get("humidity", 0)
        wind_by_src[inmet["source"]]     = inmet.get("wind_speed_kmh", 0)
        pressure_by_src[inmet["source"]] = inmet.get("pressure", 0)
        temp_by_src[inmet["source"]]     = inmet.get("temp_c", 0)
    else:
        err = type(inmet).__name__ if isinstance(inmet, Exception) else "sem dados"
        failed_sources.append({"source": "INMET", "reason": "estação indisponível"})

    if isinstance(owm, dict):
        rain_next_values.append(owm["rain_next_24h_mm"])
        valid_sources.append("OpenWeatherMap")
        humidity_by_src["OpenWeatherMap"] = owm.get("humidity", 0)
        pressure_by_src["OpenWeatherMap"] = owm.get("pressure", 0)
        temp_by_src["OpenWeatherMap"]     = owm.get("temp_c", 0)
    else:
        err = type(owm).__name__ if isinstance(owm, Exception) else "sem dados"
        failed_sources.append({"source": "OpenWeatherMap", "reason": "API indisponível"})

    if not rain_next_values:
        rain_next_values = [0.0]

    mean_next  = statistics.mean(rain_next_values)
    mean_past  = statistics.mean(rain_past_values) if rain_past_values else 0.0
    stdev_next = statistics.stdev(rain_next_values) if len(rain_next_values) > 1 else 0.0

    if stdev_next < 3:
        confidence = "ALTA"
    elif stdev_next < 8:
        confidence = "MEDIA"
    else:
        confidence = "BAIXA"

    # Consensus metrics (average across available sources)
    humidity_vals = [v for v in humidity_by_src.values() if v > 0]
    wind_vals     = [v for v in wind_by_src.values()     if v >= 0]
    pressure_vals = [v for v in pressure_by_src.values() if v > 0]
    temp_vals     = [v for v in temp_by_src.values()     if v != 0]

    humidity_consensus = _avg(humidity_vals) if humidity_vals else current.get("relative_humidity_2m", 75)
    wind_consensus     = _avg(wind_vals)     if wind_vals     else current.get("wind_speed_10m", 0)
    pressure_consensus = _avg(pressure_vals) if pressure_vals else current.get("surface_pressure", 1013)
    temp_consensus     = _avg(temp_vals)     if temp_vals     else current.get("temperature_2m", 28)

    return {
        "rain_next_24h_mm":   round(mean_next, 1),
        "rain_past_24h_mm":   round(mean_past, 1),
        "rain_next_range":    (round(min(rain_next_values), 1), round(max(rain_next_values), 1)),
        "stdev_mm":           round(stdev_next, 1),
        "confidence":         confidence,
        "sources":            valid_sources,
        "sources_used":       valid_sources,
        "sources_count":      len(valid_sources),
        "sources_attempted":  ["Open-Meteo", "INMET", "OpenWeatherMap"],
        "sources_failed":     failed_sources,

        # Consensus metrics (averaged)
        "humidity":           humidity_consensus,
        "wind_speed_kmh":     wind_consensus,
        "pressure":           pressure_consensus,
        "temperature":        temp_consensus,
        "apparent_temperature": current.get("apparent_temperature", temp_consensus),
        "uv_index":           current.get("uv_index", 0),
        "wind_direction":     current.get("wind_direction_10m", 0),
        "weather_code":       current.get("weather_code", 0),
        "visibility_m":       current.get("visibility", 0),

        # Per-source breakdown (para UI e IA)
        "metrics_by_source": {
            "humidity":  humidity_by_src,
            "wind_kmh":  wind_by_src,
            "pressure":  pressure_by_src,
            "temp_c":    temp_by_src,
        },

        "raw_open_meteo": om_raw if isinstance(om_raw, dict) else None,
    }
