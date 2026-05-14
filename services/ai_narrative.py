import os
import asyncio
import logging

logger = logging.getLogger(__name__)

# Static bairro context — flood vulnerability % (2018-2024 APAC data)
_VULN = {
    "Boa Viagem":       62,
    "Jordão":           81,
    "Brasília Teimosa": 74,
    "Imbiribeira":      69,
    "Afogados":         71,
    "Ibura":            88,
    "Tejipió":          85,
    "Mustardinha":      76,
    "Casa Amarela":     58,
    "Cordeiro":         64,
    "Madalena":         55,
    "Pina":             67,
    "Boa Vista":        45,
    "Torre":            48,
    "Derby":            42,
    "Espinheiro":       39,
    "Graças":           41,
    "Recife Antigo":    53,
}

_PONTOS = {
    "Boa Viagem":       "Av. Boa Viagem (trecho Pina), Av. Eng. Domingos Ferreira, Canal das Três Bocas",
    "Jordão":           "Encosta Norte do Jordão, Rua João de Barros, estrada do Ibura",
    "Brasília Teimosa": "Av. Herculano Bandeira, comunidade Coque, Beira Rio",
    "Imbiribeira":      "Av. Sul, BR-101 cruzamento Imbiribeira, Conjunto Imbiribeira",
    "Afogados":         "Av. Caxangá km 3, Canal do Capibaribe Afogados, Rua Visconde de Jequitinhonha",
    "Ibura":            "Comunidade Ibura (encosta), Av. Ibura de Baixo, Rua da Encosta",
    "Tejipió":          "Av. Tejipió, planície do Rio Tejipió, Rua Bom Jesus do Tejipió",
    "Mustardinha":      "Rua Padre Carapuceiro, Av. Senator Nilo Coelho, região da Mustardinha Baixa",
    "Pina":             "BR-101 sul, Canal do Pina, Av. República do Líbano",
    "default":          "verificar pontos críticos locais com Defesa Civil",
}

PROMPT_V2 = """Você é analista da Defesa Civil do Recife. Resposta direta, técnica mas acessível, sem floreio.

CONTEXTO ESTÁTICO DO BAIRRO:
• Bairro: {bairro}
• Vulnerabilidade histórica: {vuln_pct}% (frequência de alagamentos 2018-2024)
• Pontos críticos conhecidos do bairro: {pontos_criticos_lista}

DADOS METEOROLÓGICOS (consenso de {sources_count} fontes — confiança {confidence}):
• Chuva próximas 24h: {rain_next}mm (faixa entre fontes: {rain_min}–{rain_max}mm)
• Chuva últimas 24h: {rain_past}mm
• Maré: {tide_h}m, tendência {tide_trend}
• Umidade: {humidity}%, pressão: {pressure}hPa
• Temperatura: {temp}°C, sensação térmica: {feels_like}°C (heat index NOAA)
• Vento: {wind_speed_kmh} km/h

HYDRA SCORE ATUAL:
• Score: {score}/100 ({nivel})

REPORTS DA COMUNIDADE (últimas 2h):
{reports_lista}

ALERTAS APAC OFICIAIS HOJE:
{apac_boletim}

==============
ESCREVA EXATAMENTE 4 FRASES, SEPARADAS POR QUEBRA DE LINHA:

1. DIAGNÓSTICO HOJE — Tem ou não risco real AGORA? De que tipo (alagamento, deslizamento, calor extremo)? Por quê em termos concretos? Cite um número específico (mm, score, heat index).

2. RUA ESPECÍFICA A EVITAR — Use os pontos críticos conhecidos + reports recentes. Cite nome de rua/avenida/esquina. Se não houver, diga "Sem ponto específico apontado agora, mas atenção redobrada em <bairro vizinho mais vulnerável>".

3. JANELA DE TEMPO — Quando o risco vai PIORAR ou PASSAR? Use horários reais ("Risco aumenta a partir das 16h e dura até 19h" ou "Cenário se mantém pelas próximas 6h").

4. AÇÃO CONCRETA — O que fazer nos PRÓXIMOS 30 MIN? Verbo no imperativo: "Saia agora", "Evite a Av. X", "Recolha veículo da garagem subsolo", "Suba para o 2º andar".

REGRAS:
• PROIBIDO usar: "procure mapas digitais", "fique atento", "consulte autoridades", "tome cuidado", "evite áreas de risco", "monitore as condições".
• OBRIGATÓRIO: nomes de ruas/horários/ações executáveis.
• Máximo 90 palavras no TOTAL das 4 frases.
• Sem emojis, sem markdown, sem títulos. Texto puro.
• Português do Brasil, vocabulário do dia a dia."""


