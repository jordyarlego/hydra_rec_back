import os
import asyncio
import logging

# Import lazy de openai (NVIDIA NIM client): economiza ~30-50MB no boot do Render.

logger = logging.getLogger(__name__)

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
    # Zona Sul
    "Boa Viagem":       "Av. Eng. Domingos Ferreira (trecho Shopping), Canal dos Setúbal, Av. Boa Viagem (Pina)",
    "Pina":             "BR-101 sul, Canal do Pina, Av. República do Líbano",
    "Imbiribeira":      "Av. Sul, BR-101 cruzamento Imbiribeira, Conjunto Imbiribeira",
    "Ibura":            "Comunidade Ibura (encosta), Av. Ibura de Baixo, Rua da Encosta",
    "Jordão":           "Encosta Norte do Jordão, Rua João de Barros, estrada do Ibura",
    "Tejipió":          "Av. Tejipió, planície do Rio Tejipió, Canal Jordão (Sancho)",
    "Mustardinha":      "Av. Senator Nilo Coelho, Rua Padre Carapuceiro, baixada da Mustardinha",
    "Afogados":         "Av. Caxangá km 3, Canal do Capibaribe (Afogados), Av. Recife (Caçote)",
    # Zona Centro / Oeste
    "Santo Amaro":      "Canal da Tacaruna, Rua das Ninfas, Av. Dantas Barreto (trecho baixo)",
    "Paissandu":        "Canal da Tacaruna, Av. Agamenon Magalhães (baixio), Rua Benfica",
    "Boa Vista":        "Praça do Derby, Av. Agamenon Magalhães (viaduto), Av. Cruz Cabugá",
    "Derby":            "Praça do Derby, Rua Gervásio Pires, cruzamento Agamenon/Cruz Cabugá",
    "Madalena":         "Av. Caxangá km 2, Canal do Capibaribe (Madalena), Rua Real da Torre",
    "Torre":            "Mangueirão (baixio), Av. Caxangá (Torre), Canal da Torre",
    "Cordeiro":         "Av. Caxangá (Cordeiro), Canal do Cordeiro, Rua José Bezerra",
    "São José":         "Av. Dantas Barreto, Rua da Aurora / Capibaribe, Comunidade Coque",
    "Recife Antigo":    "Bairro do Recife (costeiro), Cais José Estelita, Marco Zero",
    # Zona Norte
    "Casa Amarela":     "Morro da Conceição, Av. Norte (trecho Casa Amarela), Rua do Futuro",
    "Água Fria":        "Alto do Mandu, Av. Norte (Arruda), encostas do Alto José Bonifácio",
    "Arruda":           "Av. Norte (Arruda/Bomba do Hemetério), Rua Real da Torre",
    "Parnamirim":       "Rua Real da Torre, Av. Norte (Parnamirim), área do Metrô Joana Bezerra",
    # Zona Sul-Sudeste
    "Brasília Teimosa": "Av. Herculano Bandeira, Comunidade Coque, Beira Rio",
    "Graças":           "Av. Boa Viagem norte, Canal dos Setúbal, Rua das Graças",
    "Espinheiro":       "Av. Rui Barbosa, Av. Agamenon (Espinheiro), Rua Espinheiro",
    "default":          None,
}

