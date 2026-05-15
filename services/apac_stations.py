"""
APAC Geoportal — estações pluviométricas em tempo real (Região Metropolitana do Recife).
ArcGIS REST API pública: geoportal.apac.pe.gov.br
Layer 4: chuva acumulada 1h / 3h / 6h / 12h / 24h por estação (lat/lon precisos).
Cache 10 min (estações atualizam a cada ~10-30 min).
"""
import logging
import httpx
from services.cache import cache_get, cache_set

logger = logging.getLogger(__name__)

_CACHE_KEY = "apac_stations_rmr"
_CACHE_TTL  = 600  # 10 min

_URL = (
    "https://geoportal.apac.pe.gov.br/server/rest/services/"
    "met_monitoramento_chuvas_pe/MapServer/4/query"
)

# Bounding box RMR: Paulista (N) → Cabo/Jaboatão (S) · São Lourenço (W) → Olinda coast (E)
_PARAMS = {
    "f": "json",
    "where": "1=1",
    "outFields": "nome,latitude,longitude,municipio,hora_1,horas_3,horas_6,horas_12,horas_24,ultima_leitura_data_hora",
    "geometry": "-35.15,-8.25,-34.75,-7.90",
    "geometryType": "esriGeometryEnvelope",
    "inSR": "4326",
    "spatialRel": "esriSpatialRelIntersects",
    "resultRecordCount": "100",
}


def _safe_mm(value) -> float:
    """Converte valor do ArcGIS para mm; -1 = sensor offline → 0."""
    v = float(value or 0)
    return max(v, 0.0)


async def fetch_apac_stations() -> list[dict]:
    """
    Retorna lista de estações APAC ativas na RMR com leituras de chuva.
    Cada item: {nome, municipio, lat, lon, hora_1_mm, horas_3_mm, horas_6_mm, horas_24_mm, ultima_leitura}
    """
    cached = cache_get(_CACHE_KEY, _CACHE_TTL)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            r = await client.get(_URL, params=_PARAMS)
            r.raise_for_status()
            data = r.json()

        results = []
        for feat in data.get("features", []):
            attr = feat.get("attributes", {})
            lat = attr.get("latitude")
            lon = attr.get("longitude")
            if lat is None or lon is None:
                continue

            results.append({
                "nome":          attr.get("nome", "Estação APAC"),
                "municipio":     attr.get("municipio", ""),
                "lat":           float(lat),
                "lon":           float(lon),
                "hora_1_mm":     _safe_mm(attr.get("hora_1")),
                "horas_3_mm":    _safe_mm(attr.get("horas_3")),
                "horas_6_mm":    _safe_mm(attr.get("horas_6")),
                "horas_24_mm":   _safe_mm(attr.get("horas_24")),
                "ultima_leitura": attr.get("ultima_leitura_data_hora", ""),
            })

        cache_set(_CACHE_KEY, results)
        logger.info(f"APAC estações RMR: {len(results)} carregadas")
        return results

    except Exception as e:
        logger.debug(f"APAC stations falhou: {type(e).__name__}: {e}")
        cache_set(_CACHE_KEY, [])
        return []
