# HydraRec — Backend

API FastAPI do sistema de monitoramento de risco climático em tempo real para bairros do Recife.

> **TCC UFPE 2026** · Jordy Arlego

---

## Stack

| Camada | Tecnologia |
|---|---|
| Framework | FastAPI + Uvicorn |
| Python | 3.9+ |
| Banco de dados | Supabase (PostgreSQL + RLS) |
| IA — narrativa | Google Gemini 1.5 Flash |
| IA — explicação score | NVIDIA NIM · Nemotron Super 49B |
| Meteorologia | Open-Meteo API (gratuito, sem chave) |
| Maré | Scraping FEMAR (BeautifulSoup4) |
| Rotas | OpenRouteService API |
| Segurança | IP hasheado SHA-256 (LGPD) |

---

## Rodando localmente

```bash
cd back_end_hydrarec
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # preencha as chaves
uvicorn main:app --port 8000 --reload
```

Acessa `http://localhost:8000` — dashboard abre direto.
Documentação interativa da API: `http://localhost:8000/docs`

---

## Variáveis de ambiente

```env
GEMINI_API_KEY=          # Google AI Studio → ai.google.dev
NVIDIA_API_KEY=          # NVIDIA NIM → console.nvidia.com/nim
SUPABASE_URL=
SUPABASE_KEY=            # chave anon (pública)
SUPABASE_SERVICE_KEY=    # service_role — SOMENTE backend, nunca frontend
OPENROUTESERVICE_KEY=
OPENWEATHER_KEY=
IP_HASH_SALT=            # string aleatória longa
ALLOWED_ORIGINS=http://localhost:5173,http://localhost:8000
```

> **LGPD:** IPs hasheados SHA-256 + salt. `SUPABASE_SERVICE_KEY` (permissão total) nunca sai do backend.

---

## Endpoints

### `GET /api/dashboard/{bairro}`
Clima atual, Hydra Score v2 completo, previsão 6h e 7 dias.

```json
{
  "weather": { "current": { "temperature_2m": 29, "weather_code": 0 } },
  "risk": {
    "score": 47, "nivel": "MODERADO", "version": "v2",
    "components": {
      "rain_next": 9.7, "rain_past": 9.3, "tide": 14.5,
      "vulnerability": 9.0, "altitude": 4.0, "atmospheric": 0.0, "community": 0.0
    },
    "raw_values": {
      "chuva_prevista_24h": 3.9, "chuva_acumulada_24h": 8.4,
      "mare_altura": 2.9, "mare_trend": "Alta",
      "umidade": 70, "pressao": 1011.6, "altitude_m": 5.0
    }
  },
  "heatIndex": { "value": 34.2, "risk": "ATENCAO" },
  "forecast6h": [...], "forecastDaily": [...]
}
```

### `GET /api/explain/{bairro}`
Explicação didática do Hydra Score gerada por IA para o morador.

**Modelo primário:** `nvidia/llama-3.3-nemotron-super-49b-v1`
**Modelo fallback:** `meta/llama-3.3-70b-instruct`

Benchmark realizado em maio/2025 com prompt de contexto climático em PT-BR:

| Modelo | Latência | Qualidade |
|---|---|---|
| Nemotron Super 49B | 3.9s | **Melhor** — cita "inundação predial e viária", mais técnico |
| Llama 3.3 70B | 5.4s | Bom — resposta mais genérica |
| Mixtral 8x22B | 1.3s | Rápido — resposta mais superficial |

**Cache:** 5 minutos em memória por bairro (dict `{bairro: (timestamp, texto)}`)
**Fallback local:** se NVIDIA API indisponível, texto gerado a partir dos componentes sem chamada externa

```json
{ "explanation": "**Por que 47 pts em Paissandu?**\n\n🌊 **Maré 2.9m...**", "score": 47, "nivel": "MODERADO" }
```

