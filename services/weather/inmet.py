import math
import logging
import httpx
from datetime import datetime, timezone
from services.cache import cache_get, cache_get_stale, cache_set

logger = logging.getLogger(__name__)

INMET_STATIONS = [
    ("A301", -8.0590, -34.9588, "Recife"),
    ("A357", -7.9858, -34.8313, "Olinda"),
]

# TTL normal: 30 min. Stale cache é usado sem limite de tempo se API cair.
_CACHE_TTL = 1800


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(a))


def _parse_reading(row: dict, code: str, name: str) -> dict:
    return {
        "source":            f"INMET {code}",
        "station":           name,
        "rain_last_hour_mm": float(row.get("CHUVA") or row.get("P_FORA") or 0),
        "temp_c":            float(row.get("TEM_INS") or row.get("TEMP") or 0),
        "humidity":          float(row.get("UMD_INS") or row.get("UR_INS") or 0),
        "pressure":          float(row.get("PRE_INS") or row.get("P_INS") or 0),
        "wind_speed_kmh":    float(row.get("VEN_VEL") or 0) * 3.6,
        "timestamp":         row.get("HR_MEDICAO") or row.get("DT_MEDICAO"),
    }


async def fetch_inmet_nearest(lat: float, lon: float) -> dict | None:
    nearest  = min(INMET_STATIONS, key=lambda s: _haversine(lat, lon, s[1], s[2]))
    code, _, _, name = nearest
    cache_key = f"inmet_{code}"

    # Cache fresco: retorna imediatamente
    cached = cache_get(cache_key, _CACHE_TTL)
    if cached:
        return cached

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Tenta múltiplos endpoints em ordem — INMET muda URLs com frequência
    urls = [
        f"https://apitempo.inmet.gov.br/estacao/dados/{code}/{today}",
        f"https://apitempo.inmet.gov.br/ESTACAO/AUTO/{code}",
        f"https://apitempo.inmet.gov.br/estacao/{code}/ultimas",
    ]

    for url in urls:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(url)
                if r.status_code != 200:
                    continue
                data = r.json()

            if not data or not isinstance(data, list):
                continue

            latest = next(
                (d for d in reversed(data)
                 if isinstance(d, dict) and d.get("CHUVA") is not None),
                None,
            )
            if not latest:
                # Tenta sem filtrar CHUVA
                latest = next((d for d in reversed(data) if isinstance(d, dict)), None)
            if not latest:
                continue

            result = _parse_reading(latest, code, name)
            cache_set(cache_key, result)
            logger.info(f"INMET {code}: ok via {url.split('/')[-2]}")
            return result

        except Exception as exc:
            logger.debug(f"INMET {code} @ {url}: {type(exc).__name__}")
            continue

    # Fallback: retorna dado em cache mesmo que expirado (stale)
    stale = cache_get_stale(cache_key)
    if stale:
        logger.info(f"INMET {code}: usando cache stale ({stale.get('_stale_age_min', '?')} min atrás)")
        return stale

    logger.warning(f"INMET {code}: todas as tentativas falharam, sem cache disponível")
    return None
