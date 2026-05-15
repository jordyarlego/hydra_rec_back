"""
INMET Alertas — avisos meteorológicos ativos para Pernambuco.
Endpoint público: https://apitempo.inmet.gov.br/AVISO/{YYYY-MM-DD}
Cache 30 min (alertas mudam raramente durante o dia).
"""
import logging
from datetime import datetime, timezone

import httpx
from services.cache import cache_get, cache_set

logger = logging.getLogger(__name__)

_CACHE_KEY = "inmet_alerts_pe"
_CACHE_TTL  = 1800  # 30 min

_PE_TERMS = [
    "pernambuco", " pe,", "pe ", "recife", "olinda",
    "região metropolitana", "rmr", "grande recife",
    "litoral pernambucano", "todo o estado",
]

# Traduz severidade textual para bônus de score na rota
_SEV_BONUS = {
    "vermelho":  30,
    "laranja":   18,
    "amarelo":    8,
    "azul":       3,
    "cinza":      2,
}


def _affects_pe(alert: dict) -> bool:
    text = " ".join(
        str(v) for v in [
            alert.get("nm_estado", ""),
            alert.get("ds_locais", ""),
            alert.get("ds_municipios", ""),
            alert.get("nome_estado", ""),
            alert.get("sigla_estado", ""),
        ]
    ).lower()
    return "pe" in text.split() or any(k in text for k in _PE_TERMS)


def _bonus(alert: dict) -> int:
    sev = str(alert.get("ds_severidade", alert.get("severidade", ""))).lower()
    for k, v in _SEV_BONUS.items():
        if k in sev:
            return v
    return 5


async def fetch_inmet_alerts() -> list[dict]:
    """Retorna lista de alertas INMET ativos para PE. Vazio se indisponível."""
    cached = cache_get(_CACHE_KEY, _CACHE_TTL)
    if cached is not None:
        return cached

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    urls = [
        f"https://apitempo.inmet.gov.br/AVISO/{today}",
        "https://apitempo.inmet.gov.br/AVISO/todos",
        "https://apitempo.inmet.gov.br/avisos/ativos",
    ]

    results = []
    try:
        async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
            for url in urls:
                try:
                    r = await client.get(url)
                    if r.status_code != 200:
                        continue
                    data = r.json()
                    if not isinstance(data, list):
                        data = data.get("avisos") or data.get("alertas") or []
                    for alert in data:
                        if not isinstance(alert, dict):
                            continue
                        if not _affects_pe(alert):
                            continue
                        results.append({
                            "fonte":      "INMET",
                            "evento":     alert.get("ds_evento") or alert.get("evento", "Alerta meteorológico"),
                            "severidade": alert.get("ds_severidade") or alert.get("severidade", "Amarelo"),
                            "inicio":     alert.get("dt_inicio"),
                            "fim":        alert.get("dt_fim"),
                            "score_bonus": _bonus(alert),
                        })
                    if results:
                        break
                except Exception:
                    continue
    except Exception as e:
        logger.debug(f"INMET alertas fetch: {type(e).__name__}")

    cache_set(_CACHE_KEY, results)
    if results:
        logger.info(f"INMET alertas PE: {len(results)} ativo(s)")
    return results
