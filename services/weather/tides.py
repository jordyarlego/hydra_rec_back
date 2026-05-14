import httpx
from bs4 import BeautifulSoup
from services.cache import cache_get, cache_set


async def scrape_tide_data() -> dict:
    cached = cache_get("tide", 3600)
    if cached:
        return cached

    headers = {"User-Agent": "HydraRec/2.0 (TCC UFPE; contato: hydrarec@example.com)"}
    url = "https://tabuademares.com/br/pernambuco/recife"
    try:
        async with httpx.AsyncClient(headers=headers, timeout=10.0) as client:
            resp = await client.get(url)
            soup = BeautifulSoup(resp.text, "html.parser")
            tide_span = soup.find("span", class_="tabla_mareas_marea_altura_numero")
            if tide_span:
                height = float(tide_span.text.strip().replace(",", "."))
                trend = "Alta" if height >= 1.5 else "Baixa"
                result = {"height": height, "trend": trend}
                cache_set("tide", result)
                return result
        return {"height": 1.4, "trend": "Valor não lido"}
    except Exception as e:
        print(f"Tide scrape error: {e}")
        return {"height": 1.5, "trend": "Desconhecido (Offline)"}