### `POST /api/narrative`
4 frases estilo Defesa Civil: diagnóstico, rua específica a evitar, janela de tempo, ação concreta.
**Modelo:** Gemini 1.5 Flash · temperatura 0.4 · max 200 tokens

### `POST /api/reports` / `GET /api/reports/nearby` / `POST /api/reports/{id}/confirm`
Ocorrências da comunidade. Tipos: alagamento, deslizamento, queda_arvore, via_intransitavel, poste_caido, outro.

### `POST /api/scores`
Hydra Score em lote (até 6 bairros simultâneos via `asyncio.gather`).

### `GET /api/route/analyze`
Análise de risco em trajeto origem→destino via OpenRouteService + haversine por pontos críticos.

---

## Hydra Score v2

Score 0–100 com 7 componentes:

| Componente | Máx | Ativação |
|---|---|---|
| Chuva prevista 24h | 50 pts | Sempre — curva logística `50*(1−e^(−mm/18))` |
| Chuva acumulada 24h | 25 pts | Sempre — mesma curva |
| Maré | 15 pts | Sempre, mas ×0.25 sem chuva |
| Vulnerabilidade do bairro | 15 pts | **Só com chuva ≥ 1mm** |
| Altitude baixa | 8 pts | **Só com chuva ≥ 1mm** |
| Instabilidade atmosférica | 7 pts | **Só com chuva ≥ 1mm** |
| Reports da comunidade | 10 pts | **Sempre** |

**Decisão de design:** sem chuva, o score fica ≈0 mesmo em bairros vulneráveis. Vulnerabilidade e altitude são *amplificadores* do risco pluvial, não fontes autônomas. Corrige o problema "ATENÇÃO com sol".

**Níveis:** SEGURO (0–24) · ATENÇÃO (25–44) · MODERADO (45–64) · ALTO (65–79) · SEVERO (80–100)

**Vulnerabilidade por bairro** — índice baseado em frequência de alagamentos APAC 2018–2024:
Brasília Teimosa 0.95 · Ibura 0.88 · Jordão 0.85 · Tejipió 0.85 · Afogados 0.82 · Paissandu 0.60 · Boa Viagem 0.50 · Default 0.35

---

## Estrutura

```
back_end_hydrarec/
├── main.py
├── routers/
│   ├── dashboard.py     # /api/dashboard, /api/scores, /api/explain
│   ├── narrative.py     # /api/narrative (Gemini)
│   ├── reports.py       # CRUD reports + confirmação
│   ├── route.py         # análise de trajeto
│   └── healthz.py
├── services/
│   ├── risk_score.py    # Hydra Score v2
│   ├── ai_narrative.py  # Gemini 1.5 Flash
│   ├── ai_explain.py    # NVIDIA NIM Nemotron (+ cache 5min)
│   ├── heat_index.py    # Steadman-NOAA
│   ├── traffic.py       # multiplicador chuva/hora
│   ├── routing.py       # OpenRouteService + haversine
│   ├── rate_limit.py    # anti-spam IP hasheado
│   ├── security.py      # SHA-256 + salt
│   └── weather/
│       ├── open_meteo.py
│       ├── fusion.py    # consenso multi-fonte
│       └── tides.py     # scraping FEMAR
├── data/
│   └── vulnerability.py
├── models/schemas.py
├── tests/               # 9 casos pytest
└── static/              # build do frontend
```

---

## Cache

| Recurso | TTL | Mecanismo |
|---|---|---|
| Open-Meteo | 15 min | `services/cache.py` |
| Maré FEMAR | 1 hora | `services/cache.py` |
| Explicação IA | **5 min** | dict em memória `ai_explain.py` |
| Narrativa Gemini | Sem cache | Dados mudam por minuto |

---

## Testes

```bash
pytest tests/ -v   # 9 passed
```

Casos críticos: `test_jordao_13mm_nao_e_seguro` (regressão bug v1) · `test_dia_de_sol_score_baixo` · `test_score_nao_ultrapassa_100`