PROMPT_V2 = """Você é analista da Defesa Civil do Recife. Escreva diretamente — sem introdução, sem conclusão, sem comentário sobre o texto.

CONTEXTO DO BAIRRO:
• Bairro: {bairro}
• Histórico de alagamentos: {vuln_pct}% dos anos (2018-2024)
• Pontos de risco mapeados: {pontos_criticos_lista}

DADOS AGORA — {sources_count} fontes cruzadas, confiança {confidence}:
{data_sources}
• Chuva prevista 24h: {rain_next}mm  → {rain_plain}
• Chuva acumulada 24h: {rain_past}mm
• Maré: {tide_h}m, tendência {tide_trend}
• Temperatura: {temp}°C, sensação de {feels_like}°C
• Umidade: {humidity}%  → {humidity_plain}
• Vento: {wind}km/h  → {wind_plain}
• Pressão atmosférica: {pressure}mb  → {pressure_plain}
• Visibilidade: {visibility}

HYDRA SCORE: {score}/100 — nível {nivel}
{apac_section}
REPORTS RECENTES:
{reports_lista}

==============
ESCREVA EXATAMENTE 4 FRASES. Cada frase em sua própria linha. Comece JÁ com a primeira frase — ZERO introdução.

FRASE 1 — SITUAÇÃO AGORA: O que está acontecendo neste momento em {bairro}? Cite o score, os mm de chuva e mencione UMA das fontes de dados ({data_sources_inline}). Use palavras simples que qualquer morador entenda.

FRASE 2 — PONTO DE ATENÇÃO: Cite uma rua ou ponto de "{pontos_criticos_lista}" com o tom PROPORCIONAL ao nível de risco:
  • SEGURO / ATENCAO (score < 45): "Recomendamos atenção em [X], que costuma acumular água em chuvas mais intensas."
  • MODERADO (score 45–64): "Evite passar por [X] durante o pico da chuva se possível."
  • ALTO / SEVERO (score ≥ 65): "Evite [X] agora — risco ativo de alagamento."
  Se a lista vier vazia, adapte ao nível sem citar rua específica.

FRASE 3 — JANELA DE TEMPO: Quanto tempo dura esse cenário? Use os {rain_next}mm e o nível {nivel} como base. Seja concreto — sem hipóteses.

FRASE 4 — AÇÃO CONCRETA: Uma ação executável agora para o nível {nivel}. SEGURO/ATENÇÃO: rotina normal com precaução leve. MODERADO: cuidado em percurso específico. ALTO/SEVERO: evitar deslocamento ou abrigar.

PROIBIDO em qualquer frase:
- Preamble, introdução ou meta-comentário ("Aqui estão", "Com base nos dados", "De acordo com")
- Numeração ("1.", "2.", "3.", "4.")
- "se chover", "caso chova", "pode acontecer"
- "heat index", "NOAA", "logístico", "Hydra Score", "Open-Meteo" como palavras isoladas sem contexto
- "recolha objetos", "verifique nível de água", "monitore", "fique atento", "consulte autoridades"
- Frase com mais de 30 palavras
- "bocas de lobo", "alagamento" ou "inundação" sem base nos dados: só use se rain_next_24h > 25mm OU APAC ALTO/SEVERO OU report confirmado de alagamento nos dados acima

Máximo 95 palavras no total. Português simples do dia a dia do Recife."""


def _build_fallback(bairro: str, risk: dict, consensus: dict) -> str:
    nivel      = risk.get("nivel", "MODERADO")
    score      = risk.get("score", 0)
    rain_next  = consensus.get("rain_next_24h_mm", 0)
    rain_past  = consensus.get("rain_past_24h_mm", 0)
    wind       = round(consensus.get("wind_speed_kmh", 0))
    humidity   = round(consensus.get("humidity", 70))
    ponto_raw  = _PONTOS.get(bairro, _PONTOS["default"])
    ponto      = ponto_raw.split(",")[0].strip() if ponto_raw else None
    raw        = risk.get("rawValues", {}) or risk.get("raw_values", {})
    tide       = raw.get("mareAltura") or raw.get("mare_altura", "—")

    sources_block, sources_inline = _build_sources_block(consensus)
    fonte_ref = sources_inline or "Open-Meteo"

    rain_desc = _rain_plain(rain_next)
    wind_desc = _wind_plain(wind)

    if score >= 65:
        diagnosis = f"Risco {nivel} agora em {bairro}: score {score}/100 — {rain_next:.0f}mm previstos ({rain_desc}) segundo {fonte_ref}, maré {tide}m."
        action = f"{'Saia de ' + ponto + ' agora' if ponto else 'Saia das ruas baixas de ' + bairro + ' agora'} e deixe o veículo em local alto."
    elif score >= 45:
        diagnosis = f"Atenção em {bairro}: score {score}/100, {rain_next:.0f}mm previstos ({rain_desc}) por {fonte_ref}. Vento {wind}km/h ({wind_desc})."
        action = f"{'Evite ' + ponto + ' no seu trajeto.' if ponto else 'Prefira ruas altas no seu trajeto de hoje.'}"
    else:
        diagnosis = f"{bairro} está tranquilo agora: score {score}/100, {rain_next:.0f}mm previstos ({rain_desc}), umidade {humidity}%."
        action = "Siga sua rotina normalmente; se a chuva apertar, prefira ruas altas no retorno."

    if score >= 65:
        if ponto:
            location_line = f"Evite {ponto} agora — risco ativo nos trechos baixos."
        else:
            location_line = f"Evite ruas baixas de {bairro} agora — risco ativo nos trechos baixos."
    elif score >= 45:
        if ponto:
            location_line = f"Evite passar por {ponto} durante o pico da chuva se possível."
        else:
            location_line = f"Evite cruzamentos baixos de {bairro} durante o pico da chuva se possível."
    else:
        if ponto:
            location_line = f"Recomendamos atenção em {ponto}, que costuma acumular água em chuvas mais intensas."
        else:
            location_line = f"Recomendamos atenção aos trechos baixos de {bairro} em chuvas mais intensas."

    if rain_next > 15:
        timing_line = f"Risco ativo nas próximas 12h com {rain_next:.0f}mm previstos — pico esperado no período da tarde."
    elif rain_next > 3:
        timing_line = f"Risco ativo nas próximas 6h com {rain_next:.1f}mm previstos."
    elif rain_past > 10:
        timing_line = f"Chuva recente ({rain_past:.0f}mm nas últimas 24h) mantém solo saturado; {rain_next:.1f}mm adicionais previstos."
    else:
        timing_line = f"Cenário estável pelas próximas 6h com {rain_next:.1f}mm previstos."

    return "\n".join([diagnosis, location_line, timing_line, action])


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


