"""
Centraliza URLs e metadados das fontes de dados oficiais do Recife.
Cada SourceConfig descreve um dataset do Portal de Dados Abertos.
Não espalhar URLs pelo código — alterar aqui afeta todos os importers.
"""
from dataclasses import dataclass, field
from typing import Optional

CKAN_BASE = "http://dados.recife.pe.gov.br/api/3/action"
CKAN_PACKAGE_SHOW = f"{CKAN_BASE}/package_show"
CKAN_DATASTORE_SEARCH = f"{CKAN_BASE}/datastore_search"


@dataclass
class SourceConfig:
    name: str
    agency: str
    ckan_slug: str          # slug do dataset no CKAN
    format: str             # 'csv' | 'json' | 'geojson'
    cache_ttl_h: int = 24
    encoding: str = "utf-8"
    category_map: dict = field(default_factory=dict)
    notes: str = ""


# ── Mapeamento de categorias EMLURB → tipo interno ─────────────────────────

_EMLURB_CATEGORIES = {
    "tapa-buracos":             "buraco",
    "pavimentação":             "buraco",
    "drenagem":                 "alagamento",
    "poda de árvore":           "queda_arvore",
    "remoção de árvore":        "queda_arvore",
    "arborização":              "queda_arvore",
    "iluminação pública":       "iluminacao",
    "poste":                    "iluminacao",
    "limpeza":                  "lixo",
    "coleta de lixo":           "lixo",
}

_DEFESA_CIVIL_CATEGORIES = {
    "alagamento":               "alagamento",
    "deslizamento":             "deslizamento",
    "barreira":                 "deslizamento",
    "queda de árvore":          "queda_arvore",
    "desabamento":              "deslizamento",
    "via intransitável":        "via_intransitavel",
    "ventania":                 "via_intransitavel",
}


# ── Fontes cadastradas ────────────────────────────────────────────────────

SOURCES: dict[str, SourceConfig] = {
    "emlurb_156": SourceConfig(
        name="Central de Atendimento EMLURB 156",
        agency="EMLURB",
        ckan_slug="central-de-atendimento-de-servicos-da-emlurb-156",
        format="csv",
        encoding="latin-1",
        cache_ttl_h=24,
        category_map=_EMLURB_CATEGORIES,
        notes="Chamados de manutenção urbana recebidos via 156 e SIGA.",
    ),
    "defesa_civil": SourceConfig(
        name="Registro de Atendimentos da Defesa Civil",
        agency="Defesa Civil do Recife",
        ckan_slug="registro-de-atendimentos-da-defesa-civil",
        format="csv",
        encoding="latin-1",
        cache_ttl_h=12,
        category_map=_DEFESA_CIVIL_CATEGORIES,
        notes="Atendimentos de emergência climática (alagamento, barreira, deslizamento).",
    ),
    "postes_iluminacao": SourceConfig(
        name="Postes de Iluminação Pública",
        agency="EMLURB",
        ckan_slug="postes-de-iluminacao-publica",
        format="csv",
        encoding="utf-8",
        cache_ttl_h=168,  # 1 semana — dado estático
        notes="Catálogo de postes de iluminação pública do Recife.",
    ),
    "logradouros": SourceConfig(
        name="Trechos de Logradouros por Bairro",
        agency="PCR / EMPREL",
        ckan_slug="trechos-de-logradouros-por-bairro",
        format="csv",
        encoding="latin-1",
        cache_ttl_h=168,
        notes="Trechos de vias urbanas com bairro e microregião.",
    ),
}


def get_source(key: str) -> Optional[SourceConfig]:
    return SOURCES.get(key)


def list_sources() -> list[str]:
    return list(SOURCES.keys())
