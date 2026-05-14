import os
import time
import logging
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# Cache simples: {bairro: (timestamp, texto)}
_cache: dict[str, tuple[float, str]] = {}
CACHE_TTL = 300  # 5 min

PROMPT = """Você é o assistente do HydraRec, app de monitoramento de risco climático do Recife.

Um morador quer entender por que {bairro} recebeu {score} pontos ({nivel}) agora.

COMPONENTES DO SCORE (apenas os que têm valor > 0):
{componentes}

CONTEXTO:
- Umidade atual: {umidade}%
- Pressão: {pressao} hPa
- Mês: maio (período chuvoso no Recife — média histórica: 340mm/mês)

Escreva uma explicação clara e amigável seguindo EXATAMENTE este formato:

**Por que {score} pontos em {bairro}?**

Para cada componente acima, explique em 1-2 frases o que aquele valor significa na prática. Use linguagem simples, sem jargão. Contextualize com dados reais do Recife (ex: "A maré de 2.9m está acima do normal para Recife, que fica entre 1.8m e 2.5m na maioria dos dias").

Encerre com 1 frase resumindo por que a COMBINAÇÃO desses fatores importa para {bairro}.

Use emojis nos itens. Máximo 220 palavras. Português do Brasil coloquial mas confiável."""


def _build_componentes_str(components: dict, raw: dict) -> str:
    lines = []
    rn = raw.get("chuva_prevista_24h", 0)
    rp = raw.get("chuva_acumulada_24h", 0)
    mare = raw.get("mare_altura", "—")
    trend = raw.get("mare_trend", "")
    vuln = raw.get("vulnerabilidade_bairro", 0)
    alt = raw.get("altitude_m", "—")
    rep = raw.get("reports_comunidade_2km", 0)

    if components.get("rain_next", 0) > 0:
        lines.append(f"🌧️ Chuva prevista 24h: {rn}mm → +{round(components['rain_next'],1)} pts")
    if components.get("rain_past", 0) > 0:
        lines.append(f"⛈️ Chuva acumulada 24h: {rp}mm → +{round(components['rain_past'],1)} pts")
    if components.get("tide", 0) > 0:
        lines.append(f"🌊 Maré: {mare}m ({trend}) → +{round(components['tide'],1)} pts")
    if components.get("vulnerability", 0) > 0:
        lines.append(f"🏘️ Vulnerabilidade do bairro: {vuln:.2f}/1.0 → +{round(components['vulnerability'],1)} pts")
    if components.get("altitude", 0) > 0:
        lines.append(f"📏 Altitude: {alt}m → +{round(components['altitude'],1)} pts")
    if components.get("community", 0) > 0:
        lines.append(f"👥 Reports da comunidade: {rep} relatos → +{round(components['community'],1)} pts")
    return "\n".join(lines) if lines else "Nenhum componente significativo ativo."


def _fallback(bairro: str, risk: dict, raw: dict) -> str:
    score = risk.get("score", 0)
    nivel = risk.get("nivel", "SEGURO")
    components = risk.get("components", {})
    parts = [f"**Por que {score} pontos em {bairro}?**\n"]

    rn = raw.get("chuva_prevista_24h", 0)
    rp = raw.get("chuva_acumulada_24h", 0)
    mare = raw.get("mare_altura", 0)

    if components.get("rain_next", 0) + components.get("rain_past", 0) > 0:
        parts.append(f"🌧️ **Chuva:** {rn}mm previstos e {rp}mm acumulados nas últimas 24h ativam o risco pluvial.")
    if components.get("tide", 0) > 1:
        parts.append(f"🌊 **Maré {mare}m:** {'Acima do normal para Recife (1.8–2.5m)' if float(mare or 0) > 2.5 else 'Moderada'} — contribui para alagamentos em áreas baixas.")
    if components.get("vulnerability", 0) > 0:
        parts.append(f"🏘️ **Vulnerabilidade:** {bairro} tem histórico de alagamentos por proximidade de corpos d'água e baixa altitude.")
    if components.get("altitude", 0) > 0:
        parts.append(f"📏 **Altitude baixa:** Água acumula mais rápido em terrenos abaixo de 10m.")

    parts.append(f"\nA combinação desses fatores resulta em {score} pts ({nivel}).")
    return "\n\n".join(parts)


async def explain_score(bairro: str, risk: dict) -> str:
    now = time.time()
    if bairro in _cache and now - _cache[bairro][0] < CACHE_TTL:
        return _cache[bairro][1]

    raw = risk.get("raw_values", {}) or risk.get("rawValues", {})
    components = risk.get("components", {})

    key = os.getenv("NVIDIA_API_KEY", "")
    if not key:
        result = _fallback(bairro, risk, raw)
        _cache[bairro] = (now, result)
        return result

    comp_str = _build_componentes_str(components, raw)
    prompt = PROMPT.format(
        bairro=bairro,
        score=risk.get("score", 0),
        nivel=risk.get("nivel", "MODERADO"),
        componentes=comp_str,
        umidade=raw.get("umidade", "—"),
        pressao=raw.get("pressao", "—"),
    )

    try:
        client = AsyncOpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=key,
        )
        resp = await client.chat.completions.create(
            model="meta/llama-3.1-8b-instruct",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500,
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            text = _fallback(bairro, risk, raw)
    except Exception as e:
        logger.warning(f"NVIDIA NIM error: {e}")
        text = _fallback(bairro, risk, raw)

    _cache[bairro] = (now, text)
    return text
