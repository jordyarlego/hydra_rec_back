"""
Importa, normaliza e persiste dados oficiais do Recife no Supabase.
Cada função de importação é idempotente (upsert por external_id).
Não quebra se uma fonte estiver offline — registra erro e continua.
"""
import asyncio
import csv
import io
import logging
import os
import time
from typing import Optional

import httpx

from services.official_data_sources import CKAN_PACKAGE_SHOW, SOURCES, SourceConfig

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0)
_BATCH_SIZE = 200  # linhas por upsert no Supabase


# ── Helpers ───────────────────────────────────────────────────────────────

def _get_client():
    from services.supabase_client import get_client
    return get_client()


def _normalize_category(raw_cat: str, category_map: dict) -> Optional[str]:
    if not raw_cat:
        return None
    low = raw_cat.lower().strip()
    for key, val in category_map.items():
        if key in low:
            return val
    return None


def _safe_float(value) -> Optional[float]:
    try:
        v = float(str(value).replace(",", ".").strip())
        return v if v != 0 else None
    except (TypeError, ValueError):
        return None


def _safe_date(value) -> Optional[str]:
    if not value:
        return None
    s = str(value).strip()
    if not s or s in ("-", "N/A", "null"):
        return None
    # Formatos comuns: DD/MM/YYYY, YYYY-MM-DD, YYYY-MM-DDTHH:MM:SS
    _DATE_LENGTHS = {"%d/%m/%Y": 10, "%Y-%m-%d": 10, "%d/%m/%Y %H:%M:%S": 19}
    for fmt, expected_len in _DATE_LENGTHS.items():
        try:
            from datetime import datetime
            return datetime.strptime(s[:expected_len], fmt).isoformat()
        except ValueError:
            continue
    return None


async def _discover_resource_url(ckan_slug: str, fmt: str) -> Optional[str]:
    """Descobre a URL de download de um dataset no CKAN do Recife."""
    url = f"{CKAN_PACKAGE_SHOW}?id={ckan_slug}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url)
            if not resp.is_success:
                return None
            data = resp.json()
            resources = data.get("result", {}).get("resources", [])
            for res in resources:
                if res.get("format", "").lower() == fmt.lower():
                    return res.get("url")
            # Fallback: pegar o primeiro recurso
            return resources[0].get("url") if resources else None
    except Exception as e:
        logger.warning(f"CKAN discovery failed for {ckan_slug}: {e}")
        return None


