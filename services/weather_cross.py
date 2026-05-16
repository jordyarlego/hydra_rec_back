"""
Cruzamento meteorológico de reports.

Quando um report é criado, persistimos um snapshot APAC do clima no momento e
no ponto exato. Isso permite responder depois "no momento do report estava
chovendo X mm/h na estação Y a Zm".

Fonte: services.apac_official.weather_at()
Tabela: public.weather_snapshots
"""
from __future__ import annotations

import logging
from typing import Optional

from services.apac_official import weather_at
from services.supabase_client import get_service_client

logger = logging.getLogger(__name__)


async def snapshot_for_point(lat: float, lon: float) -> Optional[dict]:
    """
    Captura snapshot APAC e persiste em `weather_snapshots`.

    Retorna o dict do snapshot (com `id` se gravou). Retorna None se não houver
    nenhuma estação APAC dentro do raio — nesse caso o report fica sem
    cruzamento (campo `weather_snapshot_id` permanece null).
    """
    snap = await weather_at(lat, lon)
    if snap is None:
        logger.info("weather_cross: nenhuma estação APAC para (%s, %s)", lat, lon)
        return None

    row = {
        "lat":                lat,
        "lon":                lon,
        "station_id":         snap.get("station_id"),
        "station_name":       snap.get("station_name"),
        "station_distance_m": snap.get("station_distance_m"),
        "rain_1h_mm":         snap.get("rain_1h_mm"),
        "rain_24h_mm":        snap.get("rain_24h_mm"),
        "temp_c":             snap.get("temp_c"),
        "humidity_pct":       snap.get("humidity_pct"),
        "wind_kmh":           snap.get("wind_kmh"),
        "source":             snap.get("source") or "none",
        "raw":                {
            "rain_station":  snap.get("rain_station"),
            "meteo_station": snap.get("meteo_station"),
            "captured_at":   snap.get("captured_at"),
        },
    }

    try:
        client = get_service_client()
        res = client.table("weather_snapshots").insert(row).execute()
        if res.data:
            inserted = res.data[0]
            snap["id"] = inserted.get("id")
            return snap
    except Exception as e:
        # Tabela ainda não existe (migration V3 não aplicada) — degrada elegante.
        msg = str(e).lower()
        if "relation" in msg or "does not exist" in msg or "schema" in msg:
            logger.warning("weather_cross: tabela weather_snapshots ausente (rode migrations/v3_civic_reports.sql)")
        else:
            logger.warning("weather_cross: falha ao persistir snapshot: %s: %s", type(e).__name__, e)

    return snap  # retorna mesmo sem persistir — caller decide o que fazer
