"""
ai_narrative — gera boletim curto de risco climático para o painel.
3 linhas, sem prefixos, sem menção a fontes mortas. Português direto.
Fonte de clima: APAC (CEMADEN + meteorologia24h + climatologia).
"""
import os
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ── Pontos de risco mapeados (mantidos do V2 para context-awareness) ────────

_VULN = {
    "Boa Viagem": 62, "Jordão": 81, "Brasília Teimosa": 74, "Imbiribeira": 69,
    "Afogados": 71, "Ibura": 88, "Tejipió": 85, "Mustardinha": 76,
    "Casa Amarela": 58, "Cordeiro": 64, "Madalena": 55, "Pina": 67,
    "Boa Vista": 45, "Torre": 48, "Derby": 42, "Espinheiro": 39,
    "Graças": 41, "Recife Antigo": 53,
}

_PONTOS = {
    "Boa Viagem":       "Av. Eng. Domingos Ferreira, Canal dos Setúbal",
    "Pina":             "BR-101 sul, Canal do Pina",
    "Imbiribeira":      "Av. Sul, BR-101 cruzamento Imbiribeira",
    "Ibura":            "Comunidade Ibura, Av. Ibura de Baixo",
    "Jordão":           "Encosta Norte do Jordão",
    "Tejipió":          "Av. Tejipió, planície do Rio Tejipió",
    "Mustardinha":      "Av. Senator Nilo Coelho",
    "Afogados":         "Av. Caxangá km 3, Canal do Capibaribe",
    "Santo Amaro":      "Canal da Tacaruna, Av. Dantas Barreto (trecho baixo)",
    "Paissandu":        "Canal da Tacaruna, Av. Agamenon (baixio)",
    "Boa Vista":        "Praça do Derby, Av. Agamenon viaduto",
    "Derby":            "Praça do Derby, Rua Gervásio Pires",
    "Madalena":         "Av. Caxangá km 2, Canal do Capibaribe",
    "Torre":            "Mangueirão, Av. Caxangá (Torre)",
    "Cordeiro":         "Av. Caxangá, Canal do Cordeiro",
    "São José":         "Av. Dantas Barreto, Comunidade Coque",
    "Recife Antigo":    "Cais José Estelita, Marco Zero",
    "Casa Amarela":     "Morro da Conceição, Av. Norte",
    "Água Fria":        "Alto do Mandu, encostas Alto José Bonifácio",
    "Arruda":           "Av. Norte (Arruda/Bomba do Hemetério)",
    "Parnamirim":       "Rua Real da Torre, Av. Norte",
    "Brasília Teimosa": "Av. Herculano Bandeira, Beira Rio",
    "Graças":           "Av. Boa Viagem norte, Canal dos Setúbal",
    "Espinheiro":       "Av. Rui Barbosa, Av. Agamenon",
}


# ── Helpers de classificação ──────────────────────────────────────────────

def _rain_phrase(mm: float) -> str:
    if mm < 0.2:  return "sem chuva"
    if mm < 2.5:  return "chuva leve"
    if mm < 10:   return "chuva moderada"
    if mm < 30:   return "chuva forte"
    return "chuva severa"


def _action_for_level(nivel: str, ponto: Optional[str], bairro: str) -> str:
    nivel = (nivel or "SEGURO").upper()
    if nivel in ("SEVERO", "ALTO"):
        if ponto:
            return f"Evite {ponto.split(',')[0]} agora — risco ativo."
        return f"Evite trechos baixos de {bairro} agora."
    if nivel == "MODERADO":
        if ponto:
            return f"Cuidado em {ponto.split(',')[0]} durante o pico da chuva."
        return f"Atenção redobrada em vias baixas de {bairro}."
    if nivel == "ATENCAO":
        return "Rotina normal, mas observe a evolução nas próximas horas."
    return "Rotina normal — sem ação operacional necessária."


# ── Prompt V3 — APAC native, 3 linhas ──────────────────────────────────────

PROMPT_V3 = """Você é analista operacional da Defesa Civil do Recife. Escreva EXATAMENTE 3 linhas curtas e diretas — sem prefixos, sem introdução, sem meta-comentário.

CONTEXTO:
- Bairro: {bairro}
- Histórico de alagamentos no bairro: {vuln_pct}% dos anos recentes
- Pontos de risco mapeados: {pontos}

LEITURA APAC AGORA:
- Estação CEMADEN mais próxima: {station_name} ({station_distance_m} m)
- Chuva atual: {rain_1h_mm} mm/h ({rain_phrase})
- Acumulado 24h: {rain_24h_mm} mm
- Vento: {wind_kmh} km/h · Umidade: {humidity_pct}%
- Boletim APAC RMR: {alert_titulo} (nível {alert_nivel})

HYDRA SCORE: {score}/100 · nível {nivel}
REPORTS RECENTES (2h): {reports_lista}

REGRAS:
- Linha 1: o que está acontecendo AGORA em {bairro} (cite chuva real e score).
- Linha 2: ponto/rua específico OU recomendação de ação proporcional ao nível.
- Linha 3: o que esperar nas próximas horas, baseado nos dados.

PROIBIDO:
- Prefixos como "SITUAÇÃO AGORA:", "AÇÃO:", numerar linhas.
- Citar Open-Meteo, OpenWeather, INMET (não usamos mais).
- "se chover", "caso chova", "monitore", "fique atento".
- Linha com mais de 18 palavras.
- "Hydra Score" literalmente — chame de "score" ou "índice".

Português direto do Recife. Máximo 55 palavras no total."""


