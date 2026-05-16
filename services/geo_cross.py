"""
Cruza coordenadas de reports com dados urbanos oficiais do Recife.

- point_in_polygon: ray-casting puro Python (portado do bairroGeo.js)
- nearest_road: Haversine sobre official_roads do Supabase
- find_similar_official_requests: chamados parecidos em raio configurável
- cross_report_with_official_data: orquestra todo o cruzamento
"""
import json
import logging
import math
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ── GeoJSON local — carregado uma única vez na importação do módulo ────────

_GEOJSON_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__),
    "../../front_end_hydrarec/src/data/geo/recife_bairros_2023.geojson",
))

_bairros_geojson: Optional[dict] = None


def _load_geojson() -> Optional[dict]:
    global _bairros_geojson
    if _bairros_geojson is not None:
        return _bairros_geojson
    try:
        with open(_GEOJSON_PATH, encoding="utf-8") as f:
            _bairros_geojson = json.load(f)
    except FileNotFoundError:
        logger.warning(f"GeoJSON não encontrado: {_GEOJSON_PATH}")
        _bairros_geojson = {}
    return _bairros_geojson


RPA_NAMES = {
    1: "RPA 1 — Centro",
    2: "RPA 2 — Norte",
    3: "RPA 3 — Noroeste",
    4: "RPA 4 — Oeste",
    5: "RPA 5 — Sul",
    6: "RPA 6 — Sudoeste",
}
MICRO_NAMES = {
    1: "Macrozona 1",
    2: "Macrozona 2",
    3: "Macrozona 3",
}

# Mapa: categoria de report → service_type parecido nos chamados oficiais
_REPORT_TO_OFFICIAL = {
    "alagamento":        ["alagamento", "drenagem"],
    "deslizamento":      ["barreira", "deslizamento"],
    "queda_arvore":      ["poda de árvore", "remoção de árvore", "queda de árvore"],
    "via_intransitavel": ["tapa-buracos", "pavimentação", "via intransitável"],
    "poste_caido":       ["iluminação pública", "poste"],
    "buraco":            ["tapa-buracos", "pavimentação"],
    "lixo":              ["limpeza", "coleta de lixo"],
    "iluminacao":        ["iluminação pública", "poste"],
    "outro":             [],
}


# ── Geometria: ray-casting para ponto em polígono ─────────────────────────

