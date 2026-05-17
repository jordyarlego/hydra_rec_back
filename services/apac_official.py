"""
APAC — Fonte ÚNICA de dados meteorológicos para HydraRec V3.

Consome os 3 endpoints oficiais publicados pela APAC em
http://dados.apac.pe.gov.br:41120/:

  · /cemaden/                       → pluviômetros (chuva atual em mm)
  · /meteorologia24h/               → temp / umidade / vento (estações agromet)
  · /blank_json_climatologico/      → médias climáticas históricas

Funções públicas:
  · fetch_cemaden()         → list[Station]
  · fetch_meteorologia24h() → list[Station]
  · fetch_climatologico()   → list[Station]
  · nearest_station(lat, lon, kind) → Station | None
  · weather_at(lat, lon)    → dict | None  (snapshot consolidado)

Cache TTLs:
  cemaden          5  min
  meteorologia24h  15 min
  climatologico    6  h
"""
from __future__ import annotations

import json
import math
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Literal, Optional

import httpx

from services.cache import cache_get, cache_get_stale, cache_set

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Configuração
# ─────────────────────────────────────────────────────────────

_BASE = "http://dados.apac.pe.gov.br:41120"

_EP = {
    "cemaden":         f"{_BASE}/cemaden/",
    "meteorologia24h": f"{_BASE}/meteorologia24h/",
    "climatologico":   f"{_BASE}/blank_json_climatologico/",
}

_TTL = {
    "cemaden":         300,    # 5 min
    "meteorologia24h": 900,    # 15 min
    "climatologico":   21600,  # 6 h
}

_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)

_HEADERS = {
    "User-Agent": "HydraRec/3.0 (civic monitoring Recife)"
}

# Bounding box RMR (mais largo que o constraint do banco)
RMR_BBOX = (-8.40, -7.80, -35.20, -34.70)  # min_lat, max_lat, min_lon, max_lon


# ─────────────────────────────────────────────────────────────
# Tipos
# ─────────────────────────────────────────────────────────────

Kind = Literal["cemaden", "meteorologia24h", "climatologico"]


@dataclass
class Station:
    id: str
    name: str
    lat: float
    lon: float
    kind: Kind
    captured_at: str  # ISO

    # Medidas (presença depende do kind)
    rain_mm: Optional[float] = None        # cemaden — leitura corrente
    temp_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    wind_kmh: Optional[float] = None

    # Auxiliares
    municipio: Optional[str] = None
    raw: Optional[dict] = None


# ─────────────────────────────────────────────────────────────
# HTTP helper
# ─────────────────────────────────────────────────────────────

async def _fetch_json(url: str) -> Optional[list | dict]:
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=_HEADERS) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("APAC fetch falhou (%s): %s: %s", url, type(e).__name__, e)
        return None


def _safe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _parse_dados(record: dict) -> Optional[dict]:
    """`Dados_completos` vem como STRING JSON nos endpoints cemaden/meteo24h."""
    inner = record.get("Dados_completos")
    if inner is None:
        return None
    if isinstance(inner, dict):
        return inner
    try:
        return json.loads(inner)
    except (json.JSONDecodeError, TypeError):
        return None


def _in_bbox(lat: float, lon: float, bbox=RMR_BBOX) -> bool:
    return bbox[0] <= lat <= bbox[1] and bbox[2] <= lon <= bbox[3]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_apac_ts(raw) -> str:
    """APAC publica timestamps como 'YYYY-MM-DD HH:MM:SS[.f]' sem timezone.
    O dado é UTC. Sem marcar isso, o JS no browser interpreta como horário
    LOCAL e mostra hora errada (ex: '23h' enquanto em Recife são 20h).

    Retorna ISO com offset UTC explícito: '2026-05-17T23:40:00+00:00'.
    """
    if not raw:
        return _now_iso()
    s = str(raw).strip()
    if not s:
        return _now_iso()
    # Se já tem timezone (T...Z, +HH:MM, -HH:MM), devolve como está
    if "T" in s and ("Z" in s or "+" in s[10:] or "-" in s[10:]):
        return s
    s = s.replace(" ", "T")
    if "." in s:
        s = s.split(".", 1)[0]
    return s + "+00:00"


