def heat_index_steadman(T_celsius: float, RH_percent: float) -> float:
    """Sensação térmica pelo método Steadman/NOAA Rothfusz. Para T < 27°C retorna T."""
    if T_celsius < 27:
        return round(T_celsius, 1)
    T_f = T_celsius * 9 / 5 + 32
    HI_f = (
        -42.379
        + 2.04901523 * T_f
        + 10.14333127 * RH_percent
        - 0.22475541 * T_f * RH_percent
        - 6.83783e-3 * T_f ** 2
        - 5.481717e-2 * RH_percent ** 2
        + 1.22874e-3 * T_f ** 2 * RH_percent
        + 8.5282e-4 * T_f * RH_percent ** 2
        - 1.99e-6 * T_f ** 2 * RH_percent ** 2
    )
    return round((HI_f - 32) * 5 / 9, 1)


def heat_risk_label(hi_celsius: float) -> str:
    if hi_celsius >= 54:
        return "CRITICO"
    if hi_celsius >= 41:
        return "ALERTA"
    if hi_celsius >= 32:
        return "ATENCAO"
    return "CONFORTAVEL"