def _rain_plain(mm: float) -> str:
    if mm == 0:    return "sem chuva prevista — tempo firme"
    if mm < 1:     return "garoa leve — asfalto úmido, sem impacto no trânsito"
    if mm < 5:     return "chuva fina — algumas poças em calçadas, trânsito normal"
    if mm < 15:    return "chuva fraca a moderada — piso molhado, possíveis poças em pontos baixos"
    if mm < 25:    return "chuva moderada — atenção nos trechos baixos e canais"
    if mm < 40:    return "chuva forte — risco de acúmulo em baixadas, bocas de lobo sobrecarregadas"
    return "chuva muito forte — alagamento provável em pontos baixos, evite áreas de risco"


def _humidity_plain(h: float) -> str:
    if h < 40:  return "ar muito seco, desconfortável"
    if h < 60:  return "umidade agradável"
    if h < 80:  return "úmido, sensação de calor aumenta"
    return "muito úmido, sensação térmica +4–6°C acima da temperatura real"


def _wind_plain(kmh: float) -> str:
    if kmh < 20:  return "brisa leve, sem impacto"
    if kmh < 40:  return "vento moderado — dificulta guarda-chuva aberto"
    if kmh < 60:  return "vento forte — cuidado com galhos e objetos soltos"
    return "vento muito forte — risco de queda de árvores e placas"


def _pressure_plain(mb: float) -> str:
    if mb < 1005: return "baixa pressão — chuva ou tempestade muito provável"
    if mb < 1010: return "pressão instável — tempo incerto"
    if mb < 1020: return "pressão normal"
    return "alta pressão — tendência de tempo firme"


def _visibility_plain(meters: float) -> str:
    if meters == 0:     return "dado não disponível"
    km = meters / 1000
    if km < 1:   return f"{km:.1f}km — neblina densa ou chuva muito forte, dirija com atenção máxima"
    if km < 5:   return f"{km:.0f}km — visibilidade reduzida"
    if km < 10:  return f"{km:.0f}km — boa"
    return f"{km:.0f}km — excelente"


def _format_apac(apac_boletim) -> str:
    if not apac_boletim:
        return ""
    if isinstance(apac_boletim, dict):
        nivel = apac_boletim.get("nivel", "")
        titulo = apac_boletim.get("titulo", "")
        texto = apac_boletim.get("texto", "")[:180]
        if not nivel or nivel == "SEGURO":
            return ""
        parts = [f"\nBOLETIM OFICIAL APAC — {nivel}"]
        if titulo:
            parts.append(f"Título: {titulo}")
        if texto:
            parts.append(f"Texto: {texto}")
        return "\n".join(parts) + "\n"
    if isinstance(apac_boletim, str) and apac_boletim.strip():
        return f"\nBOLETIM OFICIAL APAC:\n{apac_boletim[:200]}\n"
    return ""


