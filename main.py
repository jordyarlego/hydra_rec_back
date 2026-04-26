from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import httpx
from bs4 import BeautifulSoup
import math
import asyncio
import time
import os
from dotenv import load_dotenv
from google import genai as google_genai

load_dotenv()

app = FastAPI(title="HydraRec Backend API")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
_gemini_client = google_genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:8000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Coordenadas estáticas para todos os bairros do Recife
# Garante precisão — o geocoding da Open-Meteo não reconhece a maioria
# ---------------------------------------------------------------------------
BAIRRO_COORDS: dict[str, tuple[float, float]] = {
    "Aflitos":                    (-8.0425, -34.9008),
    "Afogados":                   (-8.0931, -34.9133),
    "Água Fria":                  (-7.9933, -34.9006),
    "Alto do Mandu":              (-8.0669, -34.9372),
    "Alto José Bonifácio":        (-8.0350, -34.9261),
    "Alto José do Pinho":         (-8.0261, -34.9264),
    "Apipucos":                   (-8.0178, -34.9181),
    "Areias":                     (-8.0956, -34.9247),
    "Arruda":                     (-8.0272, -34.8961),
    "Barro":                      (-8.1006, -34.9414),
    "Beberibe":                   (-8.0200, -34.8908),
    "Boa Viagem":                 (-8.1180, -34.9000),
    "Boa Vista":                  (-8.0636, -34.8861),
    "Bomba do Hemetério":         (-8.0228, -34.9000),
    "Bongi":                      (-8.0897, -34.9178),
    "Brasília Teimosa":           (-8.0919, -34.8819),
    "Brejo da Guabiraba":         (-8.0042, -34.9342),
    "Brejo de Beberibe":          (-8.0136, -34.9033),
    "Cabanga":                    (-8.0747, -34.8894),
    "Caçote":                     (-8.1025, -34.9122),
    "Campina do Barreto":         (-8.0256, -34.9028),
    "Campo Grande":               (-8.0394, -34.9144),
    "Casa Amarela":               (-8.0261, -34.9181),
    "Casa Forte":                 (-8.0133, -34.9119),
    "Caxangá":                    (-8.0467, -34.9500),
    "Cidade Universitária":       (-8.0522, -34.9481),
    "Coelho":                     (-8.0736, -34.9181),
    "Coelhos":                    (-8.0719, -34.8836),
    "Cohab":                      (-8.0736, -34.9247),
    "Coqueiral":                  (-8.0442, -34.9358),
    "Cordeiro":                   (-8.0706, -34.9283),
    "Córrego do Jenipapo":        (-8.0503, -34.9411),
    "Derby":                      (-8.0556, -34.8983),
    "Dois Irmãos":                (-7.9889, -34.9264),
    "Dois Unidos":                (-8.0094, -34.8958),
    "Encruzilhada":               (-8.0361, -34.9019),
    "Engenho do Meio":            (-8.0567, -34.9372),
    "Espinheiro":                 (-8.0378, -34.9061),
    "Estância":                   (-8.0531, -34.9044),
    "Fundão":                     (-8.0078, -34.8872),
    "Graças":                     (-8.0422, -34.9089),
    "Guabiraba":                  (-8.0006, -34.9306),
    "Hipódromo":                  (-8.0475, -34.9156),
    "Ibura":                      (-8.1264, -34.9372),
    "Ilha do Retiro":             (-8.0711, -34.9161),
    "Ilha Joana Bezerra":         (-8.0792, -34.8900),
    "Imbiribeira":                (-8.1042, -34.9078),
    "Ipsep":                      (-8.1097, -34.9278),
    "Iputinga":                   (-8.0472, -34.9461),
    "Jaqueira":                   (-8.0350, -34.9094),
    "Jiquiá":                     (-8.0875, -34.9167),
    "Jordão":                     (-8.1150, -34.9367),
    "Linha do Tiro":              (-8.0197, -34.9039),
    "Macaxeira":                  (-8.0139, -34.9389),
    "Madalena":                   (-8.0547, -34.9236),
    "Mangabeira":                 (-8.0694, -34.9228),
    "Mangueira":                  (-8.0861, -34.9206),
    "Monteiro":                   (-8.0506, -34.9217),
    "Morro da Conceição":         (-8.0214, -34.9047),
    "Mustardinha":                (-8.0767, -34.9278),
    "Nova Descoberta":            (-8.0283, -34.9214),
    "Paissandu":                  (-8.0664, -34.8922),
    "Parnamirim":                 (-8.0272, -34.9111),
    "Passarinho":                 (-7.9992, -34.9150),
    "Pau Ferro":                  (-8.0044, -34.9100),
    "Peixinhos":                  (-8.0217, -34.9103),
    "Pina":                       (-8.1022, -34.8939),
    "Poço da Panela":             (-8.0183, -34.9139),
    "Ponta de Campina":           (-8.0567, -34.9500),
    "Ponto de Parada":            (-8.0308, -34.9044),
    "Porto da Madeira":           (-8.0869, -34.9253),
    "Prado":                      (-8.0908, -34.9200),
    "Recife Antigo":              (-8.0636, -34.8733),
    "Recife (Bairro do Recife)":  (-8.0636, -34.8733),
    "Rosarinho":                  (-8.0414, -34.9039),
    "San Martin":                 (-8.0725, -34.9208),
    "Sancho":                     (-8.0189, -34.9194),
    "Santana":                    (-8.0778, -34.8861),
    "Santo Amaro":                (-8.0661, -34.8806),
    "Santo Antônio":              (-8.0656, -34.8786),
    "São José":                   (-8.0636, -34.8800),
    "Sítio dos Pintos":           (-8.0061, -34.9278),
    "Soledade":                   (-8.0472, -34.9003),
    "Sudene":                     (-8.0461, -34.9361),
    "Tamarineira":                (-8.0378, -34.9117),
    "Tejipió":                    (-8.0978, -34.9286),
    "Torre":                      (-8.0533, -34.9108),
    "Torreão":                    (-8.0394, -34.8986),
    "Torrões":                    (-8.0942, -34.9258),
    "Totó":                       (-8.1031, -34.9222),
    "Várzea":                     (-8.0517, -34.9542),
    "Vasco da Gama":              (-8.0661, -34.9028),
    "Zumbi":                      (-8.0189, -34.8981),
}

