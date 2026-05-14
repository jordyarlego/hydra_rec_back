import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from unittest.mock import MagicMock, patch


def _make_mock_client(reports_data):
    client = MagicMock()
    reports_mock = MagicMock()
    alerts_mock = MagicMock()

    def table_router(name):
        if name == "reports":
            return reports_mock
        return alerts_mock

    client.table.side_effect = table_router

    for m in (reports_mock, alerts_mock):
        m.select.return_value = m
        m.eq.return_value = m
        m.gte.return_value = m
        m.gt.return_value = m
        m.insert.return_value = m
        m.update.return_value = m
        m.order.return_value = m

    reports_mock.execute.return_value = MagicMock(data=reports_data)
    alerts_mock.execute.return_value = MagicMock(data=[])
    return client


def test_sem_reports_sem_alert():
    mock = _make_mock_client([])
    with patch("services.alerts_engine.get_service_client", return_value=mock):
        from services import alerts_engine
        result = alerts_engine.check_and_create_alerts("Boa Viagem")
    assert result == []


def test_dois_reports_sem_alert():
    reports = [{"id": "1", "type": "alagamento", "severity": "moderado"},
               {"id": "2", "type": "alagamento", "severity": "leve"}]
    mock = _make_mock_client(reports)
    with patch("services.alerts_engine.get_service_client", return_value=mock):
        from services import alerts_engine
        result = alerts_engine.check_and_create_alerts("Boa Viagem")
    assert result == []


def test_tres_reports_cria_alert():
    reports = [
        {"id": "1", "type": "alagamento", "severity": "moderado"},
        {"id": "2", "type": "alagamento", "severity": "moderado"},
        {"id": "3", "type": "alagamento", "severity": "grave"},
    ]
    mock = _make_mock_client(reports)

    # Give insert its own chain so the select(existing check) still returns []
    insert_exec = MagicMock()
    insert_exec.execute.return_value = MagicMock(data=[{"id": "alert-1"}])
    mock.table("alerts").insert.return_value = insert_exec

    with patch("services.alerts_engine.get_service_client", return_value=mock):
        from services import alerts_engine
        result = alerts_engine.check_and_create_alerts("Boa Viagem")
    assert len(result) >= 1