# ─────────────────────────────────────────────────────────────
# Parsers — cada endpoint vira list[Station]
# ─────────────────────────────────────────────────────────────

def _clean_station_name(raw: str) -> str:
    """
    Remove prefixos [CEMADEN], [APAC]... e sufixos numéricos do código
    interno ("Janga 2" → "Janga", "Cruz de Rebouças 2" → "Cruz de Rebouças").
    """
    import re
    name = str(raw or "").strip()
    # Prefixos institucionais
    name = re.sub(r"^\[(CEMADEN|APAC|INMET|FEMAR)\]\s*", "", name, flags=re.I)
    # Sufixo " 2", " 3" etc — código interno de estação, não interessa ao usuário
    name = re.sub(r"\s+\d+\s*$", "", name)
    # Title case mantendo preposições em minúsculo
    name = name.strip()
    if name.isupper():
        # "PAULISTA" → "Paulista"
        parts = []
        for w in name.split():
            if w.lower() in ("de", "da", "do", "das", "dos", "e"):
                parts.append(w.lower())
            else:
                parts.append(w.title())
        name = " ".join(parts)
        if parts and parts[0] in ("de", "da", "do", "das", "dos"):
            parts[0] = parts[0].title()
            name = " ".join(parts)
    return name


def _parse_cemaden(records: list) -> list[Station]:
    """Parseia + deduplica por (lat, lon, nome) — JSON cemaden costuma ter dups."""
    by_key: dict[str, Station] = {}
    for r in records or []:
        inner = _parse_dados(r)
        if not inner:
            continue
        lat = _safe_float(inner.get("latitude") or inner.get("lat"))
        lon = _safe_float(inner.get("longitude") or inner.get("lon"))
        if lat is None or lon is None:
            continue
        rain = _safe_float(inner.get("chuva"))
        captured = (
            inner.get("dataHora")
            or r.get("Data-hora")
            or _now_iso()
        )
        name = _clean_station_name(r.get("Estação") or inner.get("nome") or "Estação")
        station = Station(
            id=str(r.get("Codigo_gmmc") or inner.get("codestacao") or inner.get("id_estacao") or ""),
            name=name or "Estação",
            lat=lat,
            lon=lon,
            kind="cemaden",
            captured_at=_normalize_apac_ts(captured),
            rain_mm=rain if rain is not None and rain >= 0 else 0.0,
            municipio=inner.get("cidade"),
            raw=inner,
        )
        key = f"{round(lat, 4)}|{round(lon, 4)}|{name.lower()}"
        prev = by_key.get(key)
        # Mantém a leitura mais recente (e/ou maior chuva quando o timestamp empata)
        if prev is None or str(station.captured_at) > str(prev.captured_at):
            by_key[key] = station
        elif (station.rain_mm or 0) > (prev.rain_mm or 0):
            by_key[key] = station
    return list(by_key.values())


def _parse_meteorologia24h(records: list) -> list[Station]:
    """Filtra somente estações com lat/lon E com pelo menos uma medida útil."""
    stations: list[Station] = []
    for r in records or []:
        inner = _parse_dados(r)
        if not inner:
            continue
        lat = _safe_float(inner.get("latitude"))
        lon = _safe_float(inner.get("longitude"))
        if lat is None or lon is None:
            continue

        temp = _safe_float(inner.get("temperatura_ar"))
        umid = _safe_float(inner.get("umidade_relativa"))
        wind_ms = _safe_float(inner.get("velocidade_vento"))
        wind_kmh = round(wind_ms * 3.6, 2) if wind_ms is not None else None
        if temp is None and umid is None and wind_kmh is None:
            continue

        captured = inner.get("dataHora") or r.get("Data-hora") or _now_iso()
        stations.append(Station(
            id=str(r.get("Codigo_gmmc") or inner.get("codestacao") or ""),
            name=_clean_station_name(r.get("Estação") or inner.get("nome") or "Estação"),
            lat=lat,
            lon=lon,
            kind="meteorologia24h",
            captured_at=_normalize_apac_ts(captured),
            temp_c=temp,
            humidity_pct=umid,
            wind_kmh=wind_kmh,
            municipio=inner.get("cidade"),
            raw=inner,
        ))
    return stations