def _build_sources_block(consensus: dict) -> tuple[str, str]:
    """Returns (multi-line block, inline comma list) of data sources."""
    sources = consensus.get("sources_used", [])
    if not sources:
        # Reconstruct from known fields
        sources = ["Open-Meteo"]
        if consensus.get("sources_count", 1) >= 2:
            sources.append("OpenWeatherMap")
        if consensus.get("sources_count", 1) >= 3:
            sources.append("INMET A301")

    source_map = {
        "Open-Meteo":     "Open-Meteo (previsão horária europeia)",
        "OpenWeatherMap": "OpenWeatherMap (blocos 3h)",
        "INMET A301":     "INMET estação A301 Recife (observação oficial)",
        "INMET A357":     "INMET estação A357 Olinda (observação oficial)",
        "FEMAR":          "FEMAR (tabua de marés Recife)",
    }
    lines = []
    for s in sources:
        mapped = source_map.get(s, s)
        lines.append(f"• {mapped}")

    block = "\n".join(lines) if lines else "• Open-Meteo (previsão horária)"
    inline = " e ".join(
        s.replace("Open-Meteo", "previsão do Open-Meteo")
         .replace("OpenWeatherMap", "dados do OpenWeatherMap")
         .replace("INMET A301", "estação INMET A301")
         .replace("INMET A357", "estação INMET A357")
        for s in sources[:2]
    )
    return block, inline or "dados meteorológicos oficiais"


def _build_prompt(bairro: str, risk: dict, consensus: dict, nearby_reports, apac_boletim):
    raw = risk.get("rawValues", {}) or risk.get("raw_values", {})
    rain_next  = round(consensus.get("rain_next_24h_mm", 0), 1)
    humidity   = round(consensus.get("humidity", 70))
    wind       = round(consensus.get("wind_speed_kmh", 0))
    pressure   = round(consensus.get("pressure", 1013))
    visibility = consensus.get("visibility_m", 0)
    pontos = _PONTOS.get(bairro, _PONTOS["default"])
    pontos_str = pontos if pontos else ""
    sources_block, sources_inline = _build_sources_block(consensus)
    return PROMPT_V2.format(
        bairro=bairro,
        vuln_pct=_VULN.get(bairro, 55),
        pontos_criticos_lista=pontos_str,
        sources_count=consensus.get("sources_count", 1),
        confidence=consensus.get("confidence", "MEDIA"),
        data_sources=sources_block,
        data_sources_inline=sources_inline,
        rain_next=rain_next,
        rain_plain=_rain_plain(rain_next),
        rain_past=round(consensus.get("rain_past_24h_mm", 0), 1),
        tide_h=raw.get("mareAltura") or raw.get("mare_altura", "—"),
        tide_trend=raw.get("mareTrend") or raw.get("mare_trend", "—"),
        humidity=humidity,
        humidity_plain=_humidity_plain(humidity),
        wind=wind,
        wind_plain=_wind_plain(wind),
        pressure=pressure,
        pressure_plain=_pressure_plain(pressure),
        visibility=_visibility_plain(visibility),
        temp=round(consensus.get("temperature", 28)),
        feels_like=round(consensus.get("apparent_temperature", consensus.get("temperature", 28))),
        score=risk.get("score", 0),
        nivel=risk.get("nivel", "MODERADO"),
        apac_section=_format_apac(apac_boletim),
        reports_lista=_format_reports(nearby_reports or []),
    )


async def generate_narrative(
    bairro: str,
    risk: dict,
    consensus: dict,
    nearby_reports: list | None = None,
    apac_boletim: str | None = None,
) -> tuple[str, str]:
    """Retorna (texto_narrativa, modelo_usado)."""
    prompt = _build_prompt(bairro, risk, consensus, nearby_reports, apac_boletim)

    # Tentativa 1 e 2: NVIDIA NIM
    nvidia_key = os.getenv("NVIDIA_API_KEY", "")
    if nvidia_key:
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=nvidia_key,
            )
            for model in ("nvidia/llama-3.3-nemotron-super-49b-v1", "meta/llama-3.3-70b-instruct"):
                try:
                    resp = await client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.3,
                        max_tokens=220,
                    )
                    text = (resp.choices[0].message.content or "").strip()
                    if text:
                        short_name = "Nemotron 49B" if "nemotron" in model else "Llama 70B"
                        return text, short_name
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"NVIDIA NIM narrative error: {e}")

    # Tentativa 3: Gemini 1.5 Flash
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    if gemini_key:
        try:
            from google import genai as google_genai
            client_g = google_genai.Client(api_key=gemini_key)
            response = await asyncio.to_thread(
                lambda: client_g.models.generate_content(
                    model="gemini-1.5-flash",
                    contents=prompt,
                    config={"temperature": 0.3, "max_output_tokens": 220},
                )
            )
            text = (response.text or "").strip()
            if text:
                return text, "Gemini Flash"
        except Exception as e:
            logger.warning(f"Gemini narrative error: {e}")

    # Tentativa 4: fallback local — dados reais, sem chamada externa
    return _build_fallback(bairro, risk, consensus), "local"
