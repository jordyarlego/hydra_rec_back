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
    """Importadores precisam do SERVICE client pra bypassar RLS."""
    from services.supabase_client import get_service_client
    return get_service_client()


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
    """
    Baixa um CSV e retorna lista de dicts. Detecta automaticamente o delimiter
    (CKAN do Recife usa ';' em vários datasets).
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    # Tenta utf-8 primeiro, depois latin-1
    raw = resp.content
    try:
        content = raw.decode(encoding)
    except UnicodeDecodeError:
        content = raw.decode("latin-1", errors="replace")

    # Detecta delimiter na primeira linha (semicolon vs comma)
    first_line = content.split("\n", 1)[0]
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","

    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
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
    # DELETE+INSERT (schema atual sem unique constraint em lower(name))
    try:
        client.table("official_neighborhoods").delete().eq("source", "geojson_recife_2023").execute()
    except Exception as e:
        logger.warning(f"Neighborhoods pre-delete falhou: {e}")
    for i in range(0, len(rows), _BATCH_SIZE):
        batch = rows[i:i + _BATCH_SIZE]
        try:
            client.table("official_neighborhoods").insert(batch).execute()
            ok += len(batch)
        except Exception as e:
            logger.error(f"Neighborhood insert batch failed: {e}")
            err += len(batch)

    duration = time.time() - start
    _log_import("neighborhoods_geojson", ok, err, duration)
    return {"ok": ok, "err": err, "duration_s": round(duration, 2)}


def _col(row: dict, *candidates: str, default=None):
    """Busca coluna case-insensitive em qualquer um dos nomes candidatos."""
    for c in candidates:
        cl = c.lower()
        for k, v in row.items():
            if k and k.lower().strip() == cl:
                return v if (v not in (None, "")) else default
    return default


async def _pick_latest_resource_url(ckan_slug: str, fmt: str = "CSV") -> str | None:
    """
    Escolhe o resource mais recente do dataset (CKAN do Recife costuma ter
    1 arquivo por ano — pegamos o último criado).
    """
    url = f"{CKAN_PACKAGE_SHOW}?id={ckan_slug}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url)
            if not resp.is_success:
                return None
            data = resp.json()
            resources = data.get("result", {}).get("resources", [])
            matching = [r for r in resources if r.get("format", "").upper() == fmt.upper()]
            if not matching:
                matching = resources
            # Ordena por created desc
            matching.sort(key=lambda r: r.get("created", ""), reverse=True)
            return matching[0].get("url") if matching else None
    except Exception as e:
        logger.warning(f"resource discovery failed for {ckan_slug}: {e}")
        return None


# MVP: bairros do entorno central que vamos apresentar primeiro
MVP_BAIRROS = {
    "soledade", "santo amaro", "boa vista", "graças", "gracas",
    "madalena", "espinheiro", "ilha do leite", "paissandu",
    "torre", "derby", "casa forte", "encruzilhada",
    "rosarinho", "aflitos", "torreão", "torreao",
    "campo grande", "ponto de parada", "hipódromo", "hipodromo",
    "santo antônio", "santo antonio", "são josé", "sao jose",
    "recife", "ilha de joana bezerra",
}


def _filter_mvp(rows: list[dict], bairro_field: str = "neighborhood") -> list[dict]:
    """Mantém apenas bairros do MVP central. Reduz volume pra apresentação."""
    out = []
    for r in rows:
        nb = (r.get(bairro_field) or "").strip().lower()
        if nb in MVP_BAIRROS:
            out.append(r)
    return out


async def import_emlurb_156(mvp_only: bool = True) -> dict:
    """
    Importa chamados EMLURB 156 do Portal de Dados Abertos do Recife.
    Schema real (2026): GRUPOSERVICO_DESCRICAO, SERVICO_DESCRICAO,
                         LOGRADOURO, BAIRRO, RPA, DATA_DEMANDA, SITUACAO,
                         DATA_ULT_SITUACAO, latitude, longitude.
    Delimitador: ;
    Se mvp_only=True, filtra pra bairros centrais (Madalena, Graças, etc).
    """
    source_cfg = SOURCES["emlurb_156"]
    start = time.time()
    ok = err = 0
    first_error: Optional[str] = None

    resource_url = await _pick_latest_resource_url(source_cfg.ckan_slug, "CSV")
    if not resource_url:
        msg = f"Não foi possível descobrir CSV de {source_cfg.ckan_slug}"
        logger.warning(msg)
        _log_import("emlurb_156", 0, 0, time.time() - start, msg)
        return {"ok": 0, "err": 0, "error": msg}

    try:
        rows_raw = await _fetch_csv(resource_url, encoding="utf-8")
        logger.info(f"EMLURB CSV baixado: {len(rows_raw)} linhas")
    except Exception as e:
        _log_import("emlurb_156", 0, 1, time.time() - start, str(e))
        return {"ok": 0, "err": 1, "error": str(e)}

    rows = []
    for idx, raw in enumerate(rows_raw):
        try:
            grupo = _col(raw, "GRUPOSERVICO_DESCRICAO", "grupo_servico", "tipo_servico", default="")
            servico = _col(raw, "SERVICO_DESCRICAO", "servico_descricao", "servico", default="")
            categoria = f"{grupo} - {servico}".strip(" -")
            ext_id = _col(raw, "protocolo", "id_demanda", "id", "numero")
            if not ext_id:
                bairro = _col(raw, "BAIRRO", "bairro", default="")
                data = _col(raw, "DATA_DEMANDA", "data_demanda", default="")
                ext_id = f"emlurb-{idx}-{bairro}-{data}"[:120]

            rpa_raw = _col(raw, "RPA", "rpa")
            rows.append({
                "external_id":   str(ext_id)[:120],
                "source":        "emlurb_156",
                "agency":        "EMLURB",
                "service_type":  (servico or grupo)[:200] or None,
                "category":      _normalize_category(categoria, source_cfg.category_map),
                "status":        (_col(raw, "SITUACAO", "situacao", default="") or "")[:80] or None,
                "description":   categoria[:500] or None,
                "neighborhood":  (_col(raw, "BAIRRO", "bairro", default="") or "").strip() or None,
                "rpa":           f"RPA {str(rpa_raw).strip()}" if rpa_raw else None,
                "street_name":   (_col(raw, "LOGRADOURO", "logradouro", default="") or "").strip()[:200] or None,
                "lat":           _safe_float(_col(raw, "latitude", "lat")),
                "lon":           _safe_float(_col(raw, "longitude", "lon", "long")),
                "opened_at":     _safe_date(_col(raw, "DATA_DEMANDA", "data_demanda")),
                "closed_at":     _safe_date(_col(raw, "DATA_ULT_SITUACAO", "data_ult_situacao")),
                # raw minimalista pra não estourar tamanho
                "raw":           None,
            })
        except Exception as e:
            logger.debug(f"EMLURB row {idx} parse error: {e}")
            err += 1

    if mvp_only:
        before = len(rows)
        rows = _filter_mvp(rows, "neighborhood")
        logger.info(f"EMLURB MVP filter: {len(rows)} de {before} (bairros centrais)")

    logger.info(f"EMLURB normalizado: {len(rows)} válidas / {err} erros de parse")

    client = _get_client()
    # DELETE+INSERT: limpa source='emlurb_156' antes pra evitar erro 42P10
    # (schema atual não tem unique constraint em (source, external_id))
    try:
        client.table("official_service_requests").delete().eq("source", "emlurb_156").execute()
    except Exception as e:
        logger.warning(f"EMLURB pre-delete falhou: {e}")
    for i in range(0, len(rows), _BATCH_SIZE):
        batch = rows[i:i + _BATCH_SIZE]
        try:
            client.table("official_service_requests").insert(batch).execute()
            ok += len(batch)
        except Exception as e:
            err += len(batch)
            if not first_error:
                first_error = f"{type(e).__name__}: {str(e)[:400]}"
                logger.error(f"EMLURB insert FALHOU no batch {i}: {first_error}")
                logger.error(f"Sample row: {batch[0]}")

    duration = time.time() - start
    error_msg = first_error if err > 0 else None
    _log_import("emlurb_156", ok, err, duration, error_msg)
    return {"ok": ok, "err": err, "duration_s": round(duration, 2), "error": error_msg}


async def import_defesa_civil(mvp_only: bool = True) -> dict:
    """
    Importa atendimentos da Defesa Civil do Recife.
    Schema real: Regional, Data, Solicitacao, Endereco, Bairro,
                 Grau_de_Risco, Tipo_da_Acao. Sem lat/lon.
    Delimitador: ;
    Se mvp_only=True, mantém só bairros centrais.
    """
    source_cfg = SOURCES["defesa_civil"]
    start = time.time()
    ok = err = 0
    first_error: Optional[str] = None

    resource_url = await _pick_latest_resource_url(source_cfg.ckan_slug, "CSV")
    if not resource_url:
        msg = "defesa_civil: CSV não encontrado"
        logger.warning(msg)
        _log_import("defesa_civil", 0, 0, time.time() - start, msg)
        return {"ok": 0, "err": 0, "error": msg}

    try:
        rows_raw = await _fetch_csv(resource_url, encoding="utf-8")
        logger.info(f"Defesa Civil CSV: {len(rows_raw)} linhas")
    except Exception as e:
        _log_import("defesa_civil", 0, 1, time.time() - start, str(e))
        return {"ok": 0, "err": 1, "error": str(e)}

    rows = []
    for idx, raw in enumerate(rows_raw):
        try:
            solicitacao = _col(raw, "Solicitacao", "solicitacao", "Tipo_da_Acao", default="")
            ocorrencia = _col(raw, "Ocorrencia", "ocorrencia", default="")
            descricao = ocorrencia or solicitacao
            bairro = (_col(raw, "Bairro", "bairro", default="") or "").strip()
            data = _col(raw, "Data", "data", "Data_da_Acao", default="")

            ext_id = f"dc-{idx}-{bairro}-{data}"[:120]

            rows.append({
                "external_id":  ext_id,
                "source":       "defesa_civil",
                "agency":       "Defesa Civil do Recife",
                "service_type": (solicitacao or "")[:200] or None,
                "category":     _normalize_category(descricao, source_cfg.category_map),
                "status":       (_col(raw, "Tipo_da_Acao", "tipo_acao", default="") or "")[:80] or None,
                "description":  (descricao or "Atendimento Defesa Civil")[:500],
                "neighborhood": bairro or None,
                "rpa":          None,
                "street_name":  (_col(raw, "Endereco", "endereco", default="") or "").strip()[:200] or None,
                "lat":          None,
                "lon":          None,
                "opened_at":    _safe_date(data),
                "raw":          None,
            })
        except Exception as e:
            logger.debug(f"Defesa Civil row {idx} error: {e}")
            err += 1

    if mvp_only:
        before = len(rows)
        rows = _filter_mvp(rows, "neighborhood")
        logger.info(f"Defesa Civil MVP filter: {len(rows)} de {before}")

    logger.info(f"Defesa Civil normalizado: {len(rows)} válidas / {err} erros de parse")

    client = _get_client()
    try:
        client.table("official_service_requests").delete().eq("source", "defesa_civil").execute()
    except Exception as e:
        logger.warning(f"Defesa Civil pre-delete falhou: {e}")
    for i in range(0, len(rows), _BATCH_SIZE):
        batch = rows[i:i + _BATCH_SIZE]
        try:
            client.table("official_service_requests").insert(batch).execute()
            ok += len(batch)
        except Exception as e:
            err += len(batch)
            if not first_error:
                first_error = f"{type(e).__name__}: {str(e)[:400]}"
                logger.error(f"Defesa Civil insert FALHOU no batch {i}: {first_error}")
                logger.error(f"Sample row: {batch[0]}")

    duration = time.time() - start
    error_msg = first_error if err > 0 else None
    _log_import("defesa_civil", ok, err, duration, error_msg)
    return {"ok": ok, "err": err, "duration_s": round(duration, 2), "error": error_msg}


async def import_public_lighting_posts() -> dict:
    """Importa catálogo de postes de iluminação pública (EMLURB)."""
    source_cfg = SOURCES["postes_iluminacao"]
    start = time.time()
    ok = err = 0

    resource_url = await _pick_latest_resource_url(source_cfg.ckan_slug, "CSV")
    if not resource_url:
        msg = f"postes_iluminacao: CSV não encontrado"
        logger.warning(msg)
        _log_import("postes_iluminacao", 0, 0, time.time() - start, msg)
        return {"ok": 0, "err": 0, "error": msg}

    try:
        rows_raw = await _fetch_csv(resource_url, encoding="utf-8")
        logger.info(f"Postes CSV: {len(rows_raw)} linhas")
    except Exception as e:
        _log_import("postes_iluminacao", 0, 1, time.time() - start, str(e))
        return {"ok": 0, "err": 1, "error": str(e)}

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




async def import_all(sources: list[str] | None = None) -> dict:
    """
    Roda os importadores essenciais. Postes opcional — pode ser muito grande
    pro MVP. Default: bairros + emlurb 156 + defesa civil, todos com filtro
    MVP (bairros centrais).
    """
    all_sources = {
        "neighborhoods": import_neighborhoods,
        "emlurb_156":    import_emlurb_156,
        "defesa_civil":  import_defesa_civil,
        # "postes":      import_public_lighting_posts,  # 200k+ rows, opt-in
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


# ════════════════════════════════════════════════════════════════════
# SEED MVP — import de dataset estatico pre-curado.
# Usar quando Portal de Dados Abertos esta off-line ou retornando schema
# incompativel. ~120 chamados representativos por bairros centrais
# (suficiente pra demonstrar cruzamento, recurrence_score, kanban).
# ════════════════════════════════════════════════════════════════════
async def import_from_seed() -> dict:
    """Importa dataset estatico (data/seed/official_sample.json)."""
    import json
    import os as _os

    start = time.time()
    ok = err = 0
    first_error: Optional[str] = None

    seed_path = _os.path.join(
        _os.path.dirname(_os.path.abspath(__file__)), "..", "data", "seed", "official_sample.json"
    )
    try:
        with open(seed_path, "r", encoding="utf-8") as f:
            seed = json.load(f)
    except Exception as e:
        _log_import("seed", 0, 1, time.time() - start, f"seed read error: {e}")
        return {"ok": 0, "err": 1, "error": str(e)}

    client = _get_client()

    # 1. Bairros oficiais — schema antigo não tem lat/lon (vem do GeoJSON estatico).
    #    Insere bairro a bairro pra tolerar colunas faltando entre schemas.
    nb_rows = []
    for nb in seed.get("neighborhoods", []):
        nb_rows.append({
            "name": nb["name"],
            "rpa": nb["rpa"],
            "source": "seed_mvp",
        })
    if nb_rows:
        try:
            names = [r["name"] for r in nb_rows]
            client.table("official_neighborhoods").delete().in_("name", names).execute()
        except Exception as e:
            logger.warning(f"seed neighborhoods delete failed: {e}")
        nb_ok = 0
        for nb in nb_rows:
            try:
                client.table("official_neighborhoods").insert(nb).execute()
                nb_ok += 1
            except Exception as e:
                logger.debug(f"neighborhoods insert {nb.get('name')}: {e}")
        logger.info(f"seed: {nb_ok}/{len(nb_rows)} bairros inseridos")

    # 2. Chamados oficiais — limpa source='seed_mvp' antes de inserir
    try:
        client.table("official_service_requests").delete().eq("source", "seed_mvp").execute()
    except Exception as e:
        logger.warning(f"seed pre-delete falhou: {e}")

    nb_index = {nb["name"]: nb for nb in seed.get("neighborhoods", [])}
    rows = []
    for idx, sr in enumerate(seed.get("service_requests", [])):
        nb = nb_index.get(sr.get("neighborhood"), {})
        ext = f"seed-{sr.get('category','outro')}-{idx:04d}"
        rows.append({
            "external_id":   ext[:120],
            "source":        "seed_mvp",
            "agency":        "EMLURB" if sr.get("category") not in ("deslizamento",) else "DEFESA_CIVIL",
            "service_type":  sr.get("service_type") or sr.get("category"),
            "category":      sr.get("category"),
            "status":        sr.get("status"),
            "description":   f"{sr.get('service_type')} - {sr.get('neighborhood')}",
            "neighborhood":  sr.get("neighborhood"),
            "rpa":           nb.get("rpa"),
            "street_name":   sr.get("street"),
            "lat":           nb.get("lat"),
            "lon":           nb.get("lon"),
            "opened_at":     _safe_date(sr.get("opened_at")),
            "closed_at":     _safe_date(sr.get("closed_at")),
            "raw":           None,
        })

    # INSERT em lotes (sem on_conflict)
    for i in range(0, len(rows), _BATCH_SIZE):
        batch = rows[i:i + _BATCH_SIZE]
        try:
            client.table("official_service_requests").insert(batch).execute()
            ok += len(batch)
        except Exception as e:
            err += len(batch)
            if not first_error:
                first_error = f"{type(e).__name__}: {str(e)[:400]}"
                logger.error(f"seed insert FALHOU no batch {i}: {first_error}")
                logger.error(f"Sample row: {batch[0]}")

    duration = time.time() - start
    error_msg = first_error if err > 0 else None
    _log_import("seed", ok, err, duration, error_msg)
    return {
        "ok": ok, "err": err,
        "duration_s": round(duration, 2),
        "error": error_msg,
        "source": "seed_mvp",
        "description": "Amostra pre-curada (~120 registros) — independe do Portal de Dados Abertos.",
    }