def _format_reports(reports: list) -> str:
    if not reports: return "nenhum"
    out = []
    for r in reports[:3]:
        tipo = r.get("tipo") or r.get("type", "ocorrência")
        cnt = r.get("confirmacoes") or r.get("confirmed_count", 0)
        out.append(f"{tipo.replace('_', ' ')} ({cnt})")
    return ", ".join(out)


def _build_prompt(bairro: str, risk: dict, weather: Optional[dict], reports: list, apac_alert: Optional[dict]) -> str:
    w = weather or {}
    score = risk.get("score", 0)
    nivel = risk.get("nivel", "SEGURO")
    rain_1h = w.get("rain_1h_mm")
    rain_24h = w.get("rain_24h_mm")

    return PROMPT_V3.format(
        bairro=bairro,
        vuln_pct=_VULN.get(bairro, 55),
        pontos=_PONTOS.get(bairro, "—"),
        station_name=w.get("station_name") or "indisponível",
        station_distance_m=w.get("station_distance_m") or 0,
        rain_1h_mm=f"{rain_1h:.1f}" if isinstance(rain_1h, (int, float)) else "—",
        rain_phrase=_rain_phrase(rain_1h or 0),
        rain_24h_mm=f"{rain_24h:.1f}" if isinstance(rain_24h, (int, float)) else "—",
        wind_kmh=int(w.get("wind_kmh") or 0),
        humidity_pct=int(w.get("humidity_pct") or 0),
        alert_titulo=(apac_alert or {}).get("titulo", "—"),
        alert_nivel=(apac_alert or {}).get("nivel", "SEGURO"),
        score=score,
        nivel=nivel,
        reports_lista=_format_reports(reports or []),
    )


# ── Fallback local (3 linhas) ─────────────────────────────────────────────

def _build_fallback(bairro: str, risk: dict, weather: Optional[dict]) -> str:
    nivel = risk.get("nivel", "SEGURO")
    score = risk.get("score", 0)
    w = weather or {}
    rain = w.get("rain_1h_mm") or 0
    rain_24h = w.get("rain_24h_mm") or 0
    station = w.get("station_name") or "estação APAC"
    ponto = _PONTOS.get(bairro)

    if rain >= 0.2:
        line1 = f"{bairro}: {_rain_phrase(rain)} agora ({rain:.1f} mm/h em {station}), índice {score}/100."
    elif rain_24h >= 5:
        line1 = f"{bairro} sem chuva no momento, mas {rain_24h:.0f} mm acumulados em 24h — índice {score}/100."
    else:
        line1 = f"{bairro} estável agora — sem chuva em {station}, índice {score}/100."

    line2 = _action_for_level(nivel, ponto, bairro)

    if nivel in ("SEVERO", "ALTO"):
        line3 = "Risco ativo nas próximas horas — confira reports antes de sair."
    elif rain >= 2.5 or rain_24h >= 10:
        line3 = "Cenário pode evoluir nas próximas horas; estação atualiza a cada 5 min."
    else:
        line3 = "Cenário estável pelas próximas horas — sem chuva forte na RMR."

    return "\n".join([line1, line2, line3])


# ── API pública ───────────────────────────────────────────────────────────

async def generate_narrative(
    bairro: str,
    risk: dict,
    consensus: dict | None = None,         # legacy
    nearby_reports: list | None = None,
    apac_boletim: str | dict | None = None,
    weather: dict | None = None,           # novo shape APAC enriquecido
) -> tuple[str, str]:
    """Retorna (texto, modelo_usado). 3 linhas curtas."""
    # Aceita ambos: weather novo (preferencial) ou consensus legado
    w = weather or consensus or {}
    alert = apac_boletim if isinstance(apac_boletim, dict) else None
    prompt = _build_prompt(bairro, risk, w, nearby_reports or [], alert)

    # NVIDIA NIM (rápido)
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
                        temperature=0.2,
                        max_tokens=140,
                    )
                    text = (resp.choices[0].message.content or "").strip()
                    if text:
                        short = "Nemotron 49B" if "nemotron" in model else "Llama 70B"
                        return _enforce_3_lines(text), short
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"NVIDIA narrative error: {e}")

    # Gemini Flash (fallback)
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    if gemini_key:
        try:
            from google import genai as google_genai
            client_g = google_genai.Client(api_key=gemini_key)
            response = await asyncio.to_thread(
                lambda: client_g.models.generate_content(
                    model="gemini-1.5-flash",
                    contents=prompt,
                    config={"temperature": 0.2, "max_output_tokens": 140},
                )
            )
            text = (response.text or "").strip()
            if text:
                return _enforce_3_lines(text), "Gemini Flash"
        except Exception as e:
            logger.warning(f"Gemini narrative error: {e}")

    return _build_fallback(bairro, risk, w), "local"


def _enforce_3_lines(text: str) -> str:
    """Garante no máximo 3 linhas, removendo prefixos numerados/labels."""
    import re
    lines = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        # Remove prefixos como "1.", "FRASE 2 —", "SITUAÇÃO AGORA:", "AÇÃO:"
        line = re.sub(r"^\d+[\.\)]\s*", "", line)
        line = re.sub(r"^(FRASE|LINHA)\s*\d+\s*[—\-:]?\s*", "", line, flags=re.I)
        line = re.sub(r"^(SITUAÇÃO|AÇÃO|PONTO|JANELA)[^:]*:\s*", "", line, flags=re.I)
        lines.append(line)
        if len(lines) == 3:
            break
    return "\n".join(lines) if lines else text