def _parse_climatologico(records: list) -> list[Station]:
    """
    Flatten regiões → lista plana de estações.
    Deduplica por nome+coordenada, preferindo o snapshot com mais medidas reais.
    """
    by_key: dict[str, Station] = {}
    for region in records or []:
        for est in region.get("estacoes", []) or []:
            lat = _safe_float(est.get("latitude"))
            lon = _safe_float(est.get("longitude"))
            if lat is None or lon is None:
                continue
            dados = est.get("dados_completos") or {}
            temp = _safe_float(dados.get("AirTC_Avg") or dados.get("temperatura_ar"))
            umid = _safe_float(dados.get("RH_Avg")    or dados.get("umidade_relativa"))
            wind = _safe_float(
                dados.get("WindSpd_Avg")
                or dados.get("WS_ms_Avg")
                or dados.get("velocidade_vento")
            )
            wind_kmh = round(wind * 3.6, 2) if wind is not None else None

            # Score = quantas medidas vieram preenchidas (preferimos a leitura mais completa)
            score = sum(1 for v in (temp, umid, wind_kmh) if v is not None)
            if score == 0:
                continue  # estação sem nenhuma leitura útil

            captured = dados.get("TIMESTAMP") or region.get("Resumo", {}).get("data_hora_leitura") or _now_iso()
            station = Station(
                id=str(est.get("nomeEstacao") or "")[:96],
                name=_clean_station_name(est.get("nomeEstacao") or "Estação climatológica"),
                lat=lat,
                lon=lon,
                kind="climatologico",
                captured_at=_normalize_apac_ts(captured),
                temp_c=temp,
                humidity_pct=umid,
                wind_kmh=wind_kmh,
                municipio=est.get("municipio"),
                raw=dados,
            )

            key = f"{station.name}|{round(lat, 4)}|{round(lon, 4)}"
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = station
                continue
            # Mantém a leitura com maior score; em empate, a mais recente
            existing_score = sum(1 for v in (existing.temp_c, existing.humidity_pct, existing.wind_kmh) if v is not None)
            if score > existing_score or (score == existing_score and station.captured_at > existing.captured_at):
                by_key[key] = station

    return list(by_key.values())


# ─────────────────────────────────────────────────────────────
# Fetchers — com cache + stale fallback
# ─────────────────────────────────────────────────────────────

async def _fetch_kind(kind: Kind) -> list[Station]:
    cache_key = f"apac:{kind}"
    cached = cache_get(cache_key, _TTL[kind])
    if cached is not None:
        return cached

    raw = await _fetch_json(_EP[kind])
    if raw is None:
        # Fallback: tenta stale
        stale = cache_get_stale(cache_key)
        if stale:
            logger.info("APAC %s: usando cache stale", kind)
            return stale
        cache_set(cache_key, [])
        return []

    if kind == "cemaden":
        stations = _parse_cemaden(raw if isinstance(raw, list) else [])
    elif kind == "meteorologia24h":
        stations = _parse_meteorologia24h(raw if isinstance(raw, list) else [])
    else:
        stations = _parse_climatologico(raw if isinstance(raw, list) else [])

    cache_set(cache_key, stations)
    logger.info("APAC %s: %d estações úteis carregadas", kind, len(stations))
    return stations


async def fetch_cemaden() -> list[Station]:
    return await _fetch_kind("cemaden")


