from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

VALID_TYPES = {
    "alagamento", "deslizamento", "queda_arvore", "via_intransitavel",
    "poste_caido", "buraco", "lixo", "iluminacao", "outro",
}


def _fallback_from_text(text: str = "", reason: str = "no_ai") -> dict[str, Any]:
    """
    Quando IA falhou ou está indisponível. Se text vier, tenta heurística.
    Quando text=="", retornamos `description=None` e `ai_used=False` para o
    frontend saber que não vale exibir a mensagem ao usuário.

    `is_urban_problem=None` no fallback: o validator interpreta None como
    "não consigo afirmar" e NÃO dispara o gate (preserva comportamento
    quando a IA está fora do ar).
    """
    lower = text.lower()
    if any(w in lower for w in ("água", "agua", "alag", "enchente", "poça")):
        kind = "alagamento"
    elif any(w in lower for w in ("árvore", "arvore", "galho")):
        kind = "queda_arvore"
    elif any(w in lower for w in ("poste", "fio", "energia")):
        kind = "poste_caido"
    elif any(w in lower for w in ("buraco", "cratera")):
        kind = "buraco"
    elif any(w in lower for w in ("lixo", "entulho")):
        kind = "lixo"
    else:
        kind = None
    return {
        "description": text.strip()[:220] if text.strip() else None,
        "suggested_type": kind,
        "confidence": (0.35 if kind else 0.0),
        "is_urban_problem": None,
        "severity_hint": None,
        "ai_used": False,
        "fallback_reason": reason,
    }


def _parse_jsonish(raw: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", raw or "", flags=re.S)
    if not match:
        return _fallback_from_text(raw, reason="no_json")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return _fallback_from_text(raw, reason="bad_json")
    kind = data.get("type") or data.get("suggested_type") or "outro"
    if kind not in VALID_TYPES:
        kind = "outro"

    # is_urban_problem aceita bool ou string ("true"/"false"/"sim"/"nao")
    raw_urban = data.get("is_urban_problem")
    if isinstance(raw_urban, bool):
        is_urban = raw_urban
    elif isinstance(raw_urban, str):
        v = raw_urban.strip().lower()
        is_urban = True if v in ("true", "sim", "yes", "1") else False if v in ("false", "nao", "não", "no", "0") else None
    else:
        is_urban = None

    sev = (data.get("severity_hint") or "").strip().lower()
    if sev not in ("grave", "moderado", "leve", "desconhecido"):
        sev = None

    return {
        "description": str(data.get("description") or data.get("descricao") or "").strip()[:300] or None,
        "suggested_type": kind,
        "confidence": max(0.0, min(float(data.get("confidence", 0.5)), 1.0)),
        "is_urban_problem": is_urban,
        "severity_hint": sev,
    }


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=12.0) as client:
        res = await client.get(url)
        res.raise_for_status()
        return res.content


_VISION_PROMPT = (
    "Analise esta foto enviada como ocorrência cívica no Recife.\n"
    "1. is_urban_problem: a foto mostra um PROBLEMA URBANO REAL "
    "(rua, calçada, poste, árvore caída, alagamento, lixo, infraestrutura)? "
    "Responda false se for: pessoa/selfie, animal de estimação, comida, "
    "tela de celular/jogo, desenho, meme, paisagem natural sem problema, "
    "interior de residência, ou qualquer coisa não relacionada a problema urbano.\n"
    "2. type: se for problema urbano, classifique como UM destes:\n"
    "   - alagamento: água acumulada em via, rua, calçada\n"
    "   - deslizamento: terra/encosta caindo, barranco rompido\n"
    "   - queda_arvore: árvore caída na via, galho grande no chão\n"
    "   - via_intransitavel: rua bloqueada, interdição, obra parada\n"
    "   - poste_caido: POSTE FÍSICO no chão, tombado, quebrado, partido\n"
    "     (NÃO use se a foto mostra poste em pé com lâmpada apagada/queimada)\n"
    "   - buraco: cratera, asfalto faltando, valeta\n"
    "   - lixo: acúmulo de lixo, entulho, descarte irregular\n"
    "   - iluminacao: poste com LÂMPADA APAGADA/QUEIMADA, escuridão na via,\n"
    "     fiação solta da iluminação (poste fica EM PÉ na foto)\n"
    "   - outro: problema urbano que não cabe nas anteriores.\n"
    "   IMPORTANTE: foto de noite com poste vertical sem luz = 'iluminacao',\n"
    "   NÃO 'poste_caido'. 'poste_caido' é só quando o poste está literalmente\n"
    "   no chão / tombado / quebrado.\n"
    "   Se NÃO for problema urbano, use 'outro'.\n"
    "3. description: NO MÁXIMO 8 PALAVRAS descrevendo o que você vê.\n"
    "4. confidence: 0.0-1.0 do quanto você está seguro da classificação.\n"
    "5. severity_hint: gravidade VISUAL do problema:\n"
    "   - 'grave': risco imediato (cratera grande, alagamento na altura do joelho,\n"
    "     árvore em fio elétrico, deslizamento ativo, via totalmente bloqueada);\n"
    "   - 'moderado': atrapalha mas tem como contornar (buraco médio,\n"
    "     lixo acumulado, iluminação apagada em via comum);\n"
    "   - 'leve': incômodo sem risco direto (pequeno entulho, lâmpada queimada\n"
    "     em rua iluminada por outras);\n"
    "   - 'desconhecido': não consegue avaliar pela foto.\n\n"
    "Responda APENAS o JSON sem texto antes ou depois:\n"
    "{\"is_urban_problem\":true|false,\"description\":\"frase curta\","
    "\"type\":\"categoria\",\"confidence\":0.0-1.0,"
    "\"severity_hint\":\"grave|moderado|leve|desconhecido\"}"
)


