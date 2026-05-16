from __future__ import annotations

from services.apac_official import weather_at


async def assist_report(lat: float, lon: float) -> dict:
    weather = await weather_at(lat, lon)
    rain = float((weather or {}).get("rain_1h_mm") or 0)
    wind = float((weather or {}).get("wind_kmh") or 0)

    if rain > 10:
        question = "Está chovendo forte aí. Tem alagamento na rua?"
        suggested = "alagamento"
    elif wind > 50:
        question = "Vento forte na região. Tem árvore caída ou poste em risco?"
        suggested = "queda_arvore"
    else:
        question = "O que você está vendo aí?"
        suggested = None

    hint_parts = []
    if weather:
        if weather.get("rain_1h_mm") is not None:
            hint_parts.append(f"{float(weather['rain_1h_mm']):.1f} mm/h")
        if weather.get("wind_kmh") is not None:
            hint_parts.append(f"{float(weather['wind_kmh']):.0f} km/h")
        if weather.get("station_name"):
            hint_parts.append(weather["station_name"])

    return {
        "question": question,
        "suggested_category": suggested,
        "weather_hint": " · ".join(hint_parts),
        "weather": weather,
    }
