# HydraRec — Backend (FastAPI · Python 3.13)

API cívica de risco climático hiperlocal para os 73 bairros do Recife/PE.
Coleta dados oficiais (APAC, CEMADEN, EMLURB, Defesa Civil), funde com
reports de cidadãos, calcula um score de risco (HydraScore v2), descreve
fotos com IA de visão e gera triagem administrativa em fila + kanban.

> Plataforma cívica · Recife.
> Frontend: [hydra_rec_front](https://github.com/jordyarlego/hydra_rec_front)

---

## 1. Stack

| Camada | Tecnologia | Por quê |
|---|---|---|
| Web framework | **FastAPI** ≥ 0.110 | async-first, OpenAPI auto, Pydantic v2 |
| Servidor ASGI | **uvicorn** (standard) | dev/prod |
| Persistência | **Supabase** (PostgreSQL + Storage + Auth + RLS) | Sem ops; RLS pra LGPD |
| HTTP client | **httpx** ≥ 0.27 | async, timeouts decentes |
| Parsing HTML | **beautifulsoup4** | scraping APAC quando JSON falha |
| Validação | **Pydantic** v2 | request/response models |
| IA Vision | **google-genai** (Gemini Flash) + **openai** (NVIDIA NIM Llama 3.2 Vision) | redundância: NVIDIA primeiro, Gemini fallback |
| IA Texto | **NVIDIA NIM** (Nemotron 49B / Llama 70B) + Gemini Flash | narrativa cidadã PT-BR |
| Web Push | **pywebpush** | notificações VAPID pro cidadão |
| Imagens | **Pillow** | redimensionamento + verify de foto |
| Retry | **tenacity** | scrapers e chamadas externas |
| Tests | **pytest** + **pytest-asyncio** | 70 testes |

---

## 2. Estrutura de pastas

```
back_end_hydrarec/
├── main.py                  # FastAPI app + CORS + routers + workers
├── routers/                 # endpoints HTTP, sem lógica de negócio
│   ├── dashboard.py         # GET /api/dashboard/{bairro}: HydraScore agregado
│   ├── narrative.py         # GET /api/narrative: texto IA pra um bairro
│   ├── ai_reports.py        # POST /api/ai/describe-photo: Vision na hora
│   ├── apac.py              # GET /api/apac/*: boletins + estações
│   ├── weather.py           # GET /api/weather/*: snapshot + outlook
│   ├── reports.py           # POST/GET /api/reports*: CRUD cidadão
│   ├── push.py              # POST /api/push/*: subscribe/notify
│   ├── ws.py                # WS /ws: alertas em tempo real
│   ├── official_data.py     # GET /api/official/*: hotspots, GeoJSON bairros
│   ├── admin.py             # 25 rotas /api/admin/* protegidas por JWT
│   └── healthz.py           # /api/healthz + /api/healthz/schema + photo-debug
│
├── services/                # toda lógica de domínio
│   ├── supabase_client.py   # singletons get_client / get_service_client
│   ├── auth_guard.py        # require_admin (JWT HS256 OU consulta Supabase)
│   ├── storage.py           # upload de foto pro bucket report-photos
│   ├── rate_limit.py        # IP hasheado SHA-256+salt (LGPD)
│   ├── security.py          # helpers de hashing
│   ├── cache.py             # cache em memória com TTL
│   │
│   ├── apac_official.py     # scraping/parser cemaden APAC (chuva real)
│   ├── weather_enrich.py    # snapshot completo (chuva 1h/24h, vento, índice)
│   ├── weather_cross.py     # cruzamento com horário do report
│   ├── heat_index.py        # apparent temperature
│   ├── risk_score.py        # HydraScore v2 (regras + boletim + vulnerabilidade)
│   ├── alerts_engine.py     # regras de gatilho pra push
│   │
│   ├── ai_vision.py         # describe_photo: NVIDIA → Gemini fallback
│   ├── ai_validator.py      # gates (Triagem v2) + score 0-1 + bucket
│   ├── ai_narrative.py      # texto cidadão (Nemotron/Llama/Gemini)
│   ├── ai_explain.py        # explicação do HydraScore
│   ├── ai_assistant.py      # Q&A geral
│   │
│   ├── geo_cross.py         # PostGIS-like: bairro/RPA/via + reincidência
│   ├── official_data_sources.py  # registry: EMLURB 156, Defesa Civil, GeoJSON
│   ├── official_importer.py # baixa CSV/JSON do Portal de Dados Abertos
│   ├── dispatch_router.py   # Triagem v2: org sugerido + auto-título + SLA + duplicatas
│   ├── priority_engine.py   # combina IA + comunidade + cruzamento → prioridade
│   └── push_service.py      # pywebpush wrapper
│
├── workers/                 # tarefas de background (ENABLE_BACKGROUND_WORKERS=1)
│   ├── cron_alerts.py       # roda regras de alertas a cada 5min
│   └── ai_revalidation.py   # reprocessa reports antigos (60s)
│
├── data/
│   ├── bairros_coords.py    # centro geográfico dos 73 bairros
│   └── ml/                  # vulnerability (índice por bairro)
│
├── migrations/
│   ├── v3_civic_reports.sql      # reports, weather_snapshots, report_likes,
│   │                             # tickets, apac_stations_cache, admin_audit
│   ├── v3_official_data_hub.sql  # official_neighborhoods, official_roads,
│   │                             # official_service_requests, hotspots
│   └── v4_triagem_v2.sql         # bucket, is_urban_problem, kanban_state,
│                                 # assigned_org, sla_deadline (Triagem v2)
│
├── static/                  # build do frontend serve daqui em prod
├── tests/                   # pytest, 70 casos
├── requirements.txt
├── render.yaml              # deploy
└── .env.example
```

---

## 3. Endpoints (categorias)

### 3.1 Público (sem auth)

| Método | Path | Descrição |
|---|---|---|
| GET | `/api/dashboard/{bairro}` | HydraScore + componentes + narrativa curta |
| GET | `/api/narrative` | Narrativa cidadã da situação atual |
| GET | `/api/weather/snapshot` | Chuva 1h/24h, vento, índice de calor |
| GET | `/api/weather/outlook` | "outras áreas da RMR com chuva" |
| GET | `/api/apac/boletins` | Boletins oficiais APAC (alertas) |
| GET | `/api/apac/stations` | Estações CEMADEN próximas |
| GET | `/api/official/hotspots` | GeoJSON de áreas com mais reportes |
| GET | `/api/official/neighborhoods.geojson` | Bairros oficiais do Recife |
| POST | `/api/reports` | Cria report sem foto (JSON) |
| POST | `/api/reports/with-photo` | Cria report com foto (multipart) |
| GET | `/api/reports/queued` | Reports pendentes/recentes (pin no mapa) |
| POST | `/api/ai/describe-photo` | Vision na hora (preview no upload) |
| POST | `/api/push/subscribe` | Inscreve cidadão pra notificações |
| WS | `/ws` | Alerta tempo real (broadcast quando boletim novo) |
| GET | `/api/healthz` | Saúde geral (APAC + Supabase + Gemini + Storage) |
| GET | `/api/healthz/schema` | Quais migrations foram aplicadas |
| GET | `/api/healthz/photo-debug` | Diagnóstico do bucket de fotos |
| GET | `/api/public-config` | URL + anon key Supabase pro frontend |

### 3.2 Admin (`Authorization: Bearer <jwt>`, role=admin)

| Método | Path | Descrição |
|---|---|---|
| GET | `/api/admin/reports` | Lista (filtros: bucket, status, tipo, bairro, q, data) |
| GET | `/api/admin/reports/counts-by-bucket` | Contagem dos 3 buckets (Triagem v2) |
| GET | `/api/admin/reports/{id}` | Detalhe + weather + audit |
| PATCH | `/api/admin/reports/{id}` | Status, bucket, rejection_reason |
| DELETE | `/api/admin/reports/{id}` | Soft-delete (vira rejected) |
| GET | `/api/admin/reports/{id}/duplicates` | Candidatos (100m, 24h, mesma cat) |
| POST | `/api/admin/reports/{id}/aggregate-to/{ticket_id}` | Agrega a chamado existente |
| POST | `/api/admin/reports/batch-approve` | Cria N chamados em lote (auditado) |
| POST | `/api/admin/reports/{id}/ticket` | Cria chamado (org+título+SLA auto) |
| GET | `/api/admin/reports/{id}/official-crossing` | Bairro/RPA/via/reincidência |
| GET | `/api/admin/tickets` | Lista chamados |
| PATCH | `/api/admin/tickets/{id}` | Status, kanban_state, assigned_org |
| POST | `/api/admin/tickets/{id}/close` | Fecha com nota obrigatória |
| GET | `/api/admin/dispatch/orgs` | Lista de órgãos (EMLURB, Celpe, Defesa Civil…) |
| GET | `/api/admin/metrics` | KPIs (24h, pendentes, top bairros) |
| GET | `/api/admin/metrics/by-rpa` | Reports por região administrativa |
| GET | `/api/admin/metrics/by-neighborhood` | Top 20 bairros |
| GET | `/api/admin/metrics/recurrent-hotspots` | Ruas com reincidência alta |
| GET | `/api/admin/official-data/status` | Status das últimas importações |
| POST | `/api/admin/official-data/import` | Dispara import background |
| GET | `/api/admin/official-data/import-status` | Polling do import |
| GET | `/api/admin/official-data/service-requests` | Lista chamados oficiais importados |
| GET | `/api/admin/audit` | Audit log das ações do admin |
| GET | `/api/admin/export/reports.csv` | CSV de todos os reports |
| GET | `/api/admin/export/reports.geojson` | GeoJSON pra QGIS |

---

## 4. Pipeline da IA (Triagem v2)

Quando o cidadão envia um report **com foto**:

```
POST /api/reports/with-photo
        ↓
1. Validação multipart + tamanho ≤ 5MB
2. Upload pro Supabase Storage (report-photos/)
3. Insere row na tabela reports (status='pending')
4. INICIA background task _run_ai_pipeline:
        ↓
   a) ai_vision.describe_photo(url)
      - prompt pede { description, type, confidence, is_urban_problem }
      - Tenta NVIDIA Llama 3.2 Vision (11B/90B) primeiro
      - Fallback Gemini Flash
      - Salva photo_ai_description, photo_ai_confidence,
        photo_ai_is_urban_problem em reports
        ↓
   b) weather_enrich.snapshot_at_point(lat, lon, ts)
      - Pega chuva 1h/24h, vento, índice de calor
      - Persiste em weather_snapshots
        ↓
   c) ai_validator.persist_validation(report)
      - Gate 1: photo_url + is_urban_problem=False → score 0.10, bucket=filtrado
      - Gate 2: photo_url + confidence < 0.4 → score 0.15, bucket=filtrado
      - Heurística: base + bonus textual + bonus clima + bonus confidence
      - Calcula bucket:
        • score < 0.20 → filtrado
        • score ≥ 0.75 + priority alta + recurrence 0 → auto_validado
        • else → revisar
      - Salva ai_validation_score, ai_validation_notes, bucket
        ↓
5. _cross_official em background:
   - geo_cross.cross_report_with_official_data
   - bairro oficial, RPA, via próxima, recurrence_score
   - persiste em report_official_crossings
        ↓
6. priority_engine combina tudo → prioridade (urgente/alta/media/baixa)
```

**Quando o admin clica "Validar e gerar chamado":**

```
POST /api/admin/reports/{id}/ticket
        ↓
1. dispatch_router.suggest_org(type)
   → EMLURB_DRENAGEM | DEFESA_CIVIL | CELPE | ...
2. dispatch_router.auto_title(report, geo)
   → "Alagamento em R. da Aurora, Boa Vista (RPA 1)"
3. dispatch_router.sla_deadline(priority)
   → +2h urgente / +24h alta / +72h media / +7d baixa
4. INSERT em tickets com:
   assigned_org, kanban_state='aberto', sla_deadline, title, notes
5. UPDATE reports.status='validated', ticket_id=...
6. INSERT em admin_audit
```

**Aprovação em lote (bucket auto_validado):**

```
POST /api/admin/reports/batch-approve { report_ids: [...] }
→ até 100 reports em transação
→ cria 1 ticket por report (org+título+SLA auto)
→ admin_audit registra { action: 'batch_approve', count, ids }
```

---

## 5. Fontes de dados

| Fonte | Tipo | Uso | Onde no código |
|---|---|---|---|
| **APAC CEMADEN** | JSON live a cada 5min | Chuva real por estação | `services/apac_official.py` |
| **APAC boletins oficiais** | Scraping HTML | Alertas (SEVERO/ALTO/MOD/ATENCAO) | `services/apac_official.py` |
| **INMET climatologia** | Tabela mensal | Referência histórica de chuva | `services/weather_enrich.py` |
| **Portal Dados Abertos Recife — EMLURB 156** | CSV public | Histórico de chamados oficiais | `services/official_importer.py` |
| **Portal Dados Abertos — Defesa Civil** | CSV public | Histórico de atendimentos | `services/official_importer.py` |
| **GeoJSON oficial bairros 2023** | Arquivo estático | Detecção de bairro por point-in-polygon | `services/geo_cross.py` |
| **Postes de iluminação Recife** | CSV public | Cadastro pra cruzamento de iluminação | `services/official_importer.py` |
| **Logradouros Recife** | CSV public | Nome de via mais próxima | `services/official_importer.py` |

### 5.0 Por que os dados de clima podem divergir do Google ou do site oficial APAC

Pergunta comum: "o Google diz que está 26 °C com 82% de umidade e céu limpo, mas o HydraRec mostra 28 °C, 100% de umidade e chuva leve. Quem está certo?" Os dois podem estar certos — eles vêm de fontes diferentes, medindo coisas diferentes.

**De onde vem cada número do HydraRec** (ver `services/apac_official.py::weather_at`):

| Variável | Fonte | Raio máximo de busca |
|---|---|---|
| Chuva (mm/h) | Estação CEMADEN mais próxima | 30 km |
| Temperatura, umidade, vento | Estação `meteorologia24h` mais próxima | 80 km |
| Fallback de temperatura/umidade | Estação `climatologico` mais próxima | 50 km |
| Alerta agregado da RMR | Boletim sintético APAC | RMR inteira |

Cada uma dessas é uma **estação física diferente**, instalada em um endereço real. A estação CEMADEN que mede chuva no bairro selecionado **não é** a mesma estação que mede temperatura — porque CEMADEN não publica temperatura, e `meteorologia24h` não tem cobertura densa na RMR. O HydraRec pega a estação mais próxima de cada variável e mostra todas juntas.

**De onde vem o número do Google Weather:**

- O endereço "Av. Cruz Cabugá, 1111 — Santo Amaro" que o Google mostra é só **referência geográfica** da sede da APAC. Os números em si **não** vêm dos sensores APAC.
- O dado vem da **The Weather Channel / IBM** (modelo numérico global combinado com APIs comerciais), suavizado pra um ponto único da cidade e atualizado a cada ~1 hora.

**Por que isso gera divergência real:**

| Causa | Efeito típico |
|---|---|
| Estações fisicamente distantes (até 80 km) | Temperatura difere 2–3 °C entre bairros (ilhas de calor) |
| Garoa fina <0.2 mm satura sensor de umidade local | Umidade chega a 100% por minutos; modelo do Google amortiza |
| Cobertura de nuvem é hiperlocal | Pode chover em Casa Amarela e céu limpo em Boa Viagem ao mesmo tempo |
| Defasagem do modelo agregado | Google atualiza a cada ~1 h; CEMADEN a cada 5 min |
| Site oficial APAC cita estação central | Mostra leitura de Santo Amaro mesmo pra usuário em outro bairro |

**O que o HydraRec optou por fazer:**

- Mostrar o número **fiel à estação real mais próxima** do bairro selecionado, não a média da cidade.
- **Não inventar fallback** quando a estação não publica a variável: o frontend mostra `—` em vez de exibir `28°` / `70%` chumbados (correção F5).
- Expor no HeroCard um botão **ⓘ** ao lado do nome da estação, que abre o detalhamento "Chuva: estação X (Y km) · Temp: estação Z (W km)" pra o usuário entender de onde vem cada número.

Resultado: o HydraRec tende a ser **mais fiel ao que está acontecendo naquele ponto da cidade naquele minuto**, enquanto o Google tende a ser mais **suavizado e atrasado**, e o site oficial APAC tende a mostrar **a estação central** mesmo pra outros bairros.

### 5.1 Bases oficiais importadas

As bases oficiais importadas **não ficam em arquivo local** depois da coleta. Elas são normalizadas e salvas no Supabase, usando a `SUPABASE_SERVICE_KEY` somente no backend.

**Onde fica salvo:**

| Tabela Supabase | Conteúdo |
|---|---|
| `official_neighborhoods` | Bairros oficiais do Recife, RPA, macrozona/microrregião e metadados do GeoJSON |
| `official_service_requests` | Chamados oficiais/históricos importados da EMLURB 156 e Defesa Civil |
| `official_import_log` | Log de cada importação: fonte, quantidade OK, erros, duração e mensagem |
| `report_official_crossings` | Resultado do cruzamento entre um report do cidadão e as bases oficiais |
| `official_roads` | Vias/logradouros, quando essa base estiver carregada |
| `official_hotspots` | Hotspots oficiais, quando pré-computados |

**Como a coleta funciona:**

1. O admin chama `POST /api/admin/official-data/import`, ou o backend roda `services.official_importer.import_all()`.
2. `official_importer.py` consulta o registry em `services/official_data_sources.py`.
3. Para EMLURB 156 e Defesa Civil, o backend descobre o CSV mais recente no Portal de Dados Abertos do Recife, baixa o arquivo e normaliza colunas como `bairro`, `logradouro`, `serviço`, `status`, `data`, `lat` e `lon`.
4. Para bairros oficiais, o backend lê o GeoJSON local `front_end_hydrarec/src/data/geo/recife_bairros_2023.geojson`.
5. EMLURB/Defesa Civil são salvos de forma incremental pela chave `(source, external_id)`: registro novo é inserido e o histórico antigo permanece no Supabase. Quando o banco expõe essa chave ao PostgREST como constraint completa, o importador faz `upsert` e também atualiza registros repetidos; se o banco só tiver índice parcial, o fallback insere apenas IDs novos sem apagar os antigos.
6. Quando um cidadão envia um report, `_cross_official()` grava em `report_official_crossings` bairro/RPA/rua aproximada, reincidência e chamados oficiais próximos.

**Importante:** essa base oficial não é stream em tempo real. APAC/CEMADEN é consultado como dado vivo de chuva; EMLURB/Defesa Civil vêm como snapshots públicos do portal. Portanto a atualização correta é periódica ou manual pelo admin, não em tempo real.

**Importação incremental vs. carga completa:**

- A primeira importação popula o histórico disponível.
- As próximas importações consultam novamente o snapshot público e tentam fazer `upsert`.
- Se um chamado já existe em `official_service_requests`, ele é atualizado quando o Supabase aceita `on_conflict`; no fallback, ele é mantido como está.
- Se apareceu um chamado novo no portal, ele é inserido.
- O importador não precisa apagar os antigos para descobrir novos.
- O campo que garante isso é `external_id`, combinado com `source`.

**Proximidade no cruzamento:**

O report do cidadão não precisa cair exatamente no mesmo ponto do registro oficial. O cruzamento usa:

- raio padrão de 300m para registros oficiais com `lat/lon`;
- cálculo Haversine para ordenar por distância real;
- comparação de tipo do report com `service_type` oficial, tolerando termos sem acento como `ILUMINACAO`, `PAVIMENTACAO`, `TAPA-BURACO`;
- fallback por bairro quando há poucos registros georreferenciados próximos.

Isso significa que um report de buraco perto da Rua da Soledade pode cruzar com `OPERACAO TAPA-BURACO` mesmo que o ponto do usuário não seja idêntico ao ponto oficial.

**O que é Haversine e por que usamos:**

Haversine é uma fórmula para calcular a distância aproximada entre dois pontos na superfície da Terra usando latitude e longitude. Ela considera a curvatura do planeta, diferente de uma subtração simples de coordenadas.

No HydraRec ela serve para:

- validar se o report foi feito perto da posição real do usuário;
- ordenar chamados oficiais por proximidade real;
- aplicar o raio de cruzamento, hoje `300m`, entre report cidadão e registros oficiais;
- evitar dizer que dois pontos são próximos só porque as coordenadas parecem parecidas numericamente.

Exemplo prático: se o usuário reporta um buraco em `lat=-8.05623, lon=-34.89018`, o backend compara esse ponto com registros oficiais ao redor. Um registro de `OPERACAO TAPA-BURACO` a 0m, 50m ou 120m entra como evidência próxima; um registro a 2km não entra no raio de reincidência daquele report.

Implementação: `services/geo_cross.py::haversine_distance_m`.

**Seed MVP:**

`POST /api/admin/official-data/import-seed` usa `data/seed/official_sample.json`. É uma amostra pequena para demonstração/fallback quando o portal oficial está fora ou muda schema. Seed não representa todo o histórico da cidade.

**Comandos úteis locais:**

```bash
cd back_end_hydrarec

# Importar snapshot completo disponível no portal público
venv/bin/python -c $'from dotenv import load_dotenv\nload_dotenv()\nimport asyncio\nfrom services.official_importer import import_all\nprint(asyncio.run(import_all()))'

# Verificar totais salvos no Supabase
venv/bin/python -c $'from dotenv import load_dotenv\nload_dotenv()\nfrom services.supabase_client import get_service_client\ndb=get_service_client()\nfor t in ["official_neighborhoods","official_service_requests"]:\n    r=db.table(t).select("id", count="exact").limit(1).execute()\n    print(t, r.count)'
```

---

## 6. Database (Supabase)

**Migrations aplicadas em ordem (ver `MIGRATION_GUIDE.md` na raiz):**

1. `v3_civic_reports.sql` — reports, weather_snapshots, report_likes, tickets, admin_audit
2. `v3_official_data_hub.sql` — official_neighborhoods, official_service_requests, hotspots
3. `v4_triagem_v2.sql` — bucket, is_urban_problem, kanban_state, assigned_org, sla_deadline
4. `v5_official_requests_full_unique.sql` — índice único completo para `upsert` incremental em `(source, external_id)`

**Tabelas principais:**

- `reports`: ocorrências do cidadão (lat, lon, tipo, severidade, foto, score IA, bucket)
- `weather_snapshots`: snapshot meteorológico no momento do report
- `report_likes`: votação ↑↓
- `tickets`: chamados administrativos (kanban_state, assigned_org, sla_deadline)
- `admin_audit`: log de tudo que admin faz
- `report_official_crossings`: cruzamento bairro/RPA/via/reincidência
- `official_*`: bases oficiais importadas
- `apac_stations_cache`: cache de estações pra perf

**Storage:**

- bucket `report-photos`, public read, max 5MB, MIMEs `image/jpeg,image/png,image/webp`

**RLS:** todas as tabelas com policies; service key só no backend, anon key no frontend.

---

## 7. Setup local

```bash
# 1. Python 3.13 + venv
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. .env
cp .env.example .env
# preencha SUPABASE_URL, SUPABASE_KEY, SUPABASE_SERVICE_KEY
# preencha GEMINI_API_KEY ou NVIDIA_API_KEY (ou ambos, fallback)

# 3. Migrations (uma vez)
# Aplicar via Supabase SQL Editor seguindo MIGRATION_GUIDE.md (raiz do monorepo)

# 4. Rodar
.venv/bin/python -m uvicorn main:app --reload --port 8000

# 5. Testes
.venv/bin/pytest -q
```

Frontend roda em `:5173` e faz proxy de `/api` e `/ws` pra `:8000`.
Em prod, FastAPI serve o build do React de `static/`.

---

## 8. Variáveis de ambiente

| Variável | Obrigatória | Uso |
|---|---|---|
| `SUPABASE_URL` | ✔ | URL do projeto |
| `SUPABASE_KEY` | ✔ | anon key (frontend usa via /api/public-config) |
| `SUPABASE_SERVICE_KEY` | ✔ | service_role (NUNCA exposta no front) |
| `SUPABASE_JWT_SECRET` | opt | Sem ela, valida admin via API Supabase Auth |
| `SUPABASE_STORAGE_BUCKET` | ✔ | `report-photos` |
| `GEMINI_API_KEY` | opt* | Vision + narrativa fallback |
| `NVIDIA_API_KEY` | opt* | Vision principal + narrativa principal |
| `VAPID_PUBLIC_KEY` | opt | Web Push |
| `VAPID_PRIVATE_KEY` | opt | Web Push |
| `VAPID_EMAIL` | opt | Web Push |
| `IP_HASH_SALT` | ✔ | Hash do IP do cidadão (LGPD) |
| `RATE_LIMIT_REPORTS_SECONDS` | opt | Default 300 |
| `ALLOWED_ORIGINS` | opt | CORS, default localhost |
| `ENABLE_BACKGROUND_WORKERS` | opt | Liga cron de alertas (default em prod Render) |
| `SENTRY_DSN` | opt | Observabilidade |

*pelo menos uma das duas chaves de IA pra Vision/narrativa funcionar.

---

## 9. LGPD & Segurança

- IP do cidadão é hasheado SHA-256+salt antes de ir pro banco
- Nenhum dado pessoal é coletado (sem nome, e-mail, telefone)
- Service key **só** no backend (variável de ambiente, nunca commitada)
- Migrations criam policies RLS automaticamente
- Admin JWT é validado por assinatura HS256 (com SUPABASE_JWT_SECRET) OU
  por chamada à API do Supabase (sem o secret) — fallback gracioso
- Refresh token rotation: frontend renova token antes de expirar; em
  401 do backend, tenta refresh + retry transparente uma vez

---

## 10. Testes

70 casos pytest cobrindo: validator (gates novos), priority engine,
geo cross, weather enrich, APAC parser, auth guard, dispatch router,
heat index, alerts engine.

```bash
.venv/bin/pytest -q                          # tudo
.venv/bin/pytest tests/test_ai_validator.py  # só validator
```

---

## 11. Deploy

- **Render.com** via `render.yaml`
- Build: instala deps + serve `main:app` com uvicorn
- Healthcheck: `/api/healthz`
- Workers de background ligam se `ENABLE_BACKGROUND_WORKERS=1` ou `RENDER=true`

---

## 12. Roadmap

Fases 1-11 entregues (HydraScore v2, Data Fusion, Reports cívicos,
Mapa, IA narrativa, Trajeto, PWA + Push, A11y WCAG AA, Forecast 6h).

**Triagem v2** (2026-05-16) — 3 buckets, kanban, gates IA, refresh
auto. Spec: `docs/superpowers/specs/2026-05-16-triagem-v2-design.md`
(no monorepo raiz).

**Próximo ciclo:** correções de UX/copy + IA prioriza pela foto +
integração real com órgãos. Backlog em
`docs/superpowers/specs/2026-05-16-feedback-ciclo-2-backlog.md`.
