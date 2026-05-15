import os
import logging
import httpx
from services.cache import cache_get, cache_set

logger = logging.getLogger(__name__)


async def _fetch_owm_raw(lat: float, lon: float) -> list | None:
    """Retorna a lista bruta de slots 3h do OWM (cached 20min)."""
    key = os.getenv("OPENWEATHER_KEY", "")
    if not key:
        return None

    cache_key = f"owm_raw_{lat:.4f}_{lon:.4f}"
    cached = cache_get(cache_key, 1200)
    if cached:
        return cached

    url = f"https://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={key}&units=metric"
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            items = r.json().get("list", [])
        cache_set(cache_key, items)
        return items
    except Exception as e:
        logger.warning(f"OWM fetch falhou: {e}")
        return None


async def fetch_owm(lat: float, lon: float) -> dict | None:
    """Totais 24h para fusão de consensus atual (dashboard)."""
    items = await _fetch_owm_raw(lat, lon)
    if not items:
        return None

    slots_24h = items[:8]
    rain_next = sum(i.get("rain", {}).get("3h", 0) for i in slots_24h)
    first = slots_24h[0] if slots_24h else {}

    return {
        "source": "OpenWeatherMap",
        "rain_next_24h_mm": round(rain_next, 2),
        "temp_c": first.get("main", {}).get("temp", 28),
        "humidity": first.get("main", {}).get("humidity", 70),
        "pressure": first.get("main", {}).get("pressure", 1013),
    }


async def fetch_owm_hourly_slots(lat: float, lon: float, n_hours: int = 6) -> list[float]:
    """
    Retorna chuva estimada por hora para as próximas n_hours horas.
    OWM fornece blocos de 3h → distribui uniformemente (rain_3h / 3 por hora).
    """
    items = await _fetch_owm_raw(lat, lon)
    if not items:
        return [0.0] * n_hours

    hourly: list[float] = []
    for item in items:
        rain_3h = item.get("rain", {}).get("3h", 0.0)
        rain_per_h = round(rain_3h / 3, 3)
        hourly.extend([rain_per_h, rain_per_h, rain_per_h])
        if len(hourly) >= n_hours:
            break

    return hourly[:n_hours]
