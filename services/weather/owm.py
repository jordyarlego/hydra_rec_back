import os
import logging
import httpx
from services.cache import cache_get, cache_set

logger = logging.getLogger(__name__)


async def fetch_owm(lat: float, lon: float) -> dict | None:
    key = os.getenv("OPENWEATHER_KEY", "")
    if not key:
        return None

    cache_key = f"owm_{lat:.4f}_{lon:.4f}"
    cached = cache_get(cache_key, 1200)
    if cached:
        return cached

    url = f"https://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={key}&units=metric"
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            data = r.json()

        items = data.get("list", [])[:8]  # próximas 24h (8 × 3h)
        rain_next = sum(i.get("rain", {}).get("3h", 0) for i in items)

        result = {
            "source": "OpenWeatherMap",
            "rain_next_24h_mm": round(rain_next, 2),
            "temp_c": items[0]["main"]["temp"] if items else 28,
            "humidity": items[0]["main"]["humidity"] if items else 70,
            "pressure": items[0]["main"]["pressure"] if items else 1013,
        }
        cache_set(cache_key, result)
        return result
    except Exception as e:
        logger.warning(f"OWM fetch falhou: {e}")
        return None
