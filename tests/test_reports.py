import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from services.security import hash_ip
from services.risk_score import calc_rain_points


def test_hash_ip_deterministic():
    assert hash_ip("192.168.1.1") == hash_ip("192.168.1.1")


def test_hash_ip_different_ips():
    assert hash_ip("1.2.3.4") != hash_ip("5.6.7.8")


def test_hash_ip_no_plaintext():
    h = hash_ip("192.168.1.1")
    assert "192" not in h
    assert len(h) == 32


def test_report_payload_bbox():
    """Lat/lon dentro do bbox do Recife são válidas."""
    from models.schemas import CreateReportPayload
    p = CreateReportPayload(tipo="alagamento", severidade="moderado", lat=-8.05, lon=-34.88)
    assert p.lat == -8.05


def test_report_payload_bbox_invalid():
    from models.schemas import CreateReportPayload
    from pydantic import ValidationError
    import pytest
    with pytest.raises(ValidationError):
        CreateReportPayload(tipo="alagamento", severidade="moderado", lat=-10.0, lon=-34.88)


def test_report_tipo_invalido():
    from models.schemas import CreateReportPayload
    from pydantic import ValidationError
    import pytest
    with pytest.raises(ValidationError):
        CreateReportPayload(tipo="terremoto", severidade="moderado", lat=-8.05, lon=-34.88)


def test_report_descricao_max():
    from models.schemas import CreateReportPayload
    from pydantic import ValidationError
    import pytest
    with pytest.raises(ValidationError):
        CreateReportPayload(tipo="alagamento", severidade="leve", lat=-8.05, lon=-34.88, descricao="x" * 281)
