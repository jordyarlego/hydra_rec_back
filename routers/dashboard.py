import asyncio
from fastapi import APIRouter, HTTPException

from services.weather.open_meteo import geocode_city, fetch_weather_data, fetch_elevation
from services.weather.tides import scrape_tide_data
from services.weather.fusion import fetch_weather_consensus
from services.risk_score import calculate_risk_score_v2
from services.heat_index import heat_index_steadman, heat_risk_label
from services.traffic import traffic_forecast_multiplier
from services.ai_explain import explain_score
from models.schemas import BatchScoreRequest

router = APIRouter()


def build_forecast_6h(weather: dict) -> list:
    hourly = weather.get("hourly", {})
    times  = hourly.get("time", [])
    precip = hourly.get("precipitation", [])
    temps  = hourly.get("temperature_2m", [])
    codes  = hourly.get("weather_code", [])

    result = []
    for i in range(24, min(30, len(times))):
        result.append({
            "time":          times[i] if i < len(times) else "",
            "precipitation": precip[i] if i < len(precip) else 0,
            "temperature":   temps[i]  if i < len(temps)  else None,
            "weather_code":  codes[i]  if i < len(codes)  else 0,
        })
    return result


def build_daily_forecast(weather: dict) -> list:
    daily = weather.get("daily", {})
    dates = daily.get("time", [])
    highs = daily.get("temperature_2m_max", [])
    lows  = daily.get("temperature_2m_min", [])
    rains = daily.get("precipitation_probability_max", [])
    result = []
    for i in range(1, min(7, len(dates))):
        result.append({
            "date": dates[i] if i < len(dates) else "",
            "high": round(highs[i]) if i < len(highs) and highs[i] is not None else None,
            "low":  round(lows[i])  if i < len(lows)  and lows[i]  is not None else None,
            "rain": int(rains[i])   if i < len(rains) and rains[i] is not None else 0,
        })
    return result


def _build_weather_consensus(weather: dict) -> dict:
    """Adapta formato Open-Meteo para o contrato do calculate_risk_score_v2."""
    hourly_precip = weather.get("hourly", {}).get("precipitation", [])
    current = weather.get("current", {})
    past24h = sum(h for h in hourly_precip[:24] if h is not None)
    next24h = sum(h for h in hourly_precip[24:48] if h is not None)
    return {
        "rain_next_24h_mm": round(next24h, 2),
        "rain_past_24h_mm": round(past24h, 2),
        "humidity": current.get("relative_humidity_2m", 70),
        "pressure": current.get("surface_pressure", 1013),
        "confidence": "ALTA",
        "sources_count": 1,
    }


async def _bairro_summary(bairro: str) -> dict:
    try:
        geo = await geocode_city(bairro)
        lat, lon = geo["latitude"], geo["longitude"]
        weather_data, tide = await asyncio.gather(
            fetch_weather_data(lat, lon),
            scrape_tide_data(),
        )
        consensus = _build_weather_consensus(weather_data)
        risk = calculate_risk_score_v2(consensus, 10.0, tide, bairro)
        current = weather_data.get("current", {})
        return {
            "name":  bairro,
            "temp":  round(current.get("temperature_2m", 0)),
            "score": risk["score"],
            "nivel": risk["nivel"],
        }
    except Exception as e:
        print(f"Summary error for {bairro}: {e}")
        return {"name": bairro, "temp": 0, "score": 0, "nivel": "ERRO"}


@router.post("/api/scores")
async def get_scores(req: BatchScoreRequest):
    results = await asyncio.gather(*[_bairro_summary(b) for b in req.bairros[:6]])
    return {"scores": list(results)}


_CONSENSUS_FALLBACK = {
    "rain_next_24h_mm": 0.0, "rain_past_24h_mm": 0.0,
    "humidity": 75, "pressure": 1013, "temperature": 28,
    "apparent_temperature": 28, "uv_index": 0,
    "wind_speed_kmh": 0, "wind_direction": 0, "weather_code": 0,
    "confidence": "BAIXA", "sources": [], "sources_count": 0,
    "raw_open_meteo": None,
}


async def fetch_dashboard(bairro: str) -> dict:
    geo = await geocode_city(bairro)
    lat, lon = geo["latitude"], geo["longitude"]

    results = await asyncio.gather(
        fetch_weather_consensus(lat, lon, bairro),
        fetch_elevation(lat, lon),
        scrape_tide_data(),
        return_exceptions=True,
    )
    consensus = results[0] if isinstance(results[0], dict) else _CONSENSUS_FALLBACK
    elevation = results[1] if isinstance(results[1], (int, float)) else 10.0
    tide      = results[2] if isinstance(results[2], dict) else {"height": 1.5, "trend": "Desconhecido"}

    risk = calculate_risk_score_v2(consensus, elevation, tide, bairro)

    temp     = consensus.get("temperature", 28)
    humidity = consensus.get("humidity", 70)
    hi       = heat_index_steadman(temp, humidity)

    from datetime import datetime
    rain_2h = consensus.get("rain_next_24h_mm", 0) / 12
    traffic = traffic_forecast_multiplier(rain_2h, datetime.now().hour)
    weather = consensus.get("raw_open_meteo") or {}

    return {
        "location":      geo,
        "weather":       weather,
        "risk":          risk,
        "consensus":     {k: v for k, v in consensus.items() if k != "raw_open_meteo"},
        "heatIndex":     {"value": hi, "risk": heat_risk_label(hi)},
        "traffic":       traffic,
        "forecast6h":    build_forecast_6h(weather),
        "forecastDaily": build_daily_forecast(weather),
    }


@router.get("/api/dashboard/{bairro}")
async def get_dashboard_data(bairro: str):
    try:
        return await fetch_dashboard(bairro)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/explain/{bairro}")
async def get_score_explanation(bairro: str):
    try:
        geo = await geocode_city(bairro)
        lat, lon = geo["latitude"], geo["longitude"]
        results = await asyncio.gather(
            fetch_weather_consensus(lat, lon, bairro),
            fetch_elevation(lat, lon),
            scrape_tide_data(),
            return_exceptions=True,
        )
        consensus = results[0] if isinstance(results[0], dict) else {
            "rain_next_24h_mm": 0.0, "rain_past_24h_mm": 0.0,
            "humidity": 75, "pressure": 1013, "temperature": 28,
            "apparent_temperature": 28, "uv_index": 0,
            "wind_speed_kmh": 0, "wind_direction": 0, "weather_code": 0,
            "confidence": "BAIXA", "sources": [], "sources_count": 0,
            "raw_open_meteo": None,
        }
        elevation = results[1] if isinstance(results[1], (int, float)) else 10.0
        tide      = results[2] if isinstance(results[2], dict) else {"height": 1.5, "trend": "Desconhecido"}
        risk = calculate_risk_score_v2(consensus, elevation, tide, bairro)
        explanation = await explain_score(bairro, risk)
        return {"explanation": explanation, "score": risk["score"], "nivel": risk["nivel"]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
