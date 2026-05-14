import math
import logging
import httpx
from services.cache import cache_get, cache_set

logger = logging.getLogger(__name__)

INMET_STATIONS = [
    ("A301", -8.0590, -34.9588, "Recife"),
    ("A357", -7.9858, -34.8313, "Olinda"),
]


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(a))


async def fetch_inmet_nearest(lat: float, lon: float) -> dict | None:
    nearest = min(INMET_STATIONS, key=lambda s: _haversine(lat, lon, s[1], s[2]))
    code, _, _, name = nearest

    cache_key = f"inmet_{code}"
    cached = cache_get(cache_key, 1800)
    if cached:
        return cached

    url = f"https://apitempo.inmet.gov.br/estacao/dados/{code}"
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.get(url)
            data = r.json()
        if not data or not isinstance(data, list):
            return None
        latest = next((d for d in reversed(data) if d.get("CHUVA") is not None), None)
        if not latest:
            return None

        result = {
            "source": f"INMET-{code}",
            "station": name,
            "rain_last_hour_mm": float(latest.get("CHUVA") or 0),
            "temp_c": float(latest.get("TEM_INS") or 0),
            "humidity": float(latest.get("UMD_INS") or 0),
            "pressure": float(latest.get("PRE_INS") or 0),
            "wind_speed_kmh": float(latest.get("VEN_VEL") or 0) * 3.6,
            "timestamp": latest.get("HR_MEDICAO"),
        }
        cache_set(cache_key, result)
        return result
    except Exception as e:
        logger.warning(f"INMET fetch falhou ({code}): {e}")
        return None
