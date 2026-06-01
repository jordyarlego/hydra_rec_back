import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.ai_recommender import _fallback_narration, _build_recommender_prompt

_RECS = [{
    "id": "alagamento:Boa Viagem",
    "scope": {"bairro": "Boa Viagem", "category": "alagamento"},
    "action": "Acionar Defesa Civil e monitorar pontos de alagamento em Boa Viagem",
    "cause": "3 reports de alagamento em Boa Viagem nas últimas 24h (janela anterior: 1)",
    "priority": "alta",
}]


def test_fallback_vazio():
    assert "dentro do normal" in _fallback_narration([])


def test_fallback_lista_acoes():
    txt = _fallback_narration(_RECS)
    assert "Acionar Defesa Civil" in txt


def test_prompt_inclui_causa():
    prompt = _build_recommender_prompt(_RECS)
    assert "3 reports de alagamento em Boa Viagem" in prompt
    assert "Acionar Defesa Civil" in prompt