async def _fetch_csv(url: str, encoding: str = "utf-8") -> list[dict]:
    """Baixa um CSV e retorna lista de dicts."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    content = resp.content.decode(encoding, errors="replace")
    reader = csv.DictReader(io.StringIO(content))
    return [dict(row) for row in reader]


def _log_import(source: str, ok: int, err: int, duration: float, error: str = None):
    try:
        _get_client().table("official_import_log").insert({
            "source": source,
            "records_ok": ok,
            "records_err": err,
            "duration_s": round(duration, 2),
            "error": error,
        }).execute()
    except Exception as e:
        logger.debug(f"Import log write failed: {e}")


# ── Importadores por fonte ────────────────────────────────────────────────

async def import_neighborhoods() -> dict:
    """
    Popula official_neighborhoods a partir do GeoJSON local.
    Não depende do portal externo — usa recife_bairros_2023.geojson.
    """
    import json
    start = time.time()
    ok = err = 0
    geojson_path = os.path.normpath(os.path.join(
        os.path.dirname(__file__),
        "../../front_end_hydrarec/src/data/geo/recife_bairros_2023.geojson"
    ))

    RPA_NAMES = {
        1: "RPA 1 — Centro",
        2: "RPA 2 — Norte",
        3: "RPA 3 — Noroeste",
        4: "RPA 4 — Oeste",
        5: "RPA 5 — Sul",
        6: "RPA 6 — Sudoeste",
    }
    MICRO_NAMES = {1: "Macrozona 1", 2: "Macrozona 2", 3: "Macrozona 3"}

    try:
        with open(geojson_path, encoding="utf-8") as f:
            geojson = json.load(f)
    except FileNotFoundError:
        return {"ok": 0, "err": 1, "error": f"GeoJSON not found at {geojson_path}"}

    rows = []
    for feat in geojson.get("features", []):
        props = feat.get("properties", {})
        name = (props.get("EBAIRRNOMEOF") or props.get("EBAIRRNOME") or "").strip()
        if not name:
            continue
        rpa_code = props.get("CRPAAACODI")
        micro_code = props.get("CMICROCODI")
        rows.append({
            "name": name,
            "rpa": RPA_NAMES.get(rpa_code, f"RPA {rpa_code}"),
            "rpa_code": rpa_code,
            "microregion": MICRO_NAMES.get(micro_code, f"Macrozona {micro_code}"),
            "microregion_code": micro_code,
            "source": "geojson_recife_2023",
            "raw": {"objectid": props.get("OBJECTID"), "cbairrcodi": props.get("CBAIRRCODI")},
        })

    client = _get_client()
    for i in range(0, len(rows), _BATCH_SIZE):
        batch = rows[i:i + _BATCH_SIZE]
        try:
            client.table("official_neighborhoods").upsert(
                batch, on_conflict="lower(name)"
            ).execute()
            ok += len(batch)
        except Exception as e:
            logger.error(f"Neighborhood upsert batch failed: {e}")
            err += len(batch)

    duration = time.time() - start
    _log_import("neighborhoods_geojson", ok, err, duration)
    return {"ok": ok, "err": err, "duration_s": round(duration, 2)}


async def import_emlurb_156() -> dict:
    """Importa chamados EMLURB 156 do Portal de Dados Abertos do Recife."""
    source_cfg = SOURCES["emlurb_156"]
    start = time.time()
    ok = err = 0

    resource_url = await _discover_resource_url(source_cfg.ckan_slug, source_cfg.format)
    if not resource_url:
        msg = f"Não foi possível descobrir a URL do dataset {source_cfg.ckan_slug}"
        logger.warning(msg)
        _log_import("emlurb_156", 0, 0, time.time() - start, msg)
        return {"ok": 0, "err": 0, "error": msg}

    try:
        rows_raw = await _fetch_csv(resource_url, source_cfg.encoding)
    except Exception as e:
        _log_import("emlurb_156", 0, 1, time.time() - start, str(e))
        return {"ok": 0, "err": 1, "error": str(e)}

    # Normalização: tenta múltiplos nomes de coluna (o portal às vezes muda)
    def _col(row: dict, *candidates: str, default=None):
        for c in candidates:
            for k, v in row.items():
                if k.lower().strip() == c.lower():
                    return v or default
        return default

    rows = []
    for raw in rows_raw:
        try:
            ext_id = _col(raw, "protocolo", "id", "numero")
            service_type = _col(raw, "tipo_servico", "tipo", "servico", default="")
            rows.append({
                "external_id": str(ext_id) if ext_id else None,
                "source": "emlurb_156",
                "agency": "EMLURB",
                "service_type": service_type,
                "category": _normalize_category(service_type, source_cfg.category_map),
                "status": _col(raw, "status", "situacao", "estado", default=""),
                "description": _col(raw, "descricao", "observacao", "assunto", default=""),
                "neighborhood": _col(raw, "bairro", "bairro_nome", default=""),
                "street_name": _col(raw, "logradouro", "endereco", "rua", default=""),
                "lat": _safe_float(_col(raw, "lat", "latitude", "y")),
                "lon": _safe_float(_col(raw, "lon", "long", "longitude", "x")),
                "opened_at": _safe_date(_col(raw, "data_abertura", "data_solicitacao", "data")),
                "closed_at": _safe_date(_col(raw, "data_fechamento", "data_encerramento")),
                "raw": {k: v for k, v in raw.items() if v not in (None, "", "null")},
            })
        except Exception as e:
            logger.debug(f"EMLURB row parse error: {e}")
            err += 1

    client = _get_client()
    for i in range(0, len(rows), _BATCH_SIZE):
        batch = rows[i:i + _BATCH_SIZE]
        try:
            client.table("official_service_requests").upsert(
                batch, on_conflict="source,external_id"
            ).execute()
            ok += len(batch)
        except Exception as e:
            logger.error(f"EMLURB upsert batch failed: {e}")
            err += len(batch)

    duration = time.time() - start
    _log_import("emlurb_156", ok, err, duration)
    return {"ok": ok, "err": err, "duration_s": round(duration, 2)}


async def import_defesa_civil() -> dict:
    """Importa registros de atendimento da Defesa Civil do Recife."""
    source_cfg = SOURCES["defesa_civil"]
    start = time.time()
    ok = err = 0

    resource_url = await _discover_resource_url(source_cfg.ckan_slug, source_cfg.format)
    if not resource_url:
        msg = f"Fonte defesa_civil indisponível: {source_cfg.ckan_slug}"
        logger.warning(msg)
        _log_import("defesa_civil", 0, 0, time.time() - start, msg)
        return {"ok": 0, "err": 0, "error": msg}

    try:
        rows_raw = await _fetch_csv(resource_url, source_cfg.encoding)
    except Exception as e:
        _log_import("defesa_civil", 0, 1, time.time() - start, str(e))
        return {"ok": 0, "err": 1, "error": str(e)}

    def _col(row, *keys, default=None):
        for k in keys:
            for rk, rv in row.items():
                if rk.lower().strip() == k.lower():
                    return rv or default
        return default

    rows = []
    for raw in rows_raw:
        try:
            stype = _col(raw, "tipo_atendimento", "tipo", "natureza", default="")
            rows.append({
                "external_id": _col(raw, "protocolo", "id", "numero"),
                "source": "defesa_civil",
                "agency": "Defesa Civil do Recife",
                "service_type": stype,
                "category": _normalize_category(stype, source_cfg.category_map),
                "status": _col(raw, "status", "situacao", default=""),
                "description": _col(raw, "descricao", "obs", "observacao", default=""),
                "neighborhood": _col(raw, "bairro", default=""),
                "street_name": _col(raw, "logradouro", "endereco", default=""),
                "lat": _safe_float(_col(raw, "lat", "latitude")),
                "lon": _safe_float(_col(raw, "lon", "longitude")),
                "opened_at": _safe_date(_col(raw, "data", "data_ocorrencia", "data_abertura")),
                "raw": {k: v for k, v in raw.items() if v not in (None, "", "null")},
            })
        except Exception as e:
            logger.debug(f"Defesa Civil row error: {e}")
            err += 1

    client = _get_client()
    for i in range(0, len(rows), _BATCH_SIZE):
        batch = rows[i:i + _BATCH_SIZE]
        try:
            client.table("official_service_requests").upsert(
                batch, on_conflict="source,external_id"
            ).execute()
            ok += len(batch)
        except Exception as e:
            logger.error(f"Defesa Civil upsert failed: {e}")
            err += len(batch)

    duration = time.time() - start
    _log_import("defesa_civil", ok, err, duration)
    return {"ok": ok, "err": err, "duration_s": round(duration, 2)}


async def import_public_lighting_posts() -> dict:
    """Importa catálogo de postes de iluminação pública."""
    source_cfg = SOURCES["postes_iluminacao"]
    start = time.time()
    ok = err = 0

    resource_url = await _discover_resource_url(source_cfg.ckan_slug, source_cfg.format)
    if not resource_url:
        msg = f"Fonte postes_iluminacao indisponível"
        logger.warning(msg)
        _log_import("postes_iluminacao", 0, 0, time.time() - start, msg)
        return {"ok": 0, "err": 0, "error": msg}

    try:
        rows_raw = await _fetch_csv(resource_url, source_cfg.encoding)
    except Exception as e:
        _log_import("postes_iluminacao", 0, 1, time.time() - start, str(e))
        return {"ok": 0, "err": 1, "error": str(e)}

    def _col(row, *keys, default=None):
        for k in keys:
            for rk, rv in row.items():
                if rk.lower().strip() == k.lower():
                    return rv or default
        return default

    rows = []
    for raw in rows_raw:
        try:
            rows.append({
                "external_id": _col(raw, "id", "cod_poste", "codigo"),
                "asset_type": "poste_iluminacao",
                "name": _col(raw, "tipo_poste", "tipo", default="Poste"),
                "neighborhood": _col(raw, "bairro", default=""),
                "street_name": _col(raw, "logradouro", "endereco", default=""),
                "lat": _safe_float(_col(raw, "lat", "latitude", "y")),
                "lon": _safe_float(_col(raw, "lon", "longitude", "x")),
                "source": "postes_iluminacao",
                "raw": {k: v for k, v in raw.items() if v not in (None, "", "null")},
            })
        except Exception as e:
            logger.debug(f"Poste row error: {e}")
            err += 1

    client = _get_client()
    for i in range(0, len(rows), _BATCH_SIZE):
        batch = rows[i:i + _BATCH_SIZE]
        try:
            client.table("official_assets").insert(batch).execute()
            ok += len(batch)
        except Exception as e:
            logger.error(f"Postes upsert failed: {e}")
            err += len(batch)

    duration = time.time() - start
    _log_import("postes_iluminacao", ok, err, duration)
    return {"ok": ok, "err": err, "duration_s": round(duration, 2)}


async def import_roads() -> dict:
    """Importa trechos de logradouros por bairro."""
    source_cfg = SOURCES["logradouros"]
    start = time.time()
    ok = err = 0

    resource_url = await _discover_resource_url(source_cfg.ckan_slug, source_cfg.format)
    if not resource_url:
        msg = "Fonte logradouros indisponível"
        logger.warning(msg)
        _log_import("logradouros", 0, 0, time.time() - start, msg)
        return {"ok": 0, "err": 0, "error": msg}

    try:
        rows_raw = await _fetch_csv(resource_url, source_cfg.encoding)
    except Exception as e:
        _log_import("logradouros", 0, 1, time.time() - start, str(e))
        return {"ok": 0, "err": 1, "error": str(e)}

    def _col(row, *keys, default=None):
        for k in keys:
            for rk, rv in row.items():
                if rk.lower().strip() == k.lower():
                    return rv or default
        return default

    rows = []
    for raw in rows_raw:
        try:
            rows.append({
                "name": _col(raw, "nome_logradouro", "logradouro", "nome", default=""),
                "neighborhood": _col(raw, "bairro", "nome_bairro", default=""),
                "rpa": _col(raw, "rpa", "regiao", default=""),
                "pavement_type": _col(raw, "tipo_pavimento", "pavimento", default=""),
                "source": "logradouros_recife",
                "raw": {k: v for k, v in raw.items() if v not in (None, "", "null")},
            })
        except Exception as e:
            logger.debug(f"Road row error: {e}")
            err += 1

    client = _get_client()
    for i in range(0, len(rows), _BATCH_SIZE):
        batch = rows[i:i + _BATCH_SIZE]
        try:
            client.table("official_roads").insert(batch).execute()
            ok += len(batch)
        except Exception as e:
            logger.error(f"Roads upsert failed: {e}")
            err += len(batch)

    duration = time.time() - start
    _log_import("logradouros", ok, err, duration)
    return {"ok": ok, "err": err, "duration_s": round(duration, 2)}


async def import_all(sources: list[str] | None = None) -> dict:
    """
    Roda todos os importadores (ou lista especificada).
    Cada falha é registrada individualmente sem parar os demais.
    """
    all_sources = {
        "neighborhoods": import_neighborhoods,
        "emlurb_156":    import_emlurb_156,
        "defesa_civil":  import_defesa_civil,
        "postes":        import_public_lighting_posts,
        "roads":         import_roads,
    }
    to_run = {k: v for k, v in all_sources.items() if sources is None or k in sources}

    results = {}
    tasks = {k: asyncio.create_task(fn()) for k, fn in to_run.items()}
    for key, task in tasks.items():
        try:
            results[key] = await task
        except Exception as e:
            results[key] = {"ok": 0, "err": 1, "error": str(e)}
            logger.error(f"import_all failed for {key}: {e}")

    return results


async def get_import_status() -> list[dict]:
    """Retorna o log das últimas importações (uma por fonte)."""
    try:
        res = _get_client().table("official_import_log").select(
            "source, records_ok, records_err, duration_s, error, started_at"
        ).order("started_at", desc=True).limit(50).execute()
        rows = res.data or []
        # Deduplica: mantém apenas a mais recente por source
        seen = {}
        for row in rows:
            src = row["source"]
            if src not in seen:
                seen[src] = row
        return list(seen.values())
    except Exception as e:
        logger.error(f"get_import_status error: {e}")
        return []