# Índice de vulnerabilidade histórica a enchentes por bairro (0.0 – 1.0)
# Baseado em dados conhecidos de alagamentos recorrentes no Recife
FLOOD_VULNERABILITY: dict[str, float] = {
    "Brasília Teimosa": 0.95,
    "Pina":             0.90,
    "Ibura":            0.88,
    "Jordão":           0.85,
    "Tejipió":          0.85,
    "Afogados":         0.82,
    "Mustardinha":      0.80,
    "Torrões":          0.80,
    "Totó":             0.78,
    "Imbiribeira":      0.75,
    "Jiquiá":           0.75,
    "Mangueira":        0.75,
    "Prado":            0.75,
    "Bongi":            0.72,
    "Caçote":           0.72,
    "Areias":           0.70,
    "Porto da Madeira": 0.70,
    "Cabanga":          0.68,
    "Coelhos":          0.65,
    "Santana":          0.65,
    "Santo Amaro":      0.65,
    "Ilha Joana Bezerra": 0.65,
    "Beberibe":         0.62,
    "Dois Unidos":      0.60,
    "Paissandu":        0.60,
    "Recife (Bairro do Recife)": 0.60,
    "Madalena":         0.58,
    "Ipsep":            0.58,
    "Coelho":           0.55,
    "Mangabeira":       0.55,
    "San Martin":       0.55,
    "Vasco da Gama":    0.55,
    "Boa Viagem":       0.50,
}
DEFAULT_VULNERABILITY = 0.35

# ---------------------------------------------------------------------------
# Cache simples em memória (thread-safe para single-process)
# ---------------------------------------------------------------------------
_cache: dict = {}