async def _try_gemini(image_bytes: bytes) -> dict[str, Any] | None:
    """Retorna dict com ai_used=True se conseguiu, None se falhou."""
    key = os.getenv("GEMINI_API_KEY", "")
    if not key:
        return None
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model="gemini-flash-latest",
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                _VISION_PROMPT,
            ],
        )
        parsed = _parse_jsonish(getattr(response, "text", "") or "")
        if parsed.get("description"):
            parsed["ai_used"] = True
            parsed["ai_model"] = "gemini-flash"
            return parsed
    except Exception as e:
        logger.warning("gemini_vision falhou: %s: %s", type(e).__name__, e)
    return None


async def _try_nvidia(image_bytes: bytes) -> dict[str, Any] | None:
    """Fallback NVIDIA NIM com llama 3.2 vision (90B). Retorna None se falhar."""
    key = os.getenv("NVIDIA_API_KEY", "")
    if not key:
        return None
    try:
        import base64
        from openai import AsyncOpenAI
        b64 = base64.b64encode(image_bytes).decode("ascii")
        if len(b64) > 180_000:  # NVIDIA NIM tem limite de ~200KB no payload
            return None
        client = AsyncOpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=key,
            timeout=20.0,
        )
        for model in ("meta/llama-3.2-11b-vision-instruct", "meta/llama-3.2-90b-vision-instruct"):
            try:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _VISION_PROMPT},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        ],
                    }],
                    max_tokens=90,
                    temperature=0.1,
                )
                text = (resp.choices[0].message.content or "").strip()
                if text:
                    parsed = _parse_jsonish(text)
                    if parsed.get("description"):
                        parsed["ai_used"] = True
                        parsed["ai_model"] = f"nvidia/{model.split('/')[-1]}"
                        return parsed
            except Exception as e:
                logger.warning(f"nvidia vision {model} falhou: {type(e).__name__}: {e}")
                continue
    except Exception as e:
        logger.warning("nvidia_vision setup falhou: %s: %s", type(e).__name__, e)
    return None


async def describe_photo(photo_bytes_or_url: bytes | str) -> dict[str, Any]:
    """
    Descreve foto de ocorrência urbana. Tenta Gemini primeiro, NVIDIA como
    fallback. Nunca levanta erro para o fluxo principal.
    """
    try:
        image_bytes = (
            await _download(photo_bytes_or_url)
            if isinstance(photo_bytes_or_url, str)
            else photo_bytes_or_url
        )
    except Exception as e:
        logger.warning("ai_vision download falhou: %s", e)
        return _fallback_from_text(reason=f"download_error:{type(e).__name__}")

    # NVIDIA primeiro (sem cota diária estourando) → Gemini de reserva
    result = await _try_nvidia(image_bytes)
    if result:
        return result

    result = await _try_gemini(image_bytes)
    if result:
        return result

    logger.warning("ai_vision: nenhum provider retornou descrição válida")
    return _fallback_from_text(reason="all_providers_failed")
