"""
Router /api/apac/* — endpoints derivados das fontes oficiais APAC.

Não usa mais scraper HTML. Toda informação vem dos 3 JSONs oficiais
expostos em http://dados.apac.pe.gov.br:41120/ (ver services.apac_official).
"""
from fastapi import APIRouter, Query

from services.apac_official import RMR_BBOX, fetch_cemaden, fetch_meteorologia24h

router = APIRouter()


@router.get("/api/apac/boletim")
async def get_apac_boletim():
    """
    Compatível com o frontend: retorna um boletim sintético baseado nas
    leituras correntes do CEMADEN dentro da RMR.

    `nivel` é classificado pelo maior valor de chuva em qualquer estação RMR:
      >= 30 mm/h → SEVERO
      >= 15 mm/h → ALTO
      >=  5 mm/h → MODERADO
      >=  1 mm/h → ATENCAO
      <  1 mm/h → SEGURO

    Frontend exibe banner conforme nível.
    """
    stations = await fetch_cemaden()
    rmr = [
        s for s in stations
        if RMR_BBOX[0] <= s.lat <= RMR_BBOX[1] and RMR_BBOX[2] <= s.lon <= RMR_BBOX[3]
    ]
    if not rmr:
        return {"boletim": None}

    max_rain = max((s.rain_mm or 0.0) for s in rmr)
    if max_rain >= 30:
        nivel, titulo = "SEVERO", "Chuva muito forte detectada na RMR"
    elif max_rain >= 15:
        nivel, titulo = "ALTO", "Chuva forte registrada na RMR"
    elif max_rain >= 5:
        nivel, titulo = "MODERADO", "Chuva moderada em estações da RMR"
    elif max_rain >= 1:
        nivel, titulo = "ATENCAO", "Chuva leve em algumas estações da RMR"
    else:
        nivel, titulo = "SEGURO", "Sem chuva expressiva na RMR no momento"

    top_stations = sorted(rmr, key=lambda s: -(s.rain_mm or 0))[:5]

    return {
        "boletim": {
            "fonte":         "APAC CEMADEN",
            "titulo":        titulo,
            "nivel":         nivel,
            "afeta_recife": True,
            "max_chuva_mm":  round(max_rain, 1),
            "estacoes":      [
                {"nome": s.name, "chuva_mm": s.rain_mm, "lat": s.lat, "lon": s.lon}
                for s in top_stations
            ],
            "coletado_em":   max(s.captured_at for s in rmr),
        }
    }


@router.get("/api/apac/rain-stations")
async def list_rain_stations(
    bbox: str | None = Query(None, description="min_lat,max_lat,min_lon,max_lon"),
):
    """Lista estações cemaden — útil para overlay opcional no mapa."""
    stations = await fetch_cemaden()
    if bbox:
        try:
            mn_lat, mx_lat, mn_lon, mx_lon = [float(x) for x in bbox.split(",")]
        except Exception:
            mn_lat, mx_lat, mn_lon, mx_lon = RMR_BBOX
    else:
        mn_lat, mx_lat, mn_lon, mx_lon = RMR_BBOX

    filtered = [s for s in stations if mn_lat <= s.lat <= mx_lat and mn_lon <= s.lon <= mx_lon]
    return {
        "count": len(filtered),
        "stations": [
            {
                "id": s.id, "name": s.name,
                "lat": s.lat, "lon": s.lon,
                "rain_mm": s.rain_mm,
                "municipio": s.municipio,
                "captured_at": s.captured_at,
            }
            for s in filtered
        ],
    }


@router.get("/api/apac/meteo-stations")
async def list_meteo_stations():
    """Lista estações agrometeorológicas (temp/umid/vento) — leitura corrente."""
    stations = await fetch_meteorologia24h()
    return {
        "count": len(stations),
        "stations": [
            {
                "id": s.id, "name": s.name,
                "lat": s.lat, "lon": s.lon,
                "temp_c": s.temp_c, "humidity_pct": s.humidity_pct, "wind_kmh": s.wind_kmh,
                "municipio": s.municipio,
                "captured_at": s.captured_at,
            }
            for s in stations
        ],
    }
