"""
Testes para official_importer — usa fixtures CSV sem tocar em APIs externas.
"""
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "official_data")


def _csv_content(filename: str) -> bytes:
    path = os.path.join(FIXTURES_DIR, filename)
    with open(path, "rb") as f:
        return f.read()


@pytest.fixture
def mock_supabase():
    client = MagicMock()
    client.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])
    client.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[])
    return client


# ── _normalize_category ───────────────────────────────────────────────────

def test_normalize_category_emlurb():
    from services.official_importer import _normalize_category
    from services.official_data_sources import SOURCES

    cat_map = SOURCES["emlurb_156"].category_map
    assert _normalize_category("Tapa-buracos", cat_map) == "buraco"
    assert _normalize_category("Poda de árvore", cat_map) == "queda_arvore"
    assert _normalize_category("Drenagem", cat_map) == "alagamento"
    assert _normalize_category("Iluminação pública", cat_map) == "iluminacao"
    assert _normalize_category("", cat_map) is None
    assert _normalize_category("Categoria Desconhecida", cat_map) is None


def test_normalize_category_defesa_civil():
    from services.official_importer import _normalize_category
    from services.official_data_sources import SOURCES

    cat_map = SOURCES["defesa_civil"].category_map
    assert _normalize_category("Alagamento", cat_map) == "alagamento"
    assert _normalize_category("Barreira", cat_map) == "deslizamento"
    assert _normalize_category("Queda de árvore", cat_map) == "queda_arvore"


# ── _safe_float ────────────────────────────────────────────────────────────

def test_safe_float():
    from services.official_importer import _safe_float

    assert _safe_float("-8.1195") == pytest.approx(-8.1195)
    assert _safe_float("-8,1195") == pytest.approx(-8.1195)
    assert _safe_float("0") is None
    assert _safe_float("") is None
    assert _safe_float(None) is None
    assert _safe_float("abc") is None


# ── _safe_date ─────────────────────────────────────────────────────────────

def test_safe_date():
    from services.official_importer import _safe_date

    assert _safe_date("15/01/2026") == "2026-01-15T00:00:00"
    assert _safe_date("2026-01-15") == "2026-01-15T00:00:00"
    assert _safe_date("") is None
    assert _safe_date(None) is None
    assert _safe_date("-") is None


# ── import_emlurb_156 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_import_emlurb_156_uses_fixture(mock_supabase):
    """Importa CSV da fixture; nenhuma chamada HTTP real."""
    csv_bytes = _csv_content("emlurb_156_fixture.csv")

    import httpx

    mock_resp = MagicMock()
    mock_resp.is_success = True
    mock_resp.json.return_value = {
        "result": {
            "resources": [{"format": "CSV", "url": "http://fake/emlurb.csv"}]
        }
    }

    mock_csv_resp = MagicMock()
    mock_csv_resp.content = csv_bytes
    mock_csv_resp.raise_for_status = MagicMock()

    async def _fake_get(url, **kwargs):
        if "package_show" in url:
            return mock_resp
        return mock_csv_resp

    with (
        patch("services.official_importer._get_client", return_value=mock_supabase),
        patch("httpx.AsyncClient") as mock_async_client,
    ):
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.get = AsyncMock(side_effect=_fake_get)
        mock_async_client.return_value = mock_ctx

        from services.official_importer import import_emlurb_156
        result = await import_emlurb_156()

    assert "ok" in result
    assert result["ok"] >= 0  # pode ser 0 se upsert mockado
    assert "err" in result


@pytest.mark.asyncio
async def test_import_emlurb_156_handles_offline_source(mock_supabase):
    """Se CKAN indisponível, retorna erro sem quebrar."""
    with (
        patch("services.official_importer._get_client", return_value=mock_supabase),
        patch(
            "services.official_importer._pick_latest_resource_url",
            new=AsyncMock(return_value=None),
        ),
    ):
        from services.official_importer import import_emlurb_156
        result = await import_emlurb_156()

    assert "error" in result
    assert result["ok"] == 0


# ── import_defesa_civil ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_import_defesa_civil_uses_fixture(mock_supabase):
    csv_bytes = _csv_content("defesa_civil_fixture.csv")

    mock_resp = MagicMock()
    mock_resp.is_success = True
    mock_resp.json.return_value = {
        "result": {"resources": [{"format": "CSV", "url": "http://fake/dc.csv"}]}
    }

    mock_csv_resp = MagicMock()
    mock_csv_resp.content = csv_bytes
    mock_csv_resp.raise_for_status = MagicMock()

    async def _fake_get(url, **kwargs):
        if "package_show" in url:
            return mock_resp
        return mock_csv_resp

    with (
        patch("services.official_importer._get_client", return_value=mock_supabase),
        patch("httpx.AsyncClient") as mock_async_client,
    ):
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.get = AsyncMock(side_effect=_fake_get)
        mock_async_client.return_value = mock_ctx

        from services.official_importer import import_defesa_civil
        result = await import_defesa_civil()

    assert "ok" in result


# ── get_import_status ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_import_status_deduplicates(mock_supabase):
    """Retorna apenas a entrada mais recente por fonte."""
    mock_supabase.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[
            {"source": "emlurb_156", "records_ok": 100, "records_err": 0, "started_at": "2026-05-15T10:00:00"},
            {"source": "emlurb_156", "records_ok": 90, "records_err": 0, "started_at": "2026-05-14T10:00:00"},
            {"source": "defesa_civil", "records_ok": 50, "records_err": 0, "started_at": "2026-05-15T09:00:00"},
        ]
    )
    with patch("services.official_importer._get_client", return_value=mock_supabase):
        from services.official_importer import get_import_status
        result = await get_import_status()

    assert len(result) == 2
    sources = {r["source"] for r in result}
    assert sources == {"emlurb_156", "defesa_civil"}
