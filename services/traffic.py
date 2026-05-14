def traffic_forecast_multiplier(rain_next_2h_mm: float, hour_of_day: int) -> dict:
    """Fator de congestionamento heurístico para o Recife."""
    base_rush = 1.4 if hour_of_day in (7, 8, 17, 18, 19) else 1.0

    if rain_next_2h_mm < 2:
        rain_factor = 1.0
    elif rain_next_2h_mm < 8:
        rain_factor = 1.3
    elif rain_next_2h_mm < 20:
        rain_factor = 1.7
    else:
        rain_factor = 2.4

    mult = base_rush * rain_factor

    if mult < 1.2:
        label = "FLUIDO"
    elif mult < 1.5:
        label = "MODERADO"
    elif mult < 2.0:
        label = "LENTO"
    else:
        label = "CONGESTIONADO"

    return {
        "multiplier": round(mult, 2),
        "label": label,
        "extra_minutes_per_10min": round((mult - 1.0) * 10, 1),
    }
