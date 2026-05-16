"""Smoke tests — verifica que o app sobe e as rotas principais existem."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from main import app


def test_app_created():
    assert app.title.startswith("HydraRec")


def test_required_routes_exist():
    paths = {r.path for r in app.routes}
    required = {
        "/api/healthz",
        "/api/dashboard/{bairro}",
        "/api/narrative",
        "/api/weather",
        "/api/reports",
        "/api/reports/with-photo",
        "/api/reports/nearby",
        "/api/apac/boletim",
        "/api/ai/report-assist",
    }
    missing = required - paths
    assert not missing, f"Rotas faltando: {missing}"


def test_bairros_coords_loaded():
    from data.bairros_coords import BAIRRO_COORDS
    assert "Boa Viagem" in BAIRRO_COORDS
    assert "Jordão" in BAIRRO_COORDS
    assert len(BAIRRO_COORDS) >= 90


def test_vulnerability_data():
    from data.vulnerability import FLOOD_VULNERABILITY, DEFAULT_VULNERABILITY
    assert "Brasília Teimosa" in FLOOD_VULNERABILITY
    assert FLOOD_VULNERABILITY["Brasília Teimosa"] > 0.9
    assert 0 < DEFAULT_VULNERABILITY < 1
