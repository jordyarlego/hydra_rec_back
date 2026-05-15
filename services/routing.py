import math
import httpx
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


_PONTOS_CRITICOS = [
    # ── Centro / Recife Antigo / Santo Amaro ──────────────────────────────────
    {"lat": -8.0628, "lon": -34.8773, "name": "Cais José Estelita",              "description": "Alagamento frequente com maré alta + chuva"},
    {"lat": -8.0650, "lon": -34.8830, "name": "Comunidade Coque (São José)",     "description": "Alta vulnerabilidade às margens do Capibaribe"},
    {"lat": -8.0730, "lon": -34.8960, "name": "Canal da Tacaruna (Santo Amaro)", "description": "Canal transborda com ≥ 20mm/h — bloqueia acesso à Boa Vista"},
    {"lat": -8.0760, "lon": -34.8850, "name": "Bairro do Recife / Recife Antigo","description": "Área costeira: combinação chuva + maré alta causa inundações"},
    {"lat": -8.0670, "lon": -34.8870, "name": "Rua da Aurora / Capibaribe",      "description": "Margem do Capibaribe — transborda em chuvas moderadas"},
    {"lat": -8.0640, "lon": -34.8810, "name": "Av. Dantas Barreto (São José)",   "description": "Via baixa com acúmulo frequente após chuvas"},
    # ── Derby / Boa Vista / Espinheiro ───────────────────────────────────────
    {"lat": -8.0590, "lon": -34.8980, "name": "Av. Agamenon Magalhães — viaduto","description": "Baixio sob o viaduto acumula água rapidamente"},
    {"lat": -8.0610, "lon": -34.9010, "name": "Praça do Derby",                  "description": "Cruzamento sujeito a acúmulo depois de chuvas moderadas"},
    {"lat": -8.0580, "lon": -34.8940, "name": "Av. Rui Barbosa (Espinheiro)",    "description": "Cruzamento de acúmulo hídrico entre Derby e Espinheiro"},
    # ── Boa Viagem / Pina / Imbiribeira ──────────────────────────────────────
    {"lat": -8.0930, "lon": -34.8960, "name": "Canal dos Setúbal (Boa Viagem N)","description": "Transbordamento bloqueia acesso à Boa Viagem Norte"},
    {"lat": -8.1048, "lon": -34.8940, "name": "BR-101 Sul / Pina",               "description": "Risco de alagamento e queda de árvores"},
    {"lat": -8.0990, "lon": -34.9210, "name": "Canal do Pina",                   "description": "Extravasamento em chuvas acima de 30mm/h"},
    {"lat": -8.1060, "lon": -34.9050, "name": "Av. Eng. Domingos Ferreira",      "description": "Alagamento frequente no trecho central da Boa Viagem"},
    {"lat": -8.1050, "lon": -34.8950, "name": "Av. Padre Carapuceiro (Shopping Recife)", "description": "Área do Shopping Recife alaga com chuvas moderadas (≥ 15mm/h)"},
    {"lat": -8.1170, "lon": -34.9070, "name": "Av. República do Líbano (Pina sul)","description": "Trecho próximo ao Canal do Pina — alagamento recorrente"},
    {"lat": -8.1300, "lon": -34.9200, "name": "Av. Visconde de Jequitinhonha",   "description": "Área alagável próxima ao aeroporto do Recife"},
    {"lat": -8.1120, "lon": -34.9070, "name": "Imbiribeira baixo",               "description": "Bacia de acumulação hídrica"},
    # ── Afogados / Madalena / Torre / Tejipió ────────────────────────────────
    {"lat": -8.0451, "lon": -34.9256, "name": "Av. Caxangá (km 3)",             "description": "Ponto crítico na BR-232 — alagamento com chuvas ≥ 20mm"},
    {"lat": -8.0840, "lon": -34.9180, "name": "Av. Caxangá — Madalena (km 2)", "description": "Cruzamento com histórico de alagamento intenso"},
    {"lat": -8.0590, "lon": -34.9310, "name": "Mangueirão (Torre/Cordeiro)",    "description": "Baixio da Torre — acúmulo frequente após chuvas"},
    {"lat": -8.0680, "lon": -34.9340, "name": "Av. Recife (Caçote/Afogados)",  "description": "Via marginal ao Canal do Capibaribe — alaga regularmente"},
    {"lat": -8.0920, "lon": -34.9340, "name": "Tejipió – Várzea",              "description": "Planície de inundação do Rio Tejipió"},
    {"lat": -8.0760, "lon": -34.9080, "name": "Canal Jordão (Afogados/Sancho)","description": "Canal histórico de transbordamento em episódios de chuva forte"},
    # ── Norte / Casa Amarela / Ibura / Jordão ────────────────────────────────
    {"lat": -8.0370, "lon": -34.9060, "name": "Av. Norte — Arruda/Bomba",      "description": "Cruzamento histórico de alagamento em Arruda"},
    {"lat": -8.0430, "lon": -34.9050, "name": "Rua Real da Torre (Parnamirim)","description": "Via baixa com histórico de alagamento"},
    {"lat": -8.0490, "lon": -34.9130, "name": "Área do Metrô Joana Bezerra",   "description": "Acesso ao metrô pode ser interditado por alagamento"},
    {"lat": -8.0180, "lon": -34.9220, "name": "Morro da Conceição (Casa Amarela)", "description": "Encosta com risco de deslizamento em chuvas fortes"},
    {"lat": -8.0270, "lon": -34.9170, "name": "Alto do Mandu (Água Fria)",     "description": "Comunidade em encosta com risco geológico"},
    {"lat": -8.1450, "lon": -34.9420, "name": "Comunidade Ibura",              "description": "Alto risco de deslizamento e alagamento"},
    {"lat": -8.1290, "lon": -34.9380, "name": "Jordão – Encosta Norte",        "description": "Área de risco geológico"},
]

