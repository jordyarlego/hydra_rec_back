import os
import math
import httpx
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ORS_BASE = "https://api.openrouteservice.org/v2"

_PONTOS_CRITICOS = [
    {"lat": -8.0628, "lon": -34.8773, "name": "Cais José Estelita", "description": "Alagamento frequente em chuvas fortes"},
    {"lat": -8.0451, "lon": -34.9256, "name": "Av. Caxangá (km 3)", "description": "Ponto crítico na BR-232"},
    {"lat": -8.1048, "lon": -34.8940, "name": "BR-101 Sul / Pina", "description": "Risco de alagamento e queda de árvores"},
    {"lat": -8.0990, "lon": -34.9210, "name": "Canal do Pina", "description": "Extravasamento em chuvas acima de 30mm/h"},
    {"lat": -8.1120, "lon": -34.9070, "name": "Imbiribeira baixo", "description": "Bacia de acumulação hídrica"},
    {"lat": -8.1450, "lon": -34.9420, "name": "Comunidade Ibura", "description": "Alto risco de deslizamento e alagamento"},
    {"lat": -8.1290, "lon": -34.9380, "name": "Jordão – Encosta Norte", "description": "Área de risco geológico"},
    {"lat": -8.0920, "lon": -34.9340, "name": "Tejipió – Várzea", "description": "Planície de inundação do Rio Tejipió"},
]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
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


async def get_route(origin: tuple[float, float], destination: tuple[float, float], profile: str = "driving-car") -> dict:
    key = os.getenv("OPENROUTESERVICE_KEY", "")
    if not key:
        raise ValueError("OPENROUTESERVICE_KEY não configurado")

    url = f"{ORS_BASE}/directions/{profile}/geojson"
    body = {
        "coordinates": [[origin[1], origin[0]], [destination[1], destination[0]]],
        "alternative_routes": {"target_count": 2, "weight_factor": 1.4, "share_factor": 0.6},
        "language": "pt-br",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(url, json=body, headers={"Authorization": key, "Content-Type": "application/json"})
        r.raise_for_status()
        return r.json()


async def analyze_route_risk(route_geojson: dict, reports_fetcher=None) -> dict:
    try:
        features = route_geojson.get("features", [])
        if not features:
            return {"risk_score": 0, "risk_level": "BAIXO", "hazards": [], "bairros_atravessados": []}

        coords = features[0]["geometry"]["coordinates"]
        hazards = []
        step = max(1, len(coords) // 30)

        for i in range(0, len(coords), step):
            lon, lat = coords[i][0], coords[i][1]

            # Check pontos críticos
            for pc in _PONTOS_CRITICOS:
                dist = _haversine_km(lat, lon, pc["lat"], pc["lon"])
                if dist < 0.4:
                    existing = any(
                        h.get("name") == pc["name"] for h in hazards
                    )
                    if not existing:
                        hazards.append({
                            "type": "ponto_critico_historico",
                            "lat": pc["lat"],
                            "lon": pc["lon"],
                            "name": pc["name"],
                            "description": pc["description"],
                            "severity": "moderado",
                        })

        # Fetch nearby reports if fetcher provided
        if reports_fetcher:
            try:
                mid_idx = len(coords) // 2
                mlat, mlon = coords[mid_idx][1], coords[mid_idx][0]
                nearby = await reports_fetcher(mlat, mlon, radius=1500)
                for r in (nearby or []):
                    hazards.append({
                        "type": f"{r.get('type', 'ocorrencia')}_reportado",
                        "lat": r.get("lat"),
                        "lon": r.get("lon"),
                        "description": r.get("description") or r.get("bairro") or r.get("type", ""),
                        "severity": r.get("severity", "leve"),
                        "reports_count": 1 + r.get("confirmed_count", 0),
                        "last_seen_minutes_ago": _minutes_since(r.get("created_at", "")),
                    })
            except Exception as e:
                logger.warning(f"reports fetch for route failed: {e}")

        # Score
        risk_score = 0
        sev_pts = {"leve": 5, "moderado": 15, "grave": 30}
        for h in hazards:
            if h["type"] == "ponto_critico_historico":
                risk_score += 8
            else:
                risk_score += sev_pts.get(h.get("severity", "leve"), 5)

        risk_score = min(risk_score, 100)
        if risk_score < 25:
            level = "BAIXO"
        elif risk_score < 55:
            level = "MEDIO"
        else:
            level = "ALTO"

        # Extract bairros from summary
        bairros = list({f.get("properties", {}).get("segments", [{}])[0].get("steps", [{}])[0].get("name", "") for f in features if f.get("properties")})

        return {
            "risk_score": risk_score,
            "risk_level": level,
            "bairros_atravessados": [b for b in bairros if b],
            "hazards": hazards,
            "route_geojson": route_geojson,
        }
    except Exception as e:
        logger.warning(f"analyze_route_risk failed: {e}")
        return {"risk_score": 0, "risk_level": "BAIXO", "hazards": [], "bairros_atravessados": [], "route_geojson": route_geojson}
