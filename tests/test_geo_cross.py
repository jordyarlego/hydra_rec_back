"""
Testes para geo_cross — geometria pura + cruzamento com Supabase mockado.
"""
import pytest
from unittest.mock import MagicMock, patch


# ── Haversine ──────────────────────────────────────────────────────────────

def test_haversine_same_point():
    from services.geo_cross import haversine_distance_m
    assert haversine_distance_m(-8.12, -34.90, -8.12, -34.90) == pytest.approx(0, abs=1)


def test_haversine_known_distance():
    from services.geo_cross import haversine_distance_m
    # Boa Viagem → UFPE (aprox 8.5–9.5 km)
    d = haversine_distance_m(-8.1180, -34.9008, -8.0528, -34.9495)
    assert 8000 < d < 10000


def test_haversine_symmetry():
    from services.geo_cross import haversine_distance_m
    d1 = haversine_distance_m(-8.12, -34.90, -8.10, -34.91)
    d2 = haversine_distance_m(-8.10, -34.91, -8.12, -34.90)
    assert d1 == pytest.approx(d2, abs=1)


# ── Ray-casting (point_in_polygon) ─────────────────────────────────────────

def test_point_in_ring_inside():
    from services.geo_cross import _point_in_ring
    # Quadrado simples ao redor da origem
    ring = [[-1, -1], [1, -1], [1, 1], [-1, 1], [-1, -1]]
    assert _point_in_ring(0, 0, ring) is True


def test_point_in_ring_outside():
    from services.geo_cross import _point_in_ring
    ring = [[-1, -1], [1, -1], [1, 1], [-1, 1], [-1, -1]]
    assert _point_in_ring(5, 5, ring) is False


def test_point_in_polygon_with_hole():
    from services.geo_cross import _point_in_polygon
    outer = [[-2, -2], [2, -2], [2, 2], [-2, 2], [-2, -2]]
    hole = [[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5], [-0.5, -0.5]]
    # Dentro do anel externo mas fora do buraco
    assert _point_in_polygon(1.5, 1.5, [outer, hole]) is True
    # Dentro do buraco → False
    assert _point_in_polygon(0, 0, [outer, hole]) is False


# ── find_neighborhood ──────────────────────────────────────────────────────

def _minimal_geojson(name="Boa Viagem", rpa_code=5, micro_code=2):
    """GeoJSON mínimo com um bairro-retângulo ao redor do ponto -8.12, -34.90."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-35.0, -8.2],
                            [-34.8, -8.2],
                            [-34.8, -8.0],
                            [-35.0, -8.0],
                            [-35.0, -8.2],
                        ]
                    ],
                },
                "properties": {
                    "EBAIRRNOMEOF": name,
                    "CRPAAACODI": rpa_code,
                    "CMICROCODI": micro_code,
                },
            }
        ],
    }


def test_find_neighborhood_inside(monkeypatch):
    from services import geo_cross
    monkeypatch.setattr(geo_cross, "_bairros_geojson", _minimal_geojson())

    result = geo_cross.find_neighborhood(-8.12, -34.90)
    assert result["name"] == "Boa Viagem"
    assert "RPA 5" in result["rpa"]


def test_find_neighborhood_outside(monkeypatch):
    from services import geo_cross
    monkeypatch.setattr(geo_cross, "_bairros_geojson", _minimal_geojson())

    # Ponto claramente fora do Recife
    result = geo_cross.find_neighborhood(-10.0, -36.0)
    assert result == {}


# ── calculate_recurrence_score ─────────────────────────────────────────────

def test_recurrence_score_empty():
    from services.geo_cross import calculate_recurrence_score
    assert calculate_recurrence_score([]) == 0.0


def test_recurrence_score_increases_with_nearby_recent():
    from services.geo_cross import calculate_recurrence_score
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    recent = (now - timedelta(days=10)).isoformat()
    old = (now - timedelta(days=120)).isoformat()

    requests_recent = [
        {"distance_m": 50, "opened_at": recent, "related": True},
        {"distance_m": 100, "opened_at": recent, "related": True},
    ]
    requests_old = [
        {"distance_m": 50, "opened_at": old, "related": True},
    ]

    score_recent = calculate_recurrence_score(requests_recent)
    score_old = calculate_recurrence_score(requests_old)
    assert score_recent > score_old


def test_recurrence_score_capped():
    from services.geo_cross import calculate_recurrence_score
    from datetime import datetime, timezone, timedelta

    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    many = [{"distance_m": 10, "opened_at": recent, "related": True}] * 50
    assert calculate_recurrence_score(many) == pytest.approx(10.0)


# ── find_similar_official_requests ────────────────────────────────────────

def test_find_similar_filters_by_radius():
    """Chamados fora do raio não devem aparecer."""
    from services.geo_cross import find_similar_official_requests

    mock_client = MagicMock()
    # Retorna um chamado; a filtragem por raio Haversine deve excluí-lo
    mock_client.table.return_value.select.return_value.gte.return_value.lte.return_value.gte.return_value.lte.return_value.not_.is_.return_value.execute.return_value = MagicMock(
        data=[
            {
                "id": "abc",
                "source": "emlurb_156",
                "service_type": "tapa-buracos",
                "category": "buraco",
                "status": "Concluído",
                "neighborhood": "Boa Viagem",
                "lat": -8.1150,
                "lon": -34.9050,
                "opened_at": "2026-01-01T00:00:00",
            }
        ]
    )

    with patch("services.supabase_client.get_client", return_value=mock_client):
        # Ponto a > 10m do chamado mockado (que está bem longe) → excluído
        results = find_similar_official_requests(-8.120, -34.900, "buraco", radius_m=10)

    assert results == []


# ── cross_report_with_official_data ───────────────────────────────────────

@pytest.mark.asyncio
async def test_cross_report_missing_report():
    """Se report não existe, retorna None sem exceção."""
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
        data=None
    )

    with patch("services.supabase_client.get_client", return_value=mock_client):
        from services.geo_cross import cross_report_with_official_data
        result = await cross_report_with_official_data("nonexistent-id")

    assert result is None
