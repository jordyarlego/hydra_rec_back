"""Camada de narração das recomendações (híbrido).

As recomendações vêm prontas e determinísticas de services/analytics.py.
Aqui o Gemini só transforma em um parágrafo curto para o gestor. Se a IA
falhar ou não houver chave, cai no fallback determinístico. Espelha o
padrão de services/ai_narrative.py.
"""
from __future__ import annotations
import os
import asyncio
import logging

logger = logging.getLogger(__name__)


def _fallback_narration(recommendations: list[dict]) -> str:
    if not recommendations:
        return "Sem recomendações no momento — volume dentro do normal."
    acoes = "; ".join(r["action"] for r in recommendations)
    n = len(recommendations)
    plural = "ações recomendadas" if n > 1 else "ação recomendada"
    return f"{n} {plural}: {acoes}."


def _build_recommender_prompt(recommendations: list[dict]) -> str:
    linhas = [
        f"- [{r['priority']}] {r['action']} — porque {r['cause']}"
        for r in recommendations
    ]
    corpo = "\n".join(linhas)
    return (
        "Você é analista da sala de operações da Defesa Civil do Recife. "
        "Resuma para o gestor, em até 3 frases curtas e diretas, as ações "
        "prioritárias abaixo. Não invente dados além dos fornecidos.\n\n"
        f"{corpo}"
    )


async def narrate_recommendations(recommendations: list[dict]) -> tuple[str, str]:
    """Retorna (texto, modelo_usado). Gemini com fallback determinístico."""
    if not recommendations:
        return _fallback_narration(recommendations), "local"

    gemini_key = os.getenv("GEMINI_API_KEY", "")
    if gemini_key:
        try:
            from google import genai as google_genai
            client_g = google_genai.Client(api_key=gemini_key)
            prompt = _build_recommender_prompt(recommendations)
            response = await asyncio.to_thread(
                lambda: client_g.models.generate_content(
                    model="gemini-flash-latest",
                    contents=prompt,
                    config={"temperature": 0.2, "max_output_tokens": 160},
                )
            )
            text = (response.text or "").strip()
            if text:
                return text, "Gemini Flash"
        except Exception as e:
            logger.warning(f"recommender narrative error: {e}")

    return _fallback_narration(recommendations), "local"
