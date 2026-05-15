"""
Cemaden — alertas recentes de desastres naturais para Recife/PE.
Centro Nacional de Monitoramento e Alertas de Desastres Naturais (MCTI).
API pública: https://alertas2.cemaden.gov.br/api/
Cache 30 min.
"""
import logging
import httpx
from services.cache import cache_get, cache_set

logger = logging.getLogger(__name__)

_CACHE_KEY = "cemaden_alerts_recife"
_CACHE_TTL  = 1800  # 30 min

# IBGE municipal code para Recife
_RECIFE_IBGE = "2611606"
# IBGE state code for PE
_PE_IBGE = "26"

_CEMADEN_URLS = [
    f"https://alertas2.cemaden.gov.br/api/alertasRecentes?municipioId={_RECIFE_IBGE}",
    f"https://alertas2.cemaden.gov.br/api/rankingdiario?codigoIbge={_RECIFE_IBGE}",
    f"https://alertas2.cemaden.gov.br/api/getAlertas?geocodigo={_RECIFE_IBGE}",
]

_TIPO_MAP = {
    "enxurrada":     {"label": "Enxurrada",         "score_bonus": 25},
    "inundacao":     {"label": "Inundação",          "score_bonus": 30},
    "alagamento":    {"label": "Alagamento urbano",  "score_bonus": 20},
    "deslizamento":  {"label": "Deslizamento",       "score_bonus": 25},
    "chuva":         {"label": "Chuva intensa",      "score_bonus": 15},
    "temporal":      {"label": "Temporal",           "score_bonus": 18},
    "vendaval":      {"label": "Vendaval",           "score_bonus": 10},
    "granizo":       {"label": "Granizo",            "score_bonus": 8},
}


def _map_tipo(tipo_raw: str) -> dict:
    t = str(tipo_raw).lower()
    for key, val in _TIPO_MAP.items():
        if key in t:
            return val
    return {"label": tipo_raw.strip().capitalize(), "score_bonus": 10}


async def fetch_cemaden_alerts() -> list[dict]:
    """Retorna alertas Cemaden ativos para Recife. Lista vazia se indisponível."""
    cached = cache_get(_CACHE_KEY, _CACHE_TTL)
    if cached is not None:
        return cached

    results: list[dict] = []
    try:
        async with httpx.AsyncClient(
            timeout=6.0,
            follow_redirects=True,
            headers={"User-Agent": "HydraRec/2.0 (TCC UFPE 2026; jordyarlego@gmail.com)"},
        ) as client:
            for url in _CEMADEN_URLS:
                try:
                    r = await client.get(url)
                    if r.status_code != 200:
                        continue
                    data = r.json()

                    # Normalize: some endpoints return list, others return dict
                    items: list = []
                    if isinstance(data, list):
                        items = data
                    elif isinstance(data, dict):
                        for key in ("alertas", "dados", "results", "items", "content"):
                            if isinstance(data.get(key), list):
                                items = data[key]
                                break

                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        tipo_raw = (
                            item.get("descricao_tipo_risco")
                            or item.get("tipo")
                            or item.get("tipoRisco")
                            or item.get("ds_tipo")
                            or "Alerta"
                        )
                        mapped = _map_tipo(tipo_raw)
                        results.append({
                            "fonte":       "Cemaden",
                            "evento":      mapped["label"],
                            "severidade":  item.get("severidade") or item.get("nivel") or "Moderado",
                            "municipio":   item.get("nm_municipio") or item.get("municipio") or "Recife",
                            "data":        item.get("data_hora") or item.get("dt_emissao"),
                            "score_bonus": mapped["score_bonus"],
                        })

                    if results:
                        logger.info(f"Cemaden: {len(results)} alerta(s) para Recife")
                        break

                except Exception as exc:
                    logger.debug(f"Cemaden {url}: {type(exc).__name__}")
                    continue

    except Exception as e:
        logger.debug(f"Cemaden fetch: {type(e).__name__}")

    cache_set(_CACHE_KEY, results)
    return results