def cache_get(key: str, ttl: int):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < ttl:
        return entry["data"]
    return None

def cache_set(key: str, data):
    _cache[key] = {"data": data, "ts": time.time()}

# ---------------------------------------------------------------------------
# Geocoding — usa mapa estático primeiro, API como fallback
# ---------------------------------------------------------------------------
async def geocode_city(city_name: str) -> dict:
    if city_name in BAIRRO_COORDS:
        lat, lon = BAIRRO_COORDS[city_name]
        return {"name": city_name, "latitude": lat, "longitude": lon,
                "country": "Brasil", "admin1": "Pernambuco"}

    async with httpx.AsyncClient(timeout=8.0) as client:
        url = f"https://geocoding-api.open-meteo.com/v1/search?name={city_name} Recife&count=1&language=pt"
        resp = await client.get(url)
        data = resp.json()
        if data.get("results"):
            return data["results"][0]

    print(f"Bairro '{city_name}' não encontrado. Usando centro do Recife.")
    return {"name": city_name, "latitude": -8.0539, "longitude": -34.8811,
            "country": "Brasil", "admin1": "Pernambuco"}

# ---------------------------------------------------------------------------
# Dados meteorológicos (Open-Meteo) — cache 15 min
# ---------------------------------------------------------------------------
async def fetch_weather_data(lat: float, lon: float) -> dict:
    cache_key = f"weather_{lat:.4f}_{lon:.4f}"
    cached = cache_get(cache_key, 900)
    if cached:
        return cached

    base = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,"
        f"wind_speed_10m,wind_direction_10m,wind_gusts_10m,surface_pressure,uv_index,is_day"
        f"&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        f"&past_hours=24&forecast_days=7&timezone=auto"
    )

    async with httpx.AsyncClient(timeout=15.0) as client:
        url_soil = base + "&hourly=precipitation,soil_moisture_0_to_7cm,temperature_2m,weather_code"
        resp = await client.get(url_soil)
        data = resp.json()

        if "error" in data:
            url_plain = base + "&hourly=precipitation,temperature_2m,weather_code"
            resp = await client.get(url_plain)
            data = resp.json()

    cache_set(cache_key, data)
    return data

# ---------------------------------------------------------------------------
# Altitude (Open-Meteo Elevation)
# ---------------------------------------------------------------------------
async def fetch_elevation(lat: float, lon: float) -> float:
    async with httpx.AsyncClient(timeout=10.0) as client:
        url = f"https://api.open-meteo.com/v1/elevation?latitude={lat}&longitude={lon}"
        resp = await client.get(url)
        data = resp.json()
        if data.get("elevation") and len(data["elevation"]) > 0:
            return data["elevation"][0]
    return 10.0

# ---------------------------------------------------------------------------
# Tábua de marés — cache 1 hora
# ---------------------------------------------------------------------------
async def scrape_tide_data() -> dict:
    cached = cache_get("tide", 3600)
    if cached:
        return cached

    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/111.0"}
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
        print("Tide Scrape Error:", e)
        return {"height": 1.5, "trend": "Desconhecido (Offline)"}

