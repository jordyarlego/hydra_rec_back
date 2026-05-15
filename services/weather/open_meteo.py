import httpx
from services.cache import cache_get, cache_set
from data.bairros_coords import BAIRRO_COORDS


async def geocode_city(city_name: str) -> dict:
    if city_name in BAIRRO_COORDS:
        lat, lon = BAIRRO_COORDS[city_name]
        return {"name": city_name, "latitude": lat, "longitude": lon,
                "country": "Brasil", "admin1": "Pernambuco"}

    async with httpx.AsyncClient(timeout=8.0) as client:
        url = f"https://geocoding-api.open-meteo.com/v1/search?name={city_name} Recife&count=1&language=pt"
        resp = await client.get(url)
        data = resp.json()
        if data.get("results"):
            return data["results"][0]

    return {"name": city_name, "latitude": -8.0539, "longitude": -34.8811,
            "country": "Brasil", "admin1": "Pernambuco"}


async def fetch_weather_data(lat: float, lon: float) -> dict:
    cache_key = f"weather_{lat:.4f}_{lon:.4f}"
    cached = cache_get(cache_key, 900)
    if cached:
        return cached

    base = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,"
        f"wind_speed_10m,wind_direction_10m,wind_gusts_10m,surface_pressure,uv_index,is_day"
        f"&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        f"&past_hours=24&forecast_days=7&timezone=auto"
        f"&hourly=precipitation,temperature_2m,weather_code"
    )

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(base)
        data = resp.json()

    cache_set(cache_key, data)
    return data


async def fetch_elevation(lat: float, lon: float) -> float:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            url = f"https://api.open-meteo.com/v1/elevation?latitude={lat}&longitude={lon}"
            resp = await client.get(url)
            data = resp.json()
            if data.get("elevation") and len(data["elevation"]) > 0:
                return data["elevation"][0]
    except Exception:
        pass
    return 10.0
