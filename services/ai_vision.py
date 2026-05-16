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


def _fallback_from_text(text: str = "") -> dict[str, Any]:
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
        kind = "outro"
    return {
        "description": text.strip()[:220] or "Foto recebida; descrição automática indisponível.",
        "suggested_type": kind,
        "confidence": 0.35 if kind != "outro" else 0.2,
    }


def _parse_jsonish(raw: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", raw or "", flags=re.S)
    if not match:
        return _fallback_from_text(raw)
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return _fallback_from_text(raw)
    kind = data.get("type") or data.get("suggested_type") or "outro"
    if kind not in VALID_TYPES:
        kind = "outro"
    return {
        "description": str(data.get("description") or data.get("descricao") or "").strip()[:300],
        "suggested_type": kind,
        "confidence": max(0.0, min(float(data.get("confidence", 0.5)), 1.0)),
    }


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=12.0) as client:
        res = await client.get(url)
        res.raise_for_status()
        return res.content


async def describe_photo(photo_bytes_or_url: bytes | str) -> dict[str, Any]:
    """
    Descreve foto de ocorrência urbana. Nunca levanta erro para o fluxo principal:
    falhas retornam fallback com confidence baixa.
    """
    key = os.getenv("GEMINI_API_KEY", "")
    if not key:
        return _fallback_from_text()

    try:
        image_bytes = await _download(photo_bytes_or_url) if isinstance(photo_bytes_or_url, str) else photo_bytes_or_url
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key)
        prompt = (
            "Analise esta foto de uma ocorrência urbana em Recife. "
            "Descreva em 1 frase e classifique entre: alagamento, deslizamento, "
            "queda_arvore, via_intransitavel, poste_caido, buraco, lixo, iluminacao, outro. "
            "Retorne apenas JSON: {\"description\":\"...\",\"type\":\"...\",\"confidence\":0.0}"
        )
        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                prompt,
            ],
        )
        return _parse_jsonish(getattr(response, "text", "") or "")
    except Exception as e:
        logger.warning("ai_vision fallback: %s", e)
        return _fallback_from_text()