_MODE_MULTIPLIER = {
    "driving-car":    1.0,
    "cycling-regular": 1.5,
    "foot-walking":   1.8,
}

_APAC_BONUS = {
    "SEVERO":   35,
    "ALTO":     25,
    "MODERADO": 12,
    "ATENCAO":   5,
    "SEGURO":    0,
}

_OSRM_ENDPOINTS = {
    "driving-car":     ("https://routing.openstreetmap.de/routed-car",  "driving"),
    "cycling-regular": ("https://routing.openstreetmap.de/routed-bike", "cycling"),
    "foot-walking":    ("https://routing.openstreetmap.de/routed-foot", "walking"),
}


def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _minutes_since(iso_str: str) -> int:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return int((datetime.now(timezone.utc) - dt).total_seconds() / 60)
    except Exception:
        return 999


def _is_condition_active(rain_next: float, apac_nivel: str) -> bool:
    """True quando as condições climáticas justificam risco ativo no ponto histórico."""
    return rain_next >= 5.0 or apac_nivel in ("MODERADO", "ALTO", "SEVERO")


def _contextualize_hazard(hazard: dict, rain_next: float, apac_nivel: str) -> dict:
    """
    Adiciona risk_active e context_note ao hazard histórico.
    Com tempo bom: ponto aparece como contexto informativo, não como risco ativo.
    """
    active = _is_condition_active(rain_next, apac_nivel)
    context_note = None if active else (
        "Condições climáticas atuais favoráveis — "
        "este ponto costuma alagar com ≥ 15mm/h de chuva"
    )
    return {
        **hazard,
        "risk_active":  active,
        "severity":     hazard.get("severity", "moderado") if active else "leve",
        "context_note": context_note,
    }