# ---------------------------------------------------------------------------
# Algoritmo Hydra Score
# Pesos: chuva prevista 35 | acumulada 20 | maré 15 | solo 10 | vuln histórica 10 | altitude 10
# ---------------------------------------------------------------------------
def calculate_risk_score(weather: dict, elevation: float, tide: dict, bairro: str) -> dict:
    try:
        hourly_precip = weather.get("hourly", {}).get("precipitation", [])
        hourly_soil = weather.get("hourly", {}).get("soil_moisture_0_to_7cm", [])

        past24h = sum(h for h in hourly_precip[:24] if h is not None)
        next24h = sum(h for h in hourly_precip[24:48] if h is not None)

        soil_moisture: float
        if len(hourly_soil) > 24 and hourly_soil[24] is not None:
            soil_moisture = hourly_soil[24]
        else:
            soil_moisture = min(past24h / 60.0, 1.0)

        tide_norm = tide.get("height", 1.5) / 3.0
        vulnerability = FLOOD_VULNERABILITY.get(bairro, DEFAULT_VULNERABILITY)

        score = 0.0
        score += min(next24h * 0.35 * 2, 35)       # Chuva prevista:  máx 35
        score += min(past24h * 0.20 * 2, 20)        # Chuva acumulada: máx 20
        score += (tide_norm * 100) * 0.15            # Maré:            máx 15
        score += (soil_moisture * 100) * 0.10        # Saturação solo:  máx 10
        score += (vulnerability * 100) * 0.10        # Vuln. histórica: máx 10

        if elevation < 5:
            score += 10
        elif elevation < 15:
            score += 5

        score = min(math.ceil(score), 100)

        nivel = "BAIXO"
        if score >= 80:   nivel = "SEVERO"
        elif score >= 60: nivel = "ALTO"
        elif score >= 30: nivel = "MODERADO"

        current = weather.get("current", {})
        return {
            "score": score,
            "nivel": nivel,
            "rawValues": {
                "chuvaPrevista":  round(next24h, 1),
                "chuva24h":       round(past24h, 1),
                "altitude":       round(elevation),
                "saturacaoSolo":  round(soil_moisture, 2),
                "mareAltura":     tide.get("height"),
                "mareTrend":      tide.get("trend"),
                "uvIndex":        current.get("uv_index", 0),
                "pressao":        current.get("surface_pressure", 1013),
                "rajadaVento":    current.get("wind_gusts_10m", 0),
            }
        }
    except Exception as e:
        print("Risk Calc Error:", e)
        return {"score": 0, "nivel": "ERRO", "rawValues": {}}

# ---------------------------------------------------------------------------
# Previsão para as próximas 6 horas
# ---------------------------------------------------------------------------
def build_forecast_6h(weather: dict) -> list:
    hourly = weather.get("hourly", {})
    times  = hourly.get("time", [])
    precip = hourly.get("precipitation", [])
    temps  = hourly.get("temperature_2m", [])
    codes  = hourly.get("weather_code", [])

    start = 24  # índice 0-23 = passado, índice 24+ = futuro
    result = []
    for i in range(start, min(start + 6, len(times))):
        result.append({
            "time":          times[i] if i < len(times) else "",
            "precipitation": precip[i] if i < len(precip) else 0,
            "temperature":   temps[i]  if i < len(temps)  else None,
            "weather_code":  codes[i]  if i < len(codes)  else 0,
        })
    return result

# ---------------------------------------------------------------------------
# Previsão diária (próximos 6 dias, pulando hoje)
# ---------------------------------------------------------------------------
def build_daily_forecast(weather: dict) -> list:
    daily = weather.get("daily", {})
    dates = daily.get("time", [])
    highs = daily.get("temperature_2m_max", [])
    lows  = daily.get("temperature_2m_min", [])
    rains = daily.get("precipitation_probability_max", [])
    result = []
    for i in range(1, min(7, len(dates))):
        result.append({
            "date": dates[i] if i < len(dates) else "",
            "high": round(highs[i]) if i < len(highs) and highs[i] is not None else None,
            "low":  round(lows[i])  if i < len(lows)  and lows[i]  is not None else None,
            "rain": int(rains[i])   if i < len(rains)  and rains[i] is not None else 0,
        })
    return result

# ---------------------------------------------------------------------------
# Resumo rápido por bairro (temperatura + Hydra Score) — para painel inferior
# ---------------------------------------------------------------------------
async def _bairro_summary(bairro: str) -> dict:
    try:
        geo = await geocode_city(bairro)
        lat, lon = geo["latitude"], geo["longitude"]
        weather_data, tide = await asyncio.gather(
            fetch_weather_data(lat, lon),
            scrape_tide_data(),
        )
        risk = calculate_risk_score(weather_data, 10.0, tide, bairro)
        current = weather_data.get("current", {})
        return {
            "name":  bairro,
            "temp":  round(current.get("temperature_2m", 0)),
            "score": risk["score"],
        }
    except Exception as e:
        print(f"Summary error for {bairro}: {e}")
        return {"name": bairro, "temp": 0, "score": 0}


