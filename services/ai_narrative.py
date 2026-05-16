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


# ── Prompt V3 — APAC native, 3 linhas, linguagem cidadã ────────────────────

PROMPT_V3 = """Sua tarefa: escrever EXATAMENTE 3 frases para o morador do Recife.

REGRA CRÍTICA — saída:
- Comece DIRETO na frase 1. Sem "Aqui estão", "Resposta:", "Linhas:", sem nada antes.
- Cada frase em sua própria linha.
- Sem numeração (1./2./3.).
- Sem aspas, sem markdown, sem comentário sobre o que está fazendo.
- Última linha = última frase. Pare. Não escreva nada depois.

CONTEXTO:
- Bairro: {bairro}
- Histórico de alagamentos no bairro: {vuln_pct}% dos anos recentes
- Pontos baixos conhecidos: {pontos}

DADOS:
- Chovendo agora? {is_raining}
- Quanto: {rain_1h_mm} mm/h ({rain_phrase})
- Choveu nas últimas 24h: {rain_24h_mm} mm
- Índice de risco: {score}/100 ({nivel})
- Reports recentes da vizinhança: {reports_lista}

REGRAS DE CONTEÚDO:
- SE rain_1h_mm < 0.2 E rain_24h_mm < 5 (tempo limpo):
  Frase 1: tempo tranquilo em {bairro}, sem chuva agora.
  Frase 2: comentário sobre rotina ou clima da época, SEM citar ponto de alagamento.
  Frase 3: o que esperar — estável, sem mudança prevista.
- SE rain_1h_mm >= 0.2 OU rain_24h_mm >= 5:
  Frase 1: descreva a chuva caindo agora em {bairro}.
  Frase 2: cite UM ponto de "{pontos}" e o que fazer (evitar/cuidado).
  Frase 3: o que esperar nas próximas horas.

PROIBIDO:
- Siglas APAC, CEMADEN, RMR, INMET.
- "Hydra Score" (use "índice" ou "risco").
- Citar alagamento/risco quando rain_1h_mm < 0.2 E rain_24h_mm < 5.
- "se chover", "caso chova", "monitore", "fique atento".
- Mais de 16 palavras por frase.

Máximo 50 palavras totais."""


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

    rain_1h_val = rain_1h if isinstance(rain_1h, (int, float)) else 0
    return PROMPT_V3.format(
        bairro=bairro,
        vuln_pct=_VULN.get(bairro, 55),
        pontos=_PONTOS.get(bairro, "—"),
        is_raining="Sim" if rain_1h_val >= 0.2 else "Não",
        rain_1h_mm=f"{rain_1h:.1f}" if isinstance(rain_1h, (int, float)) else "0.0",
        rain_phrase=_rain_phrase(rain_1h_val),
        rain_24h_mm=f"{rain_24h:.1f}" if isinstance(rain_24h, (int, float)) else "0.0",
        score=score,
        nivel=nivel,
        reports_lista=_format_reports(reports or []),
    )


# ── Fallback local (3 linhas) ─────────────────────────────────────────────

def _build_fallback(bairro: str, risk: dict, weather: Optional[dict]) -> str:
    nivel = (risk.get("nivel", "SEGURO") or "SEGURO").upper()
    score = risk.get("score", 0)
    w = weather or {}
    rain = w.get("rain_1h_mm") or 0
    rain_24h = w.get("rain_24h_mm") or 0
    ponto = _PONTOS.get(bairro)
    primeiro_ponto = ponto.split(",")[0].strip() if ponto else None

    # Cenário 1: SEM chuva e SEM acúmulo — não inventa risco
    if rain < 0.2 and rain_24h < 5:
        line1 = f"O tempo está tranquilo em {bairro} agora — não está chovendo."
        line2 = f"Dia normal pra sair, sem risco no momento (índice {score}/100)."
        line3 = "Sem previsão de chuva nas próximas horas pelos sensores próximos."
        return "\n".join([line1, line2, line3])

    # Cenário 2: chuva ativa OU acúmulo relevante
    if rain >= 0.2:
        line1 = f"{_rain_phrase(rain).capitalize()} agora em {bairro} ({rain:.1f} mm por hora)."
    else:
        line1 = f"{bairro} parou de chover, mas choveu {rain_24h:.0f} mm nas últimas 24h."

    if nivel in ("SEVERO", "ALTO") and primeiro_ponto:
        line2 = f"Evite {primeiro_ponto} agora — risco de alagamento."
    elif nivel == "MODERADO" and primeiro_ponto:
        line2 = f"Cuidado em {primeiro_ponto} durante a chuva."
    elif primeiro_ponto:
        line2 = f"Fique atento se passar por {primeiro_ponto}."
    else:
        line2 = f"Cuidado em ruas baixas de {bairro}."

    if rain >= 10:
        line3 = "A chuva deve continuar nas próximas horas — solo já saturado."
    elif rain >= 2.5:
        line3 = "A chuva pode aumentar nas próximas horas, fique de olho."
    else:
        line3 = "Tempo deve melhorar nas próximas horas."

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
                    model="gemini-flash-latest",
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


_META_LINE_PATTERNS = [
    r"^aqui (estão|vão|seguem)",
    r"^seguem? (as|3)",
    r"^resposta\s*:",
    r"^linhas?\s*:",
    r"^output\s*:",
    r"^conforme (solicitado|pedido)",
    r"^atendendo (aos|as|ao)",
    r"^cenário\s*:",
    r"^claro!?\s",
    r"^certo!?\s",
    r"^entendido",
    r"^(em|nas?) seguid",
]


def _is_meta_line(line: str) -> bool:
    import re
    low = line.lower().strip()
    if not low:
        return True
    # Linha que termina com ":" e tem menos de 60 chars → provavelmente label
    if low.endswith(":") and len(low) < 60:
        return True
    return any(re.match(pat, low) for pat in _META_LINE_PATTERNS)


def _strip_markdown(line: str) -> str:
    """Remove **, *, _, `, > em volta da linha."""
    import re
    line = re.sub(r"^[*_>\s`]+", "", line)
    line = re.sub(r"[*_`]+$", "", line)
    return line.strip()


def _enforce_3_lines(text: str) -> str:
    """Garante 3 linhas curtas, sem meta-comentário nem prefixo."""
    import re
    cleaned = []
    for raw in (text or "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        line = _strip_markdown(line)
        # Remove prefixos enumerados / labels
        line = re.sub(r"^\d+[\.\)]\s*", "", line)
        line = re.sub(r"^(FRASE|LINHA)\s*\d+\s*[—\-:]?\s*", "", line, flags=re.I)
        line = re.sub(r"^(SITUAÇÃO|AÇÃO|PONTO|JANELA|DIAGNÓSTICO|RECOMENDAÇÃO)[^:]*:\s*", "", line, flags=re.I)
        line = line.strip()
        if not line or _is_meta_line(line):
            continue
        cleaned.append(line)

    # Pega as primeiras 3 linhas substantivas
    return "\n".join(cleaned[:3]) if cleaned else text