def _stations_near_route(coords: list, stations: list, radius_km: float = 1.5) -> list:
    """Find APAC stations within radius_km of any sampled route point."""
    step = max(1, len(coords) // 60)
    nearby = []
    seen: set[str] = set()
    for i in range(0, len(coords), step):
        lon, lat = coords[i][0], coords[i][1]
        for st in stations:
            key = st["nome"]
            if key in seen:
                continue
            if _haversine_km(lat, lon, st["lat"], st["lon"]) < radius_km:
                seen.add(key)
                nearby.append(st)
    return nearby


async def get_route(
    origin: tuple[float, float],
    destination: tuple[float, float],
    profile: str = "driving-car",
) -> dict:
    """Fetch route from OSRM (free, no API key required)."""
    base_url, osrm_profile = _OSRM_ENDPOINTS.get(profile, _OSRM_ENDPOINTS["driving-car"])
    lat1, lon1 = origin
    lat2, lon2 = destination
    url = (
        f"{base_url}/route/v1/{osrm_profile}"
        f"/{lon1},{lat1};{lon2},{lat2}"
        f"?overview=full&geometries=geojson&steps=false"
    )
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()

    if data.get("code") != "Ok" or not data.get("routes"):
        raise ValueError("OSRM não retornou rota válida")

    route = data["routes"][0]
    return {
        "coordinates": route["geometry"]["coordinates"],   # [[lon, lat], ...]
        "distance_m":  route.get("distance", 0),
        "duration_s":  route.get("duration", 0),
        "profile":     profile,
    }


async def analyze_route_risk(
    route_data: dict,
    weather_rain_next: float = 0.0,
    apac_nivel: str = "ATENCAO",
    inmet_alerts: list | None = None,
    apac_stations: list | None = None,
) -> dict:
    coords    = route_data.get("coordinates", [])
    profile   = route_data.get("profile", "driving-car")
    dist_km   = round(route_data.get("distance_m", 0) / 1000, 1)
    dur_min   = round(route_data.get("duration_s", 0) / 60)
    mode_mult = _MODE_MULTIPLIER.get(profile, 1.0)

    if not coords:
        return {"risk_score": 0, "risk_level": "BAIXO", "hazards": [],
                "active_alerts": [], "apac_nivel": apac_nivel,
                "distance_km": dist_km, "duration_min": dur_min}

    hazards: list[dict] = []
    seen_names: set[str] = set()

    # 1. Historical critical points (Defesa Civil PE) — contextualized by current conditions
    step = max(1, len(coords) // 40)
    for i in range(0, len(coords), step):
        lon, lat = coords[i][0], coords[i][1]
        for pc in _PONTOS_CRITICOS:
            if pc["name"] in seen_names:
                continue
            if _haversine_km(lat, lon, pc["lat"], pc["lon"]) < 0.6:
                seen_names.add(pc["name"])
                raw = {
                    "type":        "ponto_critico_historico",
                    "lat":         pc["lat"],
                    "lon":         pc["lon"],
                    "name":        pc["name"],
                    "description": pc["description"],
                    "severity":    "moderado",
                    "source":      "Defesa Civil PE / APAC 2018-2024",
                }
                hazards.append(_contextualize_hazard(raw, weather_rain_next, apac_nivel))

    # 2. Real-time APAC pluviometric stations near the route
    for st in _stations_near_route(coords, apac_stations or []):
        h1  = st.get("hora_1_mm", 0.0)
        h6  = st.get("horas_6_mm", 0.0)
        h24 = st.get("horas_24_mm", 0.0)
        if h1 < 1.0 and h6 < 5.0:
            continue
        sev = "grave" if h1 >= 20 else ("moderado" if h1 >= 8 else "leve")
        nome = st["nome"]
        if nome in seen_names:
            continue
        seen_names.add(nome)
        hazards.append({
            "type":        "chuva_ativa_apac",
            "lat":         st["lat"],
            "lon":         st["lon"],
            "name":        nome,
            "description": f"{h1:.1f}mm na última hora · {h6:.1f}mm em 6h (APAC Geoportal — tempo real)",
            "severity":    sev,
            "hora_1_mm":   h1,
            "horas_6_mm":  h6,
            "source":      "APAC Geoportal (tempo real)",
        })

    # Score calculation
    base_score = 0
    _SEV_PTS = {"leve": 5, "moderado": 15, "grave": 30}
    for h in hazards:
        if h["type"] == "ponto_critico_historico":
            # Pontos históricos sem condições ativas valem menos no score
            base_score += 8 if h.get("risk_active", True) else 2
        elif h["type"] == "chuva_ativa_apac":
            base_score += _SEV_PTS.get(h["severity"], 10)
        else:
            base_score += _SEV_PTS.get(h.get("severity", "leve"), 5)

    # Rain forecast bonus
    if weather_rain_next > 20:
        base_score += 20
    elif weather_rain_next > 8:
        base_score += 10
    elif weather_rain_next > 2:
        base_score += 5

    # APAC boletim alert level
    base_score += _APAC_BONUS.get(apac_nivel, 5)

    # INMET active alerts (capped)
    inmet_bonus = min(sum(a.get("score_bonus", 0) for a in (inmet_alerts or [])), 30)
    base_score += inmet_bonus

    risk_score = min(round(base_score * mode_mult), 100)
    level = "BAIXO" if risk_score < 25 else ("MEDIO" if risk_score < 55 else "ALTO")

    # Subsample coords for the map (max 200 points)
    step = max(1, len(coords) // 200)
    map_coords = [[c[1], c[0]] for c in coords[::step]]  # [lon,lat] → [lat,lon] for Leaflet

    return {
        "risk_score":    risk_score,
        "risk_level":    level,
        "hazards":       hazards,
        "active_alerts": inmet_alerts or [],
        "apac_nivel":    apac_nivel,
        "distance_km":   dist_km,
        "duration_min":  dur_min,
        "profile":       profile,
        "route_coords":  map_coords,
    }
