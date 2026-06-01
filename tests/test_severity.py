import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.severity import infer_initial_severity, resolve_severity_from_vision


def test_deslizamento_baseline_grave():
    assert infer_initial_severity("deslizamento", None) == "grave"


def test_lixo_baseline_leve():
    assert infer_initial_severity("lixo", None) == "leve"


def test_alagamento_sem_chuva_moderado():
    assert infer_initial_severity("alagamento", {"rain_1h_mm": 0}) == "moderado"


def test_alagamento_chuva_forte_grave():
    assert infer_initial_severity("alagamento", {"rain_1h_mm": 35}) == "grave"


def test_categoria_desconhecida_moderado():
    assert infer_initial_severity("terremoto", None) == "moderado"


def test_resolve_usa_hint_valido():
    assert resolve_severity_from_vision("moderado", "grave") == "grave"


def test_resolve_ignora_hint_desconhecido():
    assert resolve_severity_from_vision("leve", "desconhecido") == "leve"


def test_resolve_ignora_hint_none():
    assert resolve_severity_from_vision("moderado", None) == "moderado"
