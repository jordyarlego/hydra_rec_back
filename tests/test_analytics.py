import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timezone, timedelta
from services.analytics import aggregate_trends

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _r(tipo, bairro, hours_ago):
    return {
        "type": tipo,
        "bairro": bairro,
        "created_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
    }


def _sample():
    return [
        _r("alagamento", "Boa Viagem", 1),
        _r("alagamento", "Boa Viagem", 2),
        _r("alagamento", "Boa Viagem", 3),
        _r("lixo", "Centro", 5),
        _r("alagamento", "Boa Viagem", 30),  # janela anterior
    ]


def test_totais_por_janela():
    t = aggregate_trends(_sample(), NOW, window_hours=24)
    assert t["current_total"] == 4
    assert t["prior_total"] == 1


def test_by_category_desc():
    t = aggregate_trends(_sample(), NOW, window_hours=24)
    assert t["by_category"][0] == {"category": "alagamento", "count": 3}


def test_rising_detecta_alagamento():
    t = aggregate_trends(_sample(), NOW, window_hours=24)
    rising = t["rising"]
    assert len(rising) == 1
    assert rising[0] == {
        "bairro": "Boa Viagem", "category": "alagamento",
        "current": 3, "prior": 1, "delta": 2,
    }


def test_lixo_baixo_volume_nao_e_rising():
    t = aggregate_trends(_sample(), NOW, window_hours=24)
    assert all(e["category"] != "lixo" for e in t["rising"])


def test_lista_vazia():
    t = aggregate_trends([], NOW, window_hours=24)
    assert t["current_total"] == 0
    assert t["by_category"] == []
    assert t["rising"] == []


from services.analytics import build_recommendations


def test_recomenda_para_tendencia_de_alta():
    trends = aggregate_trends(_sample(), NOW, window_hours=24)
    recs = build_recommendations(trends, NOW)
    assert len(recs) == 1
    rec = recs[0]
    assert rec["scope"] == {"bairro": "Boa Viagem", "category": "alagamento"}
    assert rec["priority"] == "alta"
    assert "3 reports de alagamento em Boa Viagem" in rec["cause"]
    assert rec["action"]
    assert rec["id"] == "alagamento:Boa Viagem"


def test_volume_alto_gera_recomendacao_cidade():
    # 12 reports na janela atual, 4 na anterior → pico de volume
    reports = [_r("buraco", "Centro", 1) for _ in range(12)]
    reports += [_r("buraco", "Centro", 30) for _ in range(4)]
    trends = aggregate_trends(reports, NOW, window_hours=24)
    recs = build_recommendations(trends, NOW)
    assert any(r["scope"] == "cidade" for r in recs)


def test_sem_tendencia_sem_recomendacao():
    reports = [_r("lixo", "Centro", 2)]  # 1 report, nada de alta
    trends = aggregate_trends(reports, NOW, window_hours=24)
    assert build_recommendations(trends, NOW) == []
