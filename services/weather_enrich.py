"""
Enriquece o snapshot APAC com campos derivados úteis para a UI.
Mantém o snapshot bruto intacto e adiciona:
  - condition       (string legível derivada de rain_1h_mm)
  - rain_level      ('none' | 'leve' | 'moderada' | 'forte' | 'severa')
  - rain_trend      ('estavel' | 'subindo' | 'descendo') — só quando worker calcular
  - is_day          (booleano baseado em hora local Recife)
  - is_stale        (true se captured_at > 30 min)
  - freshness_s     (segundos desde a leitura)
  - alert           (boletim APAC cruzado em RMR)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_TZ_RECIFE = ZoneInfo("America/Recife")
_STALE_AFTER_S = 30 * 60  # 30 min


# ── Classificação de chuva ───────────────────────────────────────────────

def classify_rain(rain_1h_mm: Optional[float]) -> tuple[str, str]:
    """Retorna (rain_level, condition_label)."""
    if rain_1h_mm is None:
        return "none", "Sem leitura"
    r = float(rain_1h_mm)
    if r < 0.2:   return "none",     "Sem chuva"
    if r < 2.5:   return "leve",     "Chuva leve"
    if r < 10:    return "moderada", "Chuva moderada"
    if r < 30:    return "forte",    "Chuva forte"
    return            "severa",      "Chuva severa"


# ── Day/Night em Recife ───────────────────────────────────────────────────

def is_day_recife(now: Optional[datetime] = None) -> bool:
    """Heurística simples: dia entre 5h e 18h locais."""
    now = now or datetime.now(_TZ_RECIFE)
    h = now.astimezone(_TZ_RECIFE).hour
    return 5 <= h < 18


# ── Freshness ─────────────────────────────────────────────────────────────

def freshness(captured_at: Optional[str]) -> tuple[int, bool]:
    """Segundos desde captured_at + flag stale (>30min)."""
    if not captured_at:
        return 0, True
    try:
        ts = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta_s = int((datetime.now(timezone.utc) - ts).total_seconds())
        return max(0, delta_s), delta_s > _STALE_AFTER_S
    except Exception as e:
        logger.debug(f"freshness parse failed: {e}")
        return 0, True


# ── Alert APAC (cruzamento com /api/apac/boletim) ────────────────────────

async def apac_alert() -> Optional[dict]:
    """
    Retorna o boletim sintético APAC consolidado (mesma lógica de routers/apac.py).
    Calculado aqui pra evitar acoplamento via HTTP interno.
    """
    try:
        from services.apac_official import fetch_cemaden, RMR_BBOX

        stations = await fetch_cemaden()
        rmr = [
            s for s in stations
            if RMR_BBOX[0] <= s.lat <= RMR_BBOX[1] and RMR_BBOX[2] <= s.lon <= RMR_BBOX[3]
        ]
        if not rmr:
            return None

        max_rain = max((s.rain_mm or 0.0) for s in rmr)
        if   max_rain >= 30: nivel, titulo = "SEVERO",   "Chuva muito forte na RMR"
        elif max_rain >= 15: nivel, titulo = "ALTO",     "Chuva forte na RMR"
        elif max_rain >=  5: nivel, titulo = "MODERADO", "Chuva moderada na RMR"
        elif max_rain >=  1: nivel, titulo = "ATENCAO",  "Chuva leve na RMR"
        else:                nivel, titulo = "SEGURO",   "Sem chuva expressiva"

        top = sorted(rmr, key=lambda s: -(s.rain_mm or 0))[:3]
        return {
            "nivel":        nivel,
            "titulo":       titulo,
            "max_mm":       round(max_rain, 1),
            "estacoes":     [{"nome": s.name, "mm": s.rain_mm} for s in top],
            "coletado_em":  max(s.captured_at for s in rmr),
        }
    except Exception as e:
        logger.debug(f"apac_alert failed: {e}")
        return None


# ── Pluviômetros pra WeatherOutlook ──────────────────────────────────────

def _haversine_m(lat1, lon1, lat2, lon2):
    import math
    R = 6_371_000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def nearest_rmr_stations(lat: float, lon: float, limit: int = 4, max_km: float = 50) -> list[dict]:
    """Pluviômetros CEMADEN mais próximos do ponto. Inclui distância em km."""
    try:
        from services.apac_official import fetch_cemaden, RMR_BBOX
        stations = await fetch_cemaden()
        rmr = [
            s for s in stations
            if RMR_BBOX[0] <= s.lat <= RMR_BBOX[1] and RMR_BBOX[2] <= s.lon <= RMR_BBOX[3]
        ]
        scored = []
        for s in rmr:
            d_m = _haversine_m(lat, lon, s.lat, s.lon)
            if d_m / 1000 > max_km:
                continue
            scored.append((d_m, s))
        scored.sort(key=lambda x: x[0])
        return [
            {
                "name":        s.name,
                "rain_mm":     s.rain_mm,
                "lat":         s.lat,
                "lon":         s.lon,
                "distance_km": round(d_m / 1000, 1),
                "captured_at": s.captured_at,
            }
            for d_m, s in scored[:limit]
        ]
    except Exception as e:
        logger.debug(f"nearest_rmr_stations failed: {e}")
        return []


async def top_rmr_stations(limit: int = 5) -> list[dict]:
    """Top pluviômetros RMR por intensidade de chuva (não por distância)."""
    try:
        from services.apac_official import fetch_cemaden, RMR_BBOX
        stations = await fetch_cemaden()
        rmr = [
            s for s in stations
            if RMR_BBOX[0] <= s.lat <= RMR_BBOX[1] and RMR_BBOX[2] <= s.lon <= RMR_BBOX[3]
        ]
        top = sorted(rmr, key=lambda s: -(s.rain_mm or 0))[:limit]
        return [
            {
                "name":     s.name,
                "rain_mm":  s.rain_mm,
                "lat":      s.lat,
                "lon":      s.lon,
                "captured_at": s.captured_at,
            }
            for s in top
        ]
    except Exception as e:
        logger.debug(f"top_rmr_stations failed: {e}")
        return []


# ── Climatologia ──────────────────────────────────────────────────────────

async def monthly_climatology(lat: float, lon: float, month: Optional[int] = None) -> Optional[dict]:
    """
    Retorna a média climatológica para o mês atual na estação mais próxima.
    Não é forecast — é referência histórica.
    """
    try:
        from services.apac_official import nearest_station
        st = await nearest_station(lat, lon, "climatologico", max_km=50)
        if not st:
            return None
        m = month or datetime.now(_TZ_RECIFE).month
        # st.raw deve ter chave 'meses' ou similar; o parser climatologico retorna a leitura corrente já
        # mas guardamos o raw pra poder extrair média mensal
        raw = getattr(st, "raw", None) or {}
        # tentativa best-effort com chaves comuns
        media = (
            raw.get(f"mes_{m}")
            or raw.get(f"m{m}")
            or raw.get("media_mm")
            or None
        )
        return {
            "station_name": st.name,
            "month":        m,
            "media_mm":     float(media) if media is not None else None,
        }
    except Exception as e:
        logger.debug(f"monthly_climatology failed: {e}")
        return None


# ── Enrich principal ──────────────────────────────────────────────────────

async def enrich_weather(snap: Optional[dict]) -> dict:
    """
    Recebe o snapshot bruto APAC (de weather_at) e devolve o shape enriquecido
    pronto pra UI. Se snap=None, retorna placeholder vazio coerente.
    """
    if not snap:
        return {
            "rain_1h_mm":   None,
            "rain_24h_mm":  None,
            "temp_c":       None,
            "humidity_pct": None,
            "wind_kmh":     None,
            "station_name": None,
            "source":       "unavailable",
            "captured_at":  None,
            "condition":    "Sem leitura",
            "rain_level":   "none",
            "rain_trend":   "estavel",
            "is_day":       is_day_recife(),
            "is_stale":     True,
            "freshness_s":  0,
            "alert":        await apac_alert(),
        }

    rain_level, condition = classify_rain(snap.get("rain_1h_mm"))
    fresh_s, stale = freshness(snap.get("captured_at"))

    enriched = dict(snap)
    enriched.update({
        "condition":   condition,
        "rain_level":  rain_level,
        "rain_trend":  "estavel",       # placeholder até worker calcular delta
        "is_day":      is_day_recife(),
        "is_stale":    stale,
        "freshness_s": fresh_s,
        "alert":       await apac_alert(),
    })
    return enriched
