import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.heat_index import heat_index_steadman, heat_risk_label


def test_abaixo_limiar_retorna_temperatura():
    assert heat_index_steadman(25, 80) == 25.0
    assert heat_index_steadman(26.9, 90) == 26.9


def test_calor_moderado():
    hi = heat_index_steadman(32, 60)
    assert 32 <= hi <= 42


def test_calor_extremo_recife():
    """Recife 35°C + 85% umidade → sensação bem acima de 41°C."""
    hi = heat_index_steadman(35, 85)
    assert hi >= 45, f"Heat index esperado ≥45°C, veio {hi}"


def test_labels():
    assert heat_risk_label(25) == "CONFORTAVEL"
    assert heat_risk_label(33) == "ATENCAO"
    assert heat_risk_label(42) == "ALERTA"
    assert heat_risk_label(55) == "CRITICO"