class BatchScoreRequest(BaseModel):
    bairros: list[str]


@app.post("/api/scores")
async def get_scores(req: BatchScoreRequest):
    results = await asyncio.gather(*[_bairro_summary(b) for b in req.bairros[:6]])
    return {"scores": list(results)}

# ---------------------------------------------------------------------------
# Narrativa IA (Gemini) — movida para cá, chave nunca vai ao cliente
# ---------------------------------------------------------------------------
class NarrativeRequest(BaseModel):
    cityName: str
    riskData: dict

@app.post("/api/narrative")
async def get_narrative(request: NarrativeRequest):
    if not _gemini_client:
        return {"narrative": "Módulo IA Offline. Configure GEMINI_API_KEY no arquivo .env do servidor."}

    risk = request.riskData
    raw  = risk.get("rawValues", {})
    solo_pct = round((raw.get("saturacaoSolo") or 0) * 100)

    prompt = f"""Você é um assistente da Defesa Civil do Recife. Fale com o morador do bairro {request.cityName} de forma simples e direta, como se fosse uma mensagem de WhatsApp.

Situação: risco {risk.get("nivel")} (score {risk.get("score")}/100). Chuva prevista {raw.get("chuvaPrevista")}mm, acumulada {raw.get("chuva24h")}mm em 24h, maré {raw.get("mareAltura")}m, solo {solo_pct}% saturado.

Escreva exatamente 3 frases curtas:
1. Diga claramente se tem ou não risco de alagamento/deslizamento HOJE, e por quê (em palavras simples).
2. Mencione algum ponto ou rua do bairro que costuma alagar ou ter problema, se souber.
3. Dê um conselho prático para o morador agir agora.

Use linguagem do dia a dia, sem termos técnicos. Máximo 55 palavras no total. Sem emojis, sem títulos."""

    try:
        response = await asyncio.to_thread(
            lambda: _gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
        )
        return {"narrative": response.text}
    except Exception as e:
        import traceback; traceback.print_exc()
        nivel = risk.get("nivel", "?")
        score = risk.get("score", 0)
        return {"narrative": f"IA temporariamente indisponível. Risco {nivel} detectado (score {score}/100)."}

# ---------------------------------------------------------------------------
# Endpoint principal
# ---------------------------------------------------------------------------
@app.get("/api/dashboard/{bairro}")
async def get_dashboard_data(bairro: str):
    try:
        geo = await geocode_city(bairro)
        lat, lon = geo["latitude"], geo["longitude"]

        # Busca paralela — ~2x mais rápido que sequencial
        weather, elevation, tide = await asyncio.gather(
            fetch_weather_data(lat, lon),
            fetch_elevation(lat, lon),
            scrape_tide_data(),
        )

        risk = calculate_risk_score(weather, elevation, tide, bairro)
        forecast_6h = build_forecast_6h(weather)

        return {
            "location":      geo,
            "weather":       weather,
            "risk":          risk,
            "forecast6h":    forecast_6h,
            "forecastDaily": build_daily_forecast(weather),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Frontend — serve o dashboard HTML estático
# ---------------------------------------------------------------------------
@app.get("/")
async def serve_dashboard():
    path = os.path.join(BASE_DIR, "static", "index.html")
    if not os.path.exists(path):
        return {"message": "HydraRec API online — arquivo index.html não encontrado em static/"}
    return FileResponse(path)


# PWA assets
@app.get("/manifest.json")
async def serve_manifest():
    return FileResponse(os.path.join(BASE_DIR, "static", "manifest.json"),
                        media_type="application/manifest+json")

@app.get("/sw.js")
async def serve_sw():
    return FileResponse(os.path.join(BASE_DIR, "static", "sw.js"),
                        media_type="application/javascript")

@app.get("/icon.svg")
async def serve_icon():
    return FileResponse(os.path.join(BASE_DIR, "static", "icon.svg"),
                        media_type="image/svg+xml")
