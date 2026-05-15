import os
import asyncio
import logging
from fastapi import APIRouter, HTTPException
from models.schemas import RouteRiskRequest
from services.routing import get_route, analyze_route_risk
from services.apac_scraper import fetch_apac_boletim
from services.inmet_alerts import fetch_inmet_alerts
from services.apac_stations import fetch_apac_stations
from services.cemaden_alerts import fetch_cemaden_alerts

logger = logging.getLogger(__name__)
router = APIRouter()

_MODO_LABEL = {
    "driving-car":     "de carro",
    "cycling-regular": "de bicicleta",
    "foot-walking":    "a pé",
}

_APAC_DESC = {
    "SEVERO":   "chuva severa ativa — alto risco de alagamento",
    "ALTO":     "chuva forte — risco moderado a alto de alagamento",
    "MODERADO": "chuva moderada — pontos de atenção em trechos baixos",
    "ATENCAO":  "atenção preventiva — chuva possível, sem risco crítico confirmado",
    "SEGURO":   "sem alertas de chuva — tempo seguro",
}

_MODO_CONTEXTO = {
    "driving-car":     "Veículo: risco principal = alagamento de via (score > 40 recomenda desvio imediato).",
    "cycling-regular": "Bicicleta: riscos = piso molhado/escorregadio, visibilidade baixa, ventos (score > 30 = cautela extrema).",
    "foot-walking":    "A pé: riscos = calçadas inundadas, drenagem ativa, piso instável (score > 25 = aguardar).",
}

_ROUTE_PROMPT = """Você é analista da Defesa Civil do Recife. Escreva 3 frases diretas sobre o trajeto abaixo, uma por linha, sem rótulos, sem numeração, sem introdução.

TRAJETO: {origem} → {destino} | {modo_label} | {distancia_km}km, ~{duracao_min}min
RISCO: score {risk_score}/100 ({risk_level}) | chuva prevista {rain_next}mm/24h
ALERTA APAC: {apac_nivel} — {apac_desc}
ALERTAS INMET ATIVOS PARA PE: {inmet_resumo}
CONTEXTO DO MODAL: {modo_contexto}
PONTOS HISTÓRICOS NO TRAJETO ({n_hazards}):
{hazards_lista}

Frase 1: situação atual do trajeto {modo_label} — use score e chuva como fatos concretos. Mencione o nível APAC em linguagem simples, não use a palavra "APAC" sozinha.
Frase 2: o ponto mais crítico com nome real e motivo histórico. Se nenhum ponto, diga que o trajeto está livre de riscos mapeados.
Frase 3: recomendação objetiva específica para quem vai {modo_label} — partir agora, aguardar ou cuidado concreto (ex: evitar calçada da Av. X, manter distância de bueiros).

Máximo 25 palavras por frase. Português direto. Zero rótulos, zero numeração, zero "se chover", zero "APAC" isolado sem contexto."""


def _format_hazards(hazards: list) -> str:
    if not hazards:
        return "Nenhum ponto de risco histórico identificado."
    lines = []
    for h in hazards[:5]:
        name = h.get("name") or "ponto de risco"
        desc = h.get("description", "")
        lines.append(f"- {name}: {desc} (fonte: Defesa Civil PE / APAC 2018-2024)")
    return "\n".join(lines)


def _format_inmet(alerts: list) -> str:
    if not alerts:
        return "Nenhum alerta ativo."
    return "; ".join(
        f"{a.get('evento','Alerta')} ({a.get('severidade','?')})"
        for a in alerts[:3]
    )


