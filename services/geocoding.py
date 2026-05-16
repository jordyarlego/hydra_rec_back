"""
Reverse geocoding cidadão.

Usa Nominatim (OSM) — gratuito, sem chave. Pra MVP serve; em prod
pode trocar por Google Geocoding API se houver budget.

LGPD: lat/lon do report já é público (visível no mapa). Não há
agregação adicional aqui — só resolução de endereço humano.

Cache em memória + TTL 24h pra reduzir hits (Nominatim pede polidez).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_NOMINATIM = "https://nominatim.openstreetmap.org/reverse"
_HEADERS = {
    # Polidez Nominatim: identificar app + e-mail
    "User-Agent": "HydraRec/3.0 (https://github.com/jordyarlego/hydra_rec_back; contato@hydrarec.app)",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

# Cache simples em memória — { (round(lat,4), round(lon,4)): result }
# Round em 4 casas decimais ≈ ~11m de precisão, evita cache explodir.
_CACHE: dict[tuple[float, float], dict[str, Any]] = {}
_CACHE_LOCK = asyncio.Lock()


def _key(lat: float, lon: float) -> tuple[float, float]:
    return (round(float(lat), 4), round(float(lon), 4))


async def reverse_geocode(lat: float, lon: float) -> dict[str, Any]:
    """
    Retorna {street, number, neighborhood, city, full_address, source}.
    Em falha (timeout, sem internet) retorna {source: 'fallback', full_address: None}.
    """
    cache_key = _key(lat, lon)
    async with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached:
            return cached

    try:
        async with httpx.AsyncClient(timeout=6.0, headers=_HEADERS) as client:
            res = await client.get(_NOMINATIM, params={
                "lat": lat,
                "lon": lon,
                "format": "jsonv2",
                "addressdetails": 1,
                "zoom": 18,  # rua/edifício
            })
            res.raise_for_status()
            data = res.json()
    except Exception as e:
        logger.warning("reverse_geocode falhou em (%s,%s): %s", lat, lon, e)
        return {
            "source": "fallback",
            "street": None,
            "number": None,
            "neighborhood": None,
            "city": None,
            "full_address": None,
        }

    addr = data.get("address") or {}
    street = addr.get("road") or addr.get("pedestrian") or addr.get("path")
    number = addr.get("house_number")
    neighborhood = addr.get("suburb") or addr.get("neighbourhood") or addr.get("city_district")
    city = addr.get("city") or addr.get("town") or addr.get("municipality") or "Recife"
    full = data.get("display_name")

    result = {
        "source": "nominatim",
        "street": street,
        "number": number,
        "neighborhood": neighborhood,
        "city": city,
        "full_address": full,
    }

    async with _CACHE_LOCK:
        _CACHE[cache_key] = result
        # Bound: cache não passa de 5000 entradas
        if len(_CACHE) > 5000:
            # FIFO simples: remove primeiros 500
            for k in list(_CACHE.keys())[:500]:
                _CACHE.pop(k, None)

    return result


async def nearby_landmarks(lat: float, lon: float, radius_m: int = 200) -> list[dict[str, Any]]:
    """
    Retorna pontos de referência próximos (escolas, hospitais, comércios).
    Usa Overpass API. Falha gracioso → lista vazia.
    """
    overpass_url = "https://overpass-api.de/api/interpreter"
    # Quanto menor o raio, mais rápido. 200m é suficiente pra contexto cívico.
    query = f"""
    [out:json][timeout:8];
    (
      node["amenity"~"school|hospital|pharmacy|police|fire_station"](around:{radius_m},{lat},{lon});
      node["shop"](around:{radius_m},{lat},{lon});
    );
    out body 5;
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(overpass_url, data={"data": query})
            res.raise_for_status()
            data = res.json()
    except Exception as e:
        logger.warning("nearby_landmarks falhou: %s", e)
        return []

    landmarks = []
    for el in (data.get("elements") or [])[:5]:
        tags = el.get("tags") or {}
        name = tags.get("name")
        if not name:
            continue
        kind = (
            tags.get("amenity")
            or tags.get("shop")
            or "ponto"
        )
        landmarks.append({
            "name": name,
            "kind": kind,
            "lat": el.get("lat"),
            "lon": el.get("lon"),
        })
    return landmarks
