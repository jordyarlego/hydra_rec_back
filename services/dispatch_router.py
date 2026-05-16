"""
Dispatch router — sugere órgão destino, monta título auto e detecta duplicatas.

Usado por:
  • POST /api/admin/reports/{id}/ticket          (preenche org+título)
  • POST /api/admin/reports/batch-approve        (preenche em lote)
  • GET  /api/admin/reports/{id}/duplicates      (lista candidatos)
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ───── Órgãos destino ──────────────────────────────────────────────
ORG_BY_TYPE: dict[str, str] = {
    "alagamento":        "EMLURB_DRENAGEM",
    "queda_arvore":      "EMLURB_ARBORIZACAO",
    "poste_caido":       "CELPE",
    "iluminacao":        "CELPE",
    "buraco":            "EMLURB_PAVIMENTACAO",
    "lixo":              "EMLURB_LIMPEZA",
    "deslizamento":      "DEFESA_CIVIL",
    "via_intransitavel": "EMLURB_PAVIMENTACAO",
    "outro":             "OUTRO",
}

ORG_LABELS: dict[str, str] = {
    "EMLURB_DRENAGEM":     "EMLURB · Drenagem",
    "EMLURB_ARBORIZACAO":  "EMLURB · Arborização",
    "EMLURB_PAVIMENTACAO": "EMLURB · Pavimentação",
    "EMLURB_LIMPEZA":      "EMLURB · Limpeza Urbana",
    "CELPE":               "Celpe / Neoenergia",
    "DEFESA_CIVIL":        "Defesa Civil",
    "OUTRO":               "A definir",
}

CATEGORY_LABEL: dict[str, str] = {
    "alagamento":        "Alagamento",
    "deslizamento":      "Deslizamento",
    "queda_arvore":      "Queda de árvore",
    "via_intransitavel": "Via fechada",
    "poste_caido":       "Poste caído",
    "buraco":            "Buraco na via",
    "lixo":              "Acúmulo de lixo",
    "iluminacao":        "Iluminação pública",
    "outro":             "Ocorrência diversa",
}


# ───── SLA por prioridade (segundos) ───────────────────────────────
SLA_SECONDS: dict[str, int] = {
    "urgente": 2 * 3600,
    "alta":    24 * 3600,
    "media":   72 * 3600,
    "baixa":   7 * 24 * 3600,
}


def suggest_org(report_type: str) -> str:
    """Retorna a chave do órgão destino sugerido a partir do tipo do report."""
    return ORG_BY_TYPE.get(report_type or "outro", "OUTRO")


def list_orgs() -> list[dict[str, str]]:
    """Lista de orgãos pra dropdown do form admin."""
    return [{"key": k, "label": v} for k, v in ORG_LABELS.items()]


def auto_title(report: dict[str, Any], geo: dict[str, Any] | None = None) -> str:
    """
    Monta título do chamado: "{Tipo} em {via}, {bairro} (RPA {n})".
    Cai pra "{Tipo} em {bairro}" quando não tem via.
    """
    geo = geo or {}
    tipo = (report.get("type") or report.get("tipo") or "outro").lower()
    tipo_label = CATEGORY_LABEL.get(tipo, "Ocorrência")

    via = geo.get("nearest_road_name") or report.get("via_proxima")
    bairro = report.get("bairro") or geo.get("neighborhood") or "Recife"
    rpa = geo.get("rpa")

    if via:
        title = f"{tipo_label} em {via}, {bairro}"
    else:
        title = f"{tipo_label} em {bairro}"

    if rpa:
        title += f" (RPA {rpa})"
    return title


def sla_deadline(priority: str | None, base: datetime | None = None) -> datetime:
    """Calcula deadline SLA pra ticket recém-criado."""
    base = base or datetime.now(timezone.utc)
    seconds = SLA_SECONDS.get((priority or "media").lower(), SLA_SECONDS["media"])
    return base + timedelta(seconds=seconds)


# ───── Detecção de duplicatas ───────────────────────────────────────
DUPLICATE_RADIUS_M = 100
DUPLICATE_WINDOW_HOURS = 24


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância em metros entre dois pontos GPS."""
    R = 6_371_000
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


async def find_duplicates(report: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Busca reports da mesma categoria, raio 100m, últimas 24h.

    Retorna lista ordenada por proximidade. Cada item:
      {id, type, bairro, created_at, distance_m, ticket_id|None}
    """
    rid = report.get("id")
    tipo = report.get("type") or report.get("tipo")
    lat = report.get("lat")
    lon = report.get("lon")
    if not (rid and tipo and lat is not None and lon is not None):
        return []

    from services.supabase_client import get_service_client
    client = get_service_client()

    since = (datetime.now(timezone.utc) - timedelta(hours=DUPLICATE_WINDOW_HOURS)).isoformat()

    try:
        res = (
            client.table("reports")
            .select("id,type,bairro,created_at,lat,lon,ticket_id")
            .eq("type", tipo)
            .gte("created_at", since)
            .neq("id", rid)
            .limit(50)
            .execute()
        )
    except Exception as e:
        logger.warning("find_duplicates query failed: %s", e)
        return []

    candidates = []
    for row in (res.data or []):
        rl = row.get("lat")
        rn = row.get("lon")
        if rl is None or rn is None:
            continue
        d = _haversine_m(float(lat), float(lon), float(rl), float(rn))
        if d <= DUPLICATE_RADIUS_M:
            candidates.append({
                "id":          row["id"],
                "type":        row.get("type"),
                "bairro":      row.get("bairro"),
                "created_at":  row.get("created_at"),
                "distance_m":  round(d),
                "ticket_id":   row.get("ticket_id"),
            })

    candidates.sort(key=lambda c: c["distance_m"])
    return candidates
