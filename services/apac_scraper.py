"""
APAC scraper — Agência Pernambucana de Águas e Clima.

Tenta 3 endpoints em sequência:
  1. API JSON de alertas (se APAC disponibilizar)
  2. Página de meteorologia (scraping HTML)
  3. Fallback: retorna None (frontend omite o banner)

O retorno é um dict padronizado ou None.
Nível segue a escala de cores APAC/INMET:
  verde / amarelo / laranja / vermelho / roxo
"""
import logging
import re
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup
from services.cache import cache_get, cache_set

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "HydraRec/2.0 (TCC UFPE 2026; monitoramento cívico; "
        "contato: jordyarlego@gmail.com)"
    )
}

# Mapeamento de palavras-chave APAC → nível padronizado
_NIVEL_MAP = [
    (["chuva muito forte", "severo", "emergência", "extremo"], "SEVERO"),
    (["chuva forte", "alto risco", "alerta vermelho", "vermelho"], "ALTO"),
    (["chuva moderada", "moderado", "alerta laranja", "laranja"], "MODERADO"),
    (["chuva fraca", "atenção", "alerta amarelo", "amarelo"], "ATENCAO"),
    (["sem alerta", "condições normais", "tempo estável"], "SEGURO"),
]

_RECIFE_KEYWORDS = [
    "recife", "região metropolitana", "rmr", "grande recife",
    "litoral sul", "litoral norte", "todo o estado",
]


def _classify_nivel(text: str) -> str:
    t = text.lower()
    for keywords, nivel in _NIVEL_MAP:
        if any(k in t for k in keywords):
            return nivel
    return "ATENCAO"  # conservador: se há boletim, assume atenção


def _affects_recife(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in _RECIFE_KEYWORDS)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _try_json_api(client: httpx.AsyncClient) -> dict | None:
    """Tenta endpoint JSON da APAC (quando disponível)."""
    urls = [
        "https://www.apac.pe.gov.br/json/alertas.json",
        "https://apac.pe.gov.br/meteorologia/alertas/json",
    ]
    for url in urls:
        try:
            r = await client.get(url, timeout=8.0)
            if r.status_code == 200:
                data = r.json()
                # Normaliza: aceita lista ou dict com "alertas"
                items = data if isinstance(data, list) else data.get("alertas", [])
                if items:
                    item = items[0]
                    texto = item.get("descricao") or item.get("texto") or str(item)
                    return {
                        "fonte":      "APAC JSON",
                        "titulo":     item.get("titulo", "Boletim APAC"),
                        "texto":      texto[:500],
                        "nivel":      item.get("nivel") or _classify_nivel(texto),
                        "afeta_recife": _affects_recife(texto),
                        "url":        url,
                        "coletado_em": _now_iso(),
                    }
        except Exception:
            continue
    return None


async def _try_html_scrape(client: httpx.AsyncClient) -> dict | None:
    """Scraping HTML da página de meteorologia da APAC."""
    urls = [
        "https://www.apac.pe.gov.br/meteorologia",
        "http://www.apac.pe.gov.br/meteorologia",
        "https://www.apac.pe.gov.br/",
    ]
    for url in urls:
        try:
            r = await client.get(url, timeout=10.0)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")

            # Busca blocos de alerta comuns em sites gov
            candidates = []
            for tag in ["article", "div", "section", "p"]:
                for el in soup.find_all(tag):
                    cls = " ".join(el.get("class", []))
                    txt = el.get_text(" ", strip=True)
                    if len(txt) < 30:
                        continue
                    # Prioriza elementos com palavras de alerta
                    if any(w in cls.lower() for w in ["alert", "boletim", "aviso", "noticia"]):
                        candidates.insert(0, txt)
                    elif any(w in txt.lower() for w in ["chuva", "alerta", "precipitação", "risco"]):
                        candidates.append(txt)

            if not candidates:
                continue

            texto = candidates[0][:600]
            return {
                "fonte":        "APAC HTML",
                "titulo":       "Boletim Meteorológico APAC",
                "texto":        texto,
                "nivel":        _classify_nivel(texto),
                "afeta_recife": _affects_recife(texto),
                "url":          url,
                "coletado_em":  _now_iso(),
            }
        except Exception as e:
            logger.debug(f"APAC HTML scrape falhou ({url}): {e}")
            continue
    return None


async def fetch_apac_boletim() -> dict | None:
    """
    Ponto de entrada principal. Retorna boletim normalizado ou None.
    Cache: 30 minutos (boletins APAC mudam raramente).
    """
    cached = cache_get("apac_boletim", 1800)
    if cached is not None:
        return cached

    async with httpx.AsyncClient(
        headers=HEADERS,
        verify=False,           # APAC tem cert issues frequentes
        follow_redirects=True,
        timeout=12.0,
    ) as client:
        result = await _try_json_api(client)
        if not result:
            result = await _try_html_scrape(client)

    # Guarda o resultado (ou sentinela vazio) para evitar bater no site a cada request
    cache_set("apac_boletim", result or {"_empty": True})
    if result:
        logger.info(f"APAC boletim coletado: nível {result['nivel']}")
    else:
        logger.warning("APAC: nenhum boletim obtido — todas as tentativas falharam")

    return result
