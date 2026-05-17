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
    # buraco / pavimentação
    "tapa-buracos":             "buraco",
    "tapa buraco":              "buraco",
    "tapaburaco":               "buraco",
    "pavimentação":             "buraco",
    "pavimentacao":             "buraco",
    "recapeamento":             "buraco",
    "buraco":                   "buraco",
    "calçamento":               "buraco",
    "calcamento":               "buraco",
    "asfalto":                  "buraco",
    # drenagem / alagamento
    "drenagem":                 "alagamento",
    "boca de lobo":             "alagamento",
    "boca-de-lobo":             "alagamento",
    "galeria":                  "alagamento",
    "canal":                    "alagamento",
    "desobstrução":             "alagamento",
    "desobstrucao":             "alagamento",
    "bueiro":                   "alagamento",
    # arborização / árvore
    "poda de árvore":           "queda_arvore",
    "poda de arvore":           "queda_arvore",
    "remoção de árvore":        "queda_arvore",
    "remocao de arvore":        "queda_arvore",
    "remoção de galho":         "queda_arvore",
    "arborização":              "queda_arvore",
    "arborizacao":              "queda_arvore",
    "áreas verdes":             "queda_arvore",
    "areas verdes":             "queda_arvore",
    "areas verdes urbanas":     "queda_arvore",
    "supressão":                "queda_arvore",
    # iluminação
    "iluminação pública":       "iluminacao",
    "iluminacao publica":       "iluminacao",
    "iluminação":               "iluminacao",
    "iluminacao":               "iluminacao",
    "lâmpada":                  "iluminacao",
    "lampada":                  "iluminacao",
    "poste":                    "iluminacao",
    # lixo
    "limpeza":                  "lixo",
    "limpeza urbana":           "lixo",
    "coleta de lixo":           "lixo",
    "coleta":                   "lixo",
    "lixo":                     "lixo",
    "entulho":                  "lixo",
    "animais mortos":           "lixo",
    "varrição":                 "lixo",
    "varricao":                 "lixo",
    "capinação":                "lixo",
    "capinacao":                "lixo",
    "roçagem":                  "lixo",
    "rocagem":                  "lixo",
    "containers":               "lixo",
    # outros que NÃO viram outro genérico
    "feira":                    "lixo",
    "manutenção":               "outro",
    "manutencao":               "outro",
}

_DEFESA_CIVIL_CATEGORIES = {
    "alagamento":               "alagamento",
    "inundação":                "alagamento",
    "inundacao":                "alagamento",
    "enchente":                 "alagamento",
    "deslizamento":             "deslizamento",
    "barreira":                 "deslizamento",
    "encosta":                  "deslizamento",
    "queda de árvore":          "queda_arvore",
    "queda de arvore":          "queda_arvore",
    "árvore":                   "queda_arvore",
    "arvore":                   "queda_arvore",
    "desabamento":              "deslizamento",
    "rachadura":                "deslizamento",
    "via intransitável":        "via_intransitavel",
    "via intransitavel":        "via_intransitavel",
    "ventania":                 "via_intransitavel",
    "vento":                    "via_intransitavel",
    "incêndio":                 "outro",
    "incendio":                 "outro",
    "lonas":                    "outro",
    "vistoria":                 "outro",
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
        ckan_slug="postes-iluminacao-publica-do-recife",   # slug corrigido em 2026
        format="csv",
        encoding="utf-8",
        cache_ttl_h=168,
        notes="Catálogo de postes de iluminação pública do Recife.",
    ),
}


def get_source(key: str) -> Optional[SourceConfig]:
    return SOURCES.get(key)


def list_sources() -> list[str]:
    return list(SOURCES.keys())