def _build_fallback(bairro: str, risk: dict, consensus: dict) -> str:
    nivel = risk.get("nivel", "MODERADO")
    score = risk.get("score", 0)
    rain = consensus.get("rain_next_24h_mm", 0)
    pontos = _PONTOS.get(bairro, _PONTOS["default"])
    if score >= 65:
        acao = f"Risco {nivel} em {bairro} (score {score}/100). {rain:.0f}mm previstos. Evite: {pontos.split(',')[0]}. Fique em local alto e seguro."
    elif score >= 45:
        acao = f"Risco {nivel} em {bairro} (score {score}/100). {rain:.0f}mm previstos. Atenção a {pontos.split(',')[0]} se chover. Reduza deslocamentos não essenciais."
    else:
        acao = f"Risco {nivel} em {bairro} (score {score}/100). Condições estáveis agora, mas acompanhe a previsão de {rain:.0f}mm. Siga a rotina normalmente."
    return acao


def _format_reports(reports: list) -> str:
    if not reports:
        return "Nenhum report nas últimas 2h."
    lines = []
    for r in reports[:5]:
        tipo = r.get("tipo") or r.get("type", "ocorrência")
        sev = r.get("severidade") or r.get("severity", "")
        cnt = r.get("confirmacoes") or r.get("confirmed_count", 0)
        lines.append(f"- {tipo.replace('_',' ').capitalize()} {sev}, {cnt} confirmações")
    return "\n".join(lines)


async def generate_narrative(
    bairro: str,
    risk: dict,
    consensus: dict,
    nearby_reports: list | None = None,
    apac_boletim: str | None = None,
) -> str:
    raw = risk.get("rawValues", {}) or risk.get("raw_values", {})

    prompt = PROMPT_V2.format(
        bairro=bairro,
        vuln_pct=_VULN.get(bairro, 55),
        pontos_criticos_lista=_PONTOS.get(bairro, _PONTOS["default"]),
        sources_count=consensus.get("sources_count", 1),
        confidence=consensus.get("confidence", "MEDIA"),
        rain_next=round(consensus.get("rain_next_24h_mm", 0), 1),
        rain_min=round(consensus.get("rain_min", consensus.get("rain_next_24h_mm", 0)) * 0.8, 1),
        rain_max=round(consensus.get("rain_max", consensus.get("rain_next_24h_mm", 0)) * 1.2, 1),
        rain_past=round(consensus.get("rain_past_24h_mm", 0), 1),
        tide_h=raw.get("mareAltura") or raw.get("mare_altura", "—"),
        tide_trend=raw.get("mareTrend") or raw.get("mare_trend", "—"),
        humidity=round(consensus.get("humidity", 70)),
        pressure=round(consensus.get("pressure", 1013)),
        temp=round(consensus.get("temperature", 28)),
        feels_like=round(consensus.get("feels_like", consensus.get("temperature", 28))),
        wind_speed_kmh=round(consensus.get("wind_speed", 0)),
        score=risk.get("score", 0),
        nivel=risk.get("nivel", "MODERADO"),
        reports_lista=_format_reports(nearby_reports or []),
        apac_boletim=apac_boletim or "Sem boletim APAC disponível.",
    )

    key = os.getenv("GEMINI_API_KEY", "")
    if not key:
        return _build_fallback(bairro, risk, consensus)

    try:
        from google import genai as google_genai
        client = google_genai.Client(api_key=key)
        response = await asyncio.to_thread(
            lambda: client.models.generate_content(
                model="gemini-1.5-flash",
                contents=prompt,
                config={"temperature": 0.4, "max_output_tokens": 200},
            )
        )
        text = (response.text or "").strip()
        return text if text else _build_fallback(bairro, risk, consensus)
    except Exception as e:
        logger.warning(f"Gemini error: {e}")
        return _build_fallback(bairro, risk, consensus)