async def fetch_meteorologia24h() -> list[Station]:
    return await _fetch_kind("meteorologia24h")


async def fetch_climatologico() -> list[Station]:
    return await _fetch_kind("climatologico")


# ─────────────────────────────────────────────────────────────
# Geo helpers
# ─────────────────────────────────────────────────────────────

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância em metros."""
    R = 6_371_000
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))


async def nearest_station(lat: float, lon: float, kind: Kind, max_km: float = 50.0) -> Optional[Station]:
    """Estação mais próxima de um ponto, dentro de raio max_km."""
    stations = await _fetch_kind(kind)
    if not stations:
        return None
    best: Optional[Station] = None
    best_dist = max_km * 1000  # converte pra metros
    for s in stations:
        d = haversine_m(lat, lon, s.lat, s.lon)
        if d < best_dist:
            best_dist = d
            best = s
    return best


# ─────────────────────────────────────────────────────────────
# Snapshot consolidado — chuva (cemaden) + meteo (meteorologia24h)
# ─────────────────────────────────────────────────────────────

async def weather_at(lat: float, lon: float) -> Optional[dict]:
    """
    Cruza dados das fontes APAC em tempo real para um ponto.

    Chuva: sempre `cemaden` (única que dá leitura corrente).
    Temp/umid/vento: tenta `meteorologia24h` primeiro (até 80km),
        depois `climatologico` (até 50km) como fallback —
        coberto em Recife, onde meteorologia24h não tem estação na RMR.
    """
    rain_station = await nearest_station(lat, lon, "cemaden", max_km=30)
    meteo_station = await nearest_station(lat, lon, "meteorologia24h", max_km=80)
    meteo_source: Kind = "meteorologia24h"
    if meteo_station is None:
        meteo_station = await nearest_station(lat, lon, "climatologico", max_km=50)
        meteo_source = "climatologico"

    if rain_station is None and meteo_station is None:
        return None

    # Estação primária = mais próxima ao ponto consultado
    candidates = [s for s in (rain_station, meteo_station) if s is not None]
    primary = min(candidates, key=lambda s: haversine_m(lat, lon, s.lat, s.lon))
    primary_distance_m = int(haversine_m(lat, lon, primary.lat, primary.lon))

    return {
        "lat": lat,
        "lon": lon,
        "station_id":         primary.id,
        "station_name":       primary.name,
        "station_distance_m": primary_distance_m,
        "rain_1h_mm":         rain_station.rain_mm  if rain_station  else None,
        "rain_24h_mm":        None,  # cemaden não fornece 24h direto; worker pode calcular delta no futuro
        "temp_c":             meteo_station.temp_c        if meteo_station else None,
        "humidity_pct":       meteo_station.humidity_pct  if meteo_station else None,
        "wind_kmh":           meteo_station.wind_kmh      if meteo_station else None,
        "source":             primary.kind,
        "captured_at":        primary.captured_at,
        "rain_station": (
            {
                "id": rain_station.id,
                "name": rain_station.name,
                "distance_m": int(haversine_m(lat, lon, rain_station.lat, rain_station.lon)),
                "source": "cemaden",
            } if rain_station else None
        ),
        "meteo_station": (
            {
                "id": meteo_station.id,
                "name": meteo_station.name,
                "distance_m": int(haversine_m(lat, lon, meteo_station.lat, meteo_station.lon)),
                "source": meteo_source,
            } if meteo_station else None
        ),
    }


# ─────────────────────────────────────────────────────────────
# Helpers públicos para o router /api/weather
# ─────────────────────────────────────────────────────────────

async def list_stations(kind: Kind, bbox: Optional[tuple] = None) -> list[dict]:
    """Lista todas estações de um tipo, opcionalmente filtradas por bbox."""
    stations = await _fetch_kind(kind)
    if bbox:
        stations = [s for s in stations if _in_bbox(s.lat, s.lon, bbox)]
    return [asdict(s) | {"raw": None} for s in stations]  # esconde raw na resposta API