def _point_in_ring(lat: float, lon: float, ring: list) -> bool:
    """Verifica se (lat, lon) está dentro de um anel GeoJSON [lon, lat][]."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]   # lon, lat
        xj, yj = ring[j][0], ring[j][1]
        intersects = (
            (yi > lat) != (yj > lat)
        ) and (
            lon < (xj - xi) * (lat - yi) / (yj - yi or 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _point_in_polygon(lat: float, lon: float, polygon: list) -> bool:
    if not polygon:
        return False
    if not _point_in_ring(lat, lon, polygon[0]):
        return False
    for hole in polygon[1:]:
        if _point_in_ring(lat, lon, hole):
            return False
    return True


def _point_in_feature(lat: float, lon: float, feature: dict) -> bool:
    geom = feature.get("geometry") or {}
    gtype = geom.get("type")
    coords = geom.get("coordinates", [])
    if gtype == "Polygon":
        return _point_in_polygon(lat, lon, coords)
    if gtype == "MultiPolygon":
        return any(_point_in_polygon(lat, lon, poly) for poly in coords)
    return False


# ── Haversine ─────────────────────────────────────────────────────────────

def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── APIs públicas ─────────────────────────────────────────────────────────

def find_neighborhood(lat: float, lon: float) -> dict:
    """
    Retorna {name, rpa, rpa_code, microregion, microregion_code} para (lat, lon).
    Usa ray-casting sobre o GeoJSON local. Retorna dict vazio se fora do Recife.
    """
    geojson = _load_geojson()
    for feat in geojson.get("features", []):
        if _point_in_feature(lat, lon, feat):
            props = feat.get("properties", {})
            rpa_code = props.get("CRPAAACODI")
            micro_code = props.get("CMICROCODI")
            return {
                "name":              props.get("EBAIRRNOMEOF") or props.get("EBAIRRNOME") or "",
                "rpa":               RPA_NAMES.get(rpa_code, f"RPA {rpa_code}"),
                "rpa_code":          rpa_code,
                "microregion":       MICRO_NAMES.get(micro_code, f"Macrozona {micro_code}"),
                "microregion_code":  micro_code,
            }
    return {}


def nearest_road(lat: float, lon: float, max_m: float = 500) -> Optional[dict]:
    """Retorna a via mais próxima de (lat, lon) dentro de max_m metros."""
    try:
        from services.supabase_client import get_service_client as get_client  # bypass RLS
        res = get_client().table("official_roads").select(
            "id, name, neighborhood, rpa, lat, lon"
        ).not_.is_("lat", "null").not_.is_("lon", "null").limit(2000).execute()
        rows = res.data or []
    except Exception as e:
        logger.warning(f"nearest_road query failed: {e}")
        return None

    best = None
    best_dist = max_m
    for row in rows:
        if row.get("lat") is None or row.get("lon") is None:
            continue
        d = haversine_distance_m(lat, lon, row["lat"], row["lon"])
        if d < best_dist:
            best_dist = d
            best = {**row, "distance_m": int(d)}
    return best


def find_similar_official_requests(
    lat: float,
    lon: float,
    report_type: str,
    radius_m: float = 300,
    limit: int = 10,
) -> list[dict]:
    """
    Retorna chamados oficiais parecidos com o tipo do report dentro do raio.
    Usa Haversine sobre registros com lat/lon. Fallback por bairro se < 3 matches.
    """
    related_types = _REPORT_TO_OFFICIAL.get(report_type, [])

    try:
        from services.supabase_client import get_service_client as get_client  # bypass RLS
        client = get_client()

        # Busca por lat/lon dentro de bbox aproximado (±0.005° ≈ 500m)
        delta = radius_m / 111_000
        res = client.table("official_service_requests").select(
            "id, source, service_type, category, status, neighborhood, lat, lon, opened_at"
        ).gte("lat", lat - delta).lte("lat", lat + delta).gte(
            "lon", lon - delta
        ).lte("lon", lon + delta).not_.is_("lat", "null").execute()

        rows = res.data or []
    except Exception as e:
        logger.warning(f"find_similar_official_requests error: {e}")
        return []

    results = []
    for row in rows:
        d = haversine_distance_m(lat, lon, row["lat"], row["lon"])
        if d > radius_m:
            continue
        stype = (row.get("service_type") or "").lower()
        is_related = any(rt in stype for rt in related_types) if related_types else True
        results.append({**row, "distance_m": int(d), "related": is_related})

    results.sort(key=lambda r: r["distance_m"])
    return results[:limit]


def calculate_recurrence_score(similar_requests: list[dict]) -> float:
    """
    Score de recorrência: quantos chamados parecidos existem no raio.
    Pondera por proximidade e recência.
    """
    if not similar_requests:
        return 0.0

    score = 0.0
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    for req in similar_requests:
        dist_m = req.get("distance_m", 300)
        dist_factor = max(0.1, 1.0 - dist_m / 300)

        # Recência: fator 1.0 se < 30 dias, 0.5 se < 90 dias, 0.2 se mais antigo
        recency_factor = 0.2
        opened_at = req.get("opened_at")
        if opened_at:
            try:
                dt = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
                days_ago = (now - dt).days
                if days_ago < 30:
                    recency_factor = 1.0
                elif days_ago < 90:
                    recency_factor = 0.5
            except Exception:
                pass

        # Chamados relacionados (mesmo tipo) contam mais
        related_factor = 1.5 if req.get("related") else 0.8
        score += dist_factor * recency_factor * related_factor

    return round(min(score, 10.0), 2)


async def cross_report_with_official_data(report_id: str) -> Optional[dict]:
    """
    Orquestra o cruzamento de um report com dados oficiais.
    Persiste resultado em report_official_crossings.
    Chamado como fire-and-forget após criação do report.
    """
    try:
        from services.supabase_client import get_service_client as get_client  # bypass RLS
        client = get_client()

        res = client.table("reports").select(
            "id, type, lat, lon"
        ).eq("id", report_id).single().execute()
        report = res.data
        if not report:
            return None

        lat = report.get("lat") or report.get("user_lat")
        lon = report.get("lon") or report.get("user_lon")
        if lat is None or lon is None:
            return None

        # 1. Bairro / RPA / Microregião
        neigh = find_neighborhood(lat, lon)

        # 2. Rua mais próxima
        road = nearest_road(lat, lon)

        # 3. Chamados parecidos
        similar = find_similar_official_requests(lat, lon, report.get("type", "outro"))
        recurrence = calculate_recurrence_score(similar)

        # 4. Chamado mais próximo
        nearest_req = similar[0] if similar else None

        crossing = {
            "report_id":                            report_id,
            "neighborhood":                         neigh.get("name"),
            "rpa":                                  neigh.get("rpa"),
            "rpa_code":                             neigh.get("rpa_code"),
            "microregion":                          neigh.get("microregion"),
            "nearest_road_id":                      road.get("id") if road else None,
            "nearest_road_name":                    road.get("name") if road else None,
            "nearest_official_request_id":          nearest_req.get("id") if nearest_req else None,
            "nearest_official_request_type":        nearest_req.get("service_type") if nearest_req else None,
            "nearest_official_request_distance_m":  nearest_req.get("distance_m") if nearest_req else None,
            "recurrence_score":                     recurrence,
            "notes":                                f"{len(similar)} chamado(s) oficial(is) similar(es) em 300m",
        }

        try:
            client.table("report_official_crossings").upsert(
                crossing, on_conflict="report_id"
            ).execute()
        except Exception as e:
            logger.warning(f"report_official_crossings upsert failed: {e}")

        return crossing

    except Exception as e:
        logger.error(f"cross_report_with_official_data({report_id}) failed: {e}")
        return None