async def _generate_route_narrative(
    origem: str, destino: str, modo: str,
    analysis: dict, rain_next: float,
) -> tuple[str, str]:
    """Gera narrativa IA para o resultado do trajeto."""
    label      = _MODO_LABEL.get(modo, "de carro")
    apac_nivel = analysis.get("apac_nivel", "ATENCAO")
    inmet_list = analysis.get("active_alerts", [])
    prompt = _ROUTE_PROMPT.format(
        origem=origem,
        destino=destino,
        modo_label=label,
        distancia_km=analysis.get("distance_km", "—"),
        duracao_min=analysis.get("duration_min", "—"),
        risk_score=analysis.get("risk_score", 0),
        risk_level=analysis.get("risk_level", "BAIXO"),
        rain_next=round(rain_next, 1),
        apac_nivel=apac_nivel,
        apac_desc=_APAC_DESC.get(apac_nivel, "condição de atenção"),
        modo_contexto=_MODO_CONTEXTO.get(modo, _MODO_CONTEXTO["driving-car"]),
        inmet_resumo=_format_inmet(inmet_list),
        n_hazards=len(analysis.get("hazards", [])),
        hazards_lista=_format_hazards(analysis.get("hazards", [])),
    )

    nvidia_key = os.getenv("NVIDIA_API_KEY", "")
    if nvidia_key:
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=nvidia_key)
            for model in ("nvidia/llama-3.3-nemotron-super-49b-v1", "meta/llama-3.3-70b-instruct"):
                try:
                    resp = await client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.3, max_tokens=200,
                    )
                    text = (resp.choices[0].message.content or "").strip()
                    if text:
                        return text, "Nemotron 49B" if "nemotron" in model else "Llama 70B"
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"NVIDIA route narrative: {e}")

    gemini_key = os.getenv("GEMINI_API_KEY", "")
    if gemini_key:
        try:
            from google import genai as google_genai
            client_g = google_genai.Client(api_key=gemini_key)
            response = await asyncio.to_thread(
                lambda: client_g.models.generate_content(
                    model="gemini-1.5-flash",
                    contents=prompt,
                    config={"temperature": 0.3, "max_output_tokens": 200},
                )
            )
            text = (response.text or "").strip()
            if text:
                return text, "Gemini Flash"
        except Exception as e:
            logger.debug(f"Gemini route narrative: {e}")

    # Fallback local — modo-específico
    level   = analysis.get("risk_level", "BAIXO")
    score   = analysis.get("risk_score", 0)
    hazards = analysis.get("hazards", [])
    dist    = analysis.get("distance_km", "—")
    dur     = analysis.get("duration_min", "—")
    ponto   = hazards[0].get("name", "") if hazards else ""
    apac_desc_short = _APAC_DESC.get(apac_nivel, "atenção preventiva")

    _mode_detail = {
        "driving-car":     ("Vias podem estar alagadas", "Desvie por ruas altas e evite túneis e viadutos"),
        "cycling-regular": ("Piso molhado e visibilidade reduzida", "Reduza velocidade, evite bueiros e sarjetas abertas"),
        "foot-walking":    ("Calçadas com drenagem ativa", "Use passarelas elevadas e evite calçadas próximas a canais"),
    }
    risco_desc, precaution = _mode_detail.get(modo, _mode_detail["driving-car"])

    if level == "ALTO":
        line1 = f"Trajeto {label} com risco elevado — score {score}/100, {rain_next:.0f}mm previstos ({apac_desc_short})."
        line2 = f"{'Evite ' + ponto if ponto else risco_desc + ' no trajeto'}."
        line3 = f"{precaution} ou aguarde a chuva passar."
    elif level == "MEDIO":
        line1 = f"Trajeto {label} requer atenção — score {score}/100, {dist}km em ~{dur}min ({apac_desc_short})."
        line2 = f"{'Atenção redobrada em ' + ponto if ponto else risco_desc}."
        line3 = f"{precaution} durante o percurso."
    else:
        line1 = f"Trajeto {label} liberado — score {score}/100, {dist}km em ~{dur}min. {apac_desc_short.capitalize()}."
        line2 = "Nenhum ponto de risco ativo identificado no caminho."
        line3 = f"Pode seguir normalmente; {precaution.lower()} como precaução."

    return "\n".join([line1, line2, line3]), "local"


@router.post("/api/route-risk")
async def route_risk(req: RouteRiskRequest):
    try:
        # Busca rota OSRM + 5 fontes externas em paralelo
        route_data, apac_raw, inmet_raw, stations_raw, cemaden_raw = await asyncio.gather(
            get_route(
                origin=(req.origem_lat, req.origem_lon),
                destination=(req.destino_lat, req.destino_lon),
                profile=req.perfil,
            ),
            fetch_apac_boletim(),
            fetch_inmet_alerts(),
            fetch_apac_stations(),
            fetch_cemaden_alerts(),
            return_exceptions=True,
        )

        if isinstance(route_data, Exception):
            raise route_data

        apac_nivel    = (
            apac_raw.get("nivel")
            if isinstance(apac_raw, dict) and not apac_raw.get("_empty")
            else "ATENCAO"
        )
        inmet_alerts  = inmet_raw    if isinstance(inmet_raw, list)    else []
        apac_sttns    = stations_raw if isinstance(stations_raw, list) else []
        cemaden_alerts = cemaden_raw if isinstance(cemaden_raw, list)  else []

        # Merge INMET + Cemaden alerts
        all_alerts = inmet_alerts + cemaden_alerts

        analysis = await analyze_route_risk(
            route_data,
            weather_rain_next=req.rain_next,
            apac_nivel=apac_nivel,
            inmet_alerts=all_alerts,
            apac_stations=apac_sttns,
        )

        origem_label  = req.origem_nome or f"({req.origem_lat:.3f}, {req.origem_lon:.3f})"
        destino_label = req.destino_nome or f"({req.destino_lat:.3f}, {req.destino_lon:.3f})"
        try:
            narrative, model_used = await _generate_route_narrative(
                origem=origem_label,
                destino=destino_label,
                modo=req.perfil,
                analysis=analysis,
                rain_next=req.rain_next,
            )
        except Exception:
            narrative, model_used = None, None

        return {**analysis, "narrative": narrative, "model_used": model_used}

    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.warning(f"route-risk error: {e}")
        raise HTTPException(status_code=502, detail=str(e))
