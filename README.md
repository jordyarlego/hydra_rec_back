# HydraRec V3 — Backend

> Estado atual: plataforma cívica mapa-first para reports colaborativos no Recife.
> V3 remove rotas/OpenWeather/Open-Meteo/INMET como fonte operacional e usa APAC como fonte meteorológica única.

## V3 em produção

- Clima: `services/apac_official.py` consome `/cemaden/`, `/meteorologia24h/` e `/blank_json_climatologico/`.
- Reports: `POST /api/reports/with-photo` aceita foto, cruza snapshot APAC e dispara IA em background.
- Fotos: Supabase Storage bucket `report-photos`.
- Admin: `/admin` + endpoints `/api/admin/*`, protegidos por Supabase Auth role `admin`.
- IA: `ai_vision`, `ai_validator`, `ai_assistant`; fallback heurístico quando Gemini não está disponível.
- Dados oficiais: aplicar `migrations/v3_official_data_hub.sql` para ativar cruzamentos EMLURB/Defesa Civil.

Variáveis mínimas:

```env
SUPABASE_URL=
SUPABASE_KEY=
SUPABASE_SERVICE_KEY=
SUPABASE_STORAGE_BUCKET=report-photos
IP_HASH_SALT=
ALLOWED_ORIGINS=http://localhost:5173,http://localhost:8000
GEMINI_API_KEY=        # opcional, melhora IA de foto
NVIDIA_API_KEY=        # opcional, narrativa
```

`OPENWEATHER_KEY` é V2/deprecado.

API FastAPI do sistema de monitoramento de risco climático em tempo real para bairros do Recife.

> **TCC UFPE 2026** · Jordy Arlego

---

## Stack

| Camada | Tecnologia |
|---|---|
| Framework | FastAPI + Uvicorn |
| Python | 3.9+ |
| Banco de dados | Supabase (PostgreSQL + RLS) |
| IA — narrativa | NVIDIA NIM Nemotron 49B → Gemini 1.5 Flash → local |
| Tempo real | WebSocket nativo FastAPI (`/ws/{bairro}`) |
| Push nativo | VAPID Web Push via pywebpush 2.x |
| IA — explicação score | NVIDIA NIM · Nemotron Super 49B |
| Meteorologia | Open-Meteo (horário, gratuito) + OpenWeatherMap (3h blocks) + INMET A301/A357 (estações oficiais) |
| Fusão de dados | Cruzamento multi-fonte: chuva, umidade, vento e pressão de OM + OWM + INMET — média consensual + breakdown por fonte |
| Alertas oficiais | APAC scraper — tenta JSON API → HTML BeautifulSoup → None (cache 30min) |
| Estações pluviométricas RT | **APAC Geoportal ArcGIS REST** — 35 estações na RMR com chuva 1h/3h/6h/24h em tempo real; API pública aberta, sem chave (cache 10min) |
| Alertas INMET | INMET alertas ativos por UF (`apitempo.inmet.gov.br/AVISO/{date}`) — filtrados para PE, aplicados como bônus no score de rota (cache 30min) |
| Maré | Scraping FEMAR (BeautifulSoup4) |
| Rotas | OSRM (OpenStreetMap, gratuito, sem chave) — suporte a carro/bike/a pé com multiplicador de risco por modal; geocodificação de endereços via Nominatim |
| Segurança | IP hasheado SHA-256 (LGPD) |
| Previsão 6h | Hydra Score v2 aplicado slot-a-slot com consenso Open-Meteo + OWM + INMET |

---

## Fontes de dados

| Fonte | Uso no HydraRec | Arquivos principais | Chave |
|---|---|---|---|
| Open-Meteo Forecast API | Clima atual, previsão horária/diária, chuva prevista, umidade, vento, pressão, UV e visibilidade | `services/weather/open_meteo.py`, `services/weather/fusion.py` | Não |
| OpenWeatherMap Forecast | Segunda fonte de chuva prevista e métricas atmosféricas; aumenta consenso e confiança | `services/weather/owm.py`, `services/weather/fusion.py` | `OPENWEATHER_KEY` |
| INMET API Tempo | Estações oficiais A301 Recife e A357 Olinda; chuva recente, temperatura, umidade, pressão e vento | `services/weather/inmet.py` | Não |
| INMET Avisos | Alertas meteorológicos oficiais para PE usados na análise de trajeto | `services/inmet_alerts.py`, `routers/route.py` | Não |
| APAC Boletim | Boletim oficial da Agência Pernambucana de Águas e Clima; calibra UI, rota e narrativa IA | `services/apac_scraper.py`, `routers/apac.py` | Não |
| APAC Geoportal ArcGIS | Estações pluviométricas em tempo real na RMR; hazards de chuva ativa próximos da rota | `services/apac_stations.py`, `services/routing.py` | Não |
| FEMAR | Maré atual/tendência por scraping; usada no Hydra Score | `services/weather/tides.py` | Não |
| OpenStreetMap / CartoDB | Tiles do mapa Leaflet | `front_end_hydrarec/src/components/map/HydraMap.jsx` | Não |
| OSRM público | Cálculo de rotas carro/bike/a pé | `services/routing.py` | Não |
| Nominatim / OpenStreetMap | Geocodificação de endereços na rota e no report por endereço próximo | `RouteAnalysis.jsx`, `ReportModal.jsx` | Não |
| Prefeitura do Recife — Dados Abertos | GeoJSON oficial de limites dos bairros 2023 | `src/data/geo/recife_bairros_2023.geojson`, `bairroGeo.js`, `HydraMap.jsx` | Não |
| Pontos críticos curados | Pontos históricos de alagamento/deslizamento usados como hazards e referência narrativa | `front_end_hydrarec/src/data/pontos_criticos.js`, `services/routing.py` | Não |
| Supabase Postgres | Reports, alertas, rate limit, push subscriptions, histórico e RLS | `routers/reports.py`, `services/supabase_client.py`, `push_service.py` | Sim |
| NVIDIA NIM | Narrativa IA e explicação do score, quando configurado | `services/ai_narrative.py`, `services/ai_explain.py` | `NVIDIA_API_KEY` |
| Google Gemini | Fallback de narrativa IA quando NVIDIA falha | `services/ai_narrative.py` | `GEMINI_API_KEY` |

Observações:

- Open-Meteo, INMET, APAC, OSRM, Nominatim e GeoJSON da Prefeitura são fontes públicas/sem chave.
- OpenWeatherMap é opcional, mas melhora o consenso multi-fonte.
- Reports da comunidade são fonte própria do sistema; não vêm de API externa.

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
OPENWEATHER_KEY=
VAPID_PUBLIC_KEY=        # Web Push
VAPID_PRIVATE_KEY=       # Web Push
VAPID_EMAIL=mailto:seu-email@dominio.com
IP_HASH_SALT=            # string aleatória longa
ALLOWED_ORIGINS=http://localhost:5173,http://localhost:8000
```

> **LGPD:** IPs hasheados SHA-256 + salt. `SUPABASE_SERVICE_KEY` (permissão total) nunca sai do backend.

---

## Hydra Score v2 — Calibração

Fórmula recalibrada em 2026-05 para Recife (`services/risk_score.py`). Curva logística com plateau evita inflação para chuvas leves:

| Componente | Peso máximo | Notas |
|---|---|---|
| `rain_next` | 35 pts | `35 * (1 - exp(-mm/22))`. 11mm→14pts, 25mm→25pts, 50mm→31pts |
| `rain_past` | 10 pts | Curva similar, peso reduzido. Solo saturado amplifica risco pluvial |
| `tide` | 10 pts | `(altura/3) * 10` com chuva ativa; ×0.2 sem chuva |
| `vulnerability` | 8 pts | Histórico do bairro × 8. Só com chuva ativa |
| `altitude` | 4 pts | < 5m → 4pts; 5–15m → 2pts; > 15m → 0 |
| `atmospheric` | 6 pts | Umidade ≥ 88% + pressão < 1006 mbar |
| `community` | 10 pts | 3+ reports → 10pts; 1–2 reports → 5pts |

**Níveis:** SEGURO (< 25) · ATENCAO (25–44) · MODERADO (45–64) · ALTO (65–79) · SEVERO (≥ 80).

**Filosofia:** chuva é gatilho. Sem chuva (rain_next < 1mm AND rain_past < 1mm), os componentes estruturais (vulnerabilidade, altitude, maré, atmosférico) zeram automaticamente — só `rain_next`/`rain_past` mínimos contam. Isso evita o caso "53/100 em dia ensolarado".

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
      "rain_next": 5.7, "rain_past": 3.4, "tide": 9.7,
      "vulnerability": 4.8, "altitude": 4.0, "atmospheric": 0.0, "community": 0.0
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
Explicação didática do Hydra Score gerada por IA para o morador (implementado em `services/ai_explain.py`).

**Cache:** 5 minutos em memória por bairro — `_cache: dict[str, tuple[float, str]]`. Evita rechamadas desnecessárias enquanto o score não muda significativamente.

#### Cadeia de fallback (3 tentativas)

```
Tentativa 1: NVIDIA NIM — nvidia/llama-3.3-nemotron-super-49b-v1
             ↓ (exceção ou timeout)
Tentativa 2: NVIDIA NIM — meta/llama-3.3-70b-instruct
             ↓ (ambos falham OU NVIDIA_API_KEY ausente)
Tentativa 3: fallback local Python — sem chamada externa, sem mock
             usa risk['components'] e risk['raw_values'] reais do Hydra Score v2
```

**Tentativa 1 e 2** — modelos NVIDIA NIM via OpenAI-compatible SDK (`base_url: https://integrate.api.nvidia.com/v1`). Iterados em sequência com `try/except`; ao primeiro sucesso, o loop quebra.

**Tentativa 3** — função `_fallback(bairro, risk, raw)` em Python puro. **Não é um mock**: usa os valores reais calculados pelo Hydra Score v2 (chuva prevista, acumulada, altura da maré, vulnerabilidade do bairro, altitude). Gera texto estruturado com os mesmos dados que seriam enviados à IA. Ativada automaticamente quando:
- `NVIDIA_API_KEY` não está definida no `.env`, ou
- todos os modelos da cadeia 1→2 lançam exceção (API fora do ar, quota esgotada, timeout)

Benchmark realizado em maio/2025 com prompt de contexto climático em PT-BR:

| Modelo | Latência | Qualidade |
|---|---|---|
| Nemotron Super 49B | 3.9s | **Melhor** — cita "inundação predial e viária", mais técnico |
| Llama 3.3 70B | 5.4s | Bom — resposta mais genérica |
| Mixtral 8x22B | 1.3s | Rápido — resposta mais superficial |
| DeepSeek R1/V3 | — | 404 nesta chave NVIDIA NIM — não disponível |

```json
{ "explanation": "**Por que 47 pts em Paissandu?**\n\n🌊 **Maré 2.9m...**", "score": 47, "nivel": "MODERADO" }
```

### `POST /api/narrative`
4 frases estilo Defesa Civil: diagnóstico, rua específica a evitar, janela de tempo, ação concreta.

#### Cadeia de fallback (4 tentativas)

```
Tentativa 1: NVIDIA NIM — nvidia/llama-3.3-nemotron-super-49b-v1
             ↓ falha
Tentativa 2: NVIDIA NIM — meta/llama-3.3-70b-instruct
             ↓ ambos falham
Tentativa 3: Gemini 1.5 Flash
             ↓ falha ou sem GEMINI_API_KEY
Tentativa 4: fallback local Python — dados reais, sem mock, sem API externa
```

O endpoint retorna `{ "narrative": "...", "model_used": "Nemotron 49B" | "Llama 70B" | "Gemini Flash" | "local" }`. O frontend exibe o modelo real no badge "IA · [modelo]".

**Prompt:** proíbe explicitamente linguagem condicional ("se chover", "caso chova") e numeração de itens. Usa dados reais de chuva prevista como fatos, não hipóteses. Fallback local usa janela de tempo dinâmica baseada em `rain_next_24h_mm`.

**APAC no contexto:** quando há boletim oficial ativo (`nivel ≠ SEGURO`), o campo é injetado no prompt. A IA usa o alerta para calibrar tom e urgência.

**Escala de referência calibrada para Recife (chuva em 24h):**
A IA recebe interpretações plain-text de cada métrica — calibradas para não exagerar:

| Volume (24h) | Descrição injetada no prompt | Efeito real esperado |
|---|---|---|
| < 5mm | chuva fina — algumas poças em calçadas, trânsito normal | Piso molhado |
| 5–15mm | chuva fraca a moderada — piso molhado, possíveis poças em pontos baixos | Poças, nenhum alagamento |
| 15–25mm | chuva moderada — atenção nos trechos baixos e canais | Lentidão em pontos baixos |
| 25–40mm | chuva forte — risco de acúmulo em baixadas | Bocas de lobo sobrecarregadas |
| 40mm+ | chuva muito forte — alagamento provável | Alagamento pontual |

> **Regra de ouro:** a IA só usa "bocas de lobo lotadas", "alagamento" ou "inundação" se `rain > 25mm` **OU** APAC ALTO/SEVERO **OU** reports da comunidade confirmam ocorrência. Com 6mm, a resposta correta é "piso molhado, algumas poças" — nada mais.

**Métricas multi-fonte:** vento, umidade, pressão e visibilidade vêm de Open-Meteo + OWM + INMET (média consensual). A IA cita ao menos uma fonte na frase 1.

**Ruas específicas:** `_PONTOS` em `ai_narrative.py` mapeia 25 bairros com pontos de atenção nomeados (ex.: Santo Amaro → "Canal da Tacaruna, Rua das Ninfas, Av. Dantas Barreto"). Bairros sem mapeamento caem no fallback genérico — não deveria acontecer para bairros da RMR.

**Narrativa de rota IA:** `POST /api/route-risk` retorna `narrative` (3 frases IA) e `model_used`. A narrativa é **específica para o modal** — carro (risco de alagamento de via), bike (piso escorregadio, visibilidade), a pé (calçadas inundadas). O nível APAC é explicado em português, não exibido como sigla.

**Fontes de ocorrências em tempo real disponíveis para Recife:**
- **APAC Geoportal** (integrado) — 35 estações pluviométricas em tempo real; melhor fonte pública disponível
- **INMET alertas** (integrado) — avisos meteorológicos oficiais para PE com score_bonus por severidade
- **Reports da comunidade** (integrado) — confirmados por outros usuários, filtrados por raio 2km
- **Cemaden** (`alertas2.cemaden.gov.br/api/`) — alertas municipais de desastres naturais; possível integração futura
- **AlertaRec (Defesa Civil Recife)** — não possui API pública aberta

### `POST /api/reports` / `GET /api/reports/nearby` / `POST /api/reports/{id}/confirm`
Ocorrências da comunidade. Tipos: alagamento, deslizamento, queda_arvore, via_intransitavel, poste_caido, outro.

**Duração no mapa:** reports aparecem em `/api/reports/nearby` por até **24 horas**, desde que `resolved = false`. A query pública busca apenas ocorrências recentes e não resolvidas:

- `created_at >= agora - 24h`;
- `resolved = false`;
- dentro do raio solicitado, por padrão **2km**;
- dentro dos limites geográficos aceitos para Recife.

**Rate limit:** o backend permite **1 report a cada 5 minutos por IP hasheado**. Se o usuário tentar reportar novamente nesse intervalo, a API retorna `429` com a mensagem `Aguarde 5 minutos entre reports.`.

**Proximidade obrigatória:** o report precisa estar a até **1,5 km da localização GPS atual** enviada pelo navegador (`user_lat`, `user_lon`). O usuário pode reportar exatamente onde está ou escolher um endereço próximo; reports mais distantes são rejeitados com erro `400`. Essa regra reduz marcações falsas em bairros distantes.

**Alertas comunitários:** quando existem 3+ reports do mesmo tipo no mesmo bairro dentro de 1 hora, `alerts_engine.py` cria um alerta comunitário; com 5+ reports, o alerta vira severo. Esses alertas expiram em 2 horas.

### `POST /api/scores`
Hydra Score em lote (até 6 bairros simultâneos via `asyncio.gather`).

### `POST /api/route-risk`
Análise de risco de trajeto com **geocodificação de endereços livres** (Nominatim) + rota OSRM + convergência de 4 fontes de dados em tempo real.

```json
{
  "origem_lat": -8.1188, "origem_lon": -34.8942,
  "destino_lat": -8.0628, "destino_lon": -34.8773,
  "perfil": "cycling-regular",
  "rain_next": 12.5,
  "origem_nome": "Av. Boa Viagem, 1000",
  "destino_nome": "Cais José Estelita"
}
```

**Resposta:**
```json
{
  "risk_score": 54, "risk_level": "MEDIO",
  "distance_km": 8.3, "duration_min": 22,
  "apac_nivel": "MODERADO",
  "hazards": [
    { "type": "ponto_critico_historico", "name": "Canal do Pina", "severity": "moderado", "source": "Defesa Civil PE" },
    { "type": "chuva_ativa_apac", "name": "[CEMADEN] Pina", "hora_1_mm": 6.2, "severity": "leve", "source": "APAC Geoportal (tempo real)" }
  ],
  "active_alerts": [{ "fonte": "INMET", "evento": "Chuva intensa", "severidade": "Laranja", "score_bonus": 18 }],
  "route_coords": [[-8.1188, -34.8942], ...],
  "narrative": "Trajeto de bike com risco MÉDIO agora — score 54/100, APAC MODERADO e 12.5mm previstos.\nAtenção no Canal do Pina: alagamento registrado 9 de 10 temporadas; estação CEMADEN Pina detectou 6.2mm na última hora.\nSiga com cautela; evite orla se chover — canal extravasa acima de 30mm/h.",
  "model_used": "Gemini Flash"
}
```

**Convergência de dados (busca em paralelo `asyncio.gather`):**
1. **OSRM (multi-endpoint)** — rota real pelo OpenStreetMap com servidor específico por modal:
   - Carro → `routing.openstreetmap.de/routed-car`
   - Bike → `routing.openstreetmap.de/routed-bike`
   - A pé → `routing.openstreetmap.de/routed-foot`
   Cada um retorna geometria, distância e duração reais para o modal — sem simular.
2. **APAC Geoportal ArcGIS** — 35 estações pluviométricas RMR com leituras 1h/6h/24h; estações dentro de **1.5km** da rota viram hazards em tempo real
3. **INMET alertas** — avisos meteorológicos ativos filtrados para PE
4. **APAC boletim** — nível de alerta atual (SEGURO → SEVERO) aplicado como bônus no score

**Pontos históricos (Defesa Civil PE / APAC 2018-2024):** 30 pontos críticos mapeados em `_PONTOS_CRITICOS`, cobrindo todas as zonas da RMR — Boa Viagem (Shopping Recife, Canal dos Setúbal, Av. Domingos Ferreira), Santo Amaro (Canal da Tacaruna), Derby/Boa Vista (Praça do Derby, viaduto Agamenon), Norte (Morro da Conceição, Arruda), Afogados/Tejipió, Jordão/Ibura. Raio de detecção: **0.6km** por ponto amostrado na rota.

**Narrativa IA por modal:** prompt inclui `modo_contexto` (ex.: "Bicicleta: piso molhado/escorregadio, visibilidade baixa") e `apac_desc` em português (ex.: "atenção preventiva — chuva possível"). A narrativa e o fallback local variam por modal.

**Score breakdown (multiplicado pelo modal):**
- Pontos críticos históricos: +8 por ponto dentro de 0.6km da rota
- Estações APAC com chuva ativa: +5/15/30 (leve/moderado/grave)
- Chuva prevista (Open-Meteo): +5/10/20 (>2/>8/>20 mm/24h)
- APAC boletim: 0/5/12/25/35 (SEGURO → SEVERO)
- INMET alertas PE: soma dos bônus, cap 30
- Multiplicador modal: carro ×1.0 · bike ×1.5 · a pé ×1.8

### `WebSocket /ws/{bairro}`
Conexão persistente que envia o JSON completo do dashboard a cada **5 minutos** sem custo de IA — usa a mesma lógica de `fetch_dashboard()` do endpoint REST, aproveitando o cache Open-Meteo (15 min). O frontend reconecta automaticamente com backoff exponencial (2s → 4s → ... → 60s máx).

Não consome tokens de IA. O WebSocket serve apenas dados meteorológicos em JSON.

### `GET /api/push/vapid-public-key`
Retorna a chave pública VAPID para o frontend registrar o service worker de push.

### `POST /api/push/subscribe` · `DELETE /api/push/subscribe`
Salva ou remove uma assinatura de push na tabela `push_subscriptions` do Supabase. O backend grava o schema normalizado:

- `ip_hash` — hash do IP, sem armazenar IP bruto;
- `endpoint` — endpoint único do Push API;
- `p256dh` e `auth` — chaves públicas do navegador.

As notificações push são disparadas quando o backend cria um alerta comunitário por cluster de reports (`alerts_engine.py`). O envio usa VAPID (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_EMAIL`) e cai para armazenamento em memória se o Supabase não estiver configurado.

**Como a notificação aparece:** é uma notificação do navegador/sistema operacional com título `HydraRec — {bairro}` e corpo `Risco {NIVEL} detectado: {score}/100. Verifique o app.`. No Android aparece pela permissão do Chrome/PWA; no desktop aparece na central de notificações; no iOS exige PWA instalado na tela inicial e permissão concedida.

**Quem recebe hoje:** todo navegador que clicou no sino e registrou assinatura em `push_subscriptions`. A tabela já possui `bairro` e `min_severity`, mas o filtro por bairro/severidade ainda é evolução futura; no estado atual, o broadcast é global para assinantes.

**Teste manual protegido:** `POST /api/push/test` dispara uma notificação de teste apenas se `PUSH_TEST_TOKEN` estiver configurado e o request enviar o header `X-Push-Test-Token`. Resposta `{ "sent": N }`, onde `N` é o número de assinaturas que receberam envio com sucesso.

### `GET /api/forecast/{bairro}`
Previsão do Hydra Score para as próximas 6 horas com **consenso multi-fonte real**.

```json
{
  "bairro": "Boa Viagem",
  "forecast": [
    {
      "time": "2026-05-14T15:00:00",
      "score": 43, "nivel": "ATENCAO",
      "precip_om_mm": 1.2, "precip_owm_mm": 0.9,
      "confidence": "ALTA", "sources": 3
    }
  ]
}
```

**Como funciona:**
Para cada slot horário (próximas 6h), o endpoint busca em paralelo:
- **Open-Meteo** — precipitação horária (`hourly.precipitation`)
- **OpenWeatherMap** — blocos de 3h distribuídos em por-hora via `fetch_owm_hourly_slots()`
- **INMET** — baseline da última observação da estação mais próxima (A301 Recife / A357 Olinda)

A função `_consensus_for_slot()` calcula:
- `rain_next_24h_mm` = média(OM janela 6h, OWM janela 6h)
- `rain_past_24h_mm` = média(OM acumulado 24h, INMET × 6 se disponível)
- `confidence` = ALTA (stdev < 1mm) / MEDIA / BAIXA

O score final é calculado pela fórmula real `calculate_risk_score_v2()` — sem modelos sintéticos, sem aproximações.

> Observação de UI: esse endpoint segue disponível para API/debug, mas o bloco visual `RiskForecast` foi removido da sidebar para reduzir ruído e priorizar a análise contextual principal.

**Cache:** 5 minutos por bairro.

### `GET /api/apac/boletim`
Busca o boletim meteorológico oficial da APAC (Agência Pernambucana de Águas e Clima).

```json
{
  "boletim": {
    "nivel": "ALTO",
    "titulo": "Chuvas Fortes Previstas para a RMR",
    "texto": "...",
    "url": "https://apac.pe.gov.br/...",
    "coletado_em": "2026-05-14T14:00:00"
  }
}
```

**Cadeia de busca (3 tentativas):**
1. JSON API oficial: `apac.pe.gov.br/json/alertas.json`
2. HTML scraping da página `/meteorologia` com BeautifulSoup4
3. Retorna `{"boletim": null}` em silêncio (sem erro 500)

**Classificação de nível por palavras-chave:**
`chuva muito forte` → SEVERO · `chuva forte` → ALTO · `chuva moderada` → MODERADO · `chuva fraca` → ATENCAO · default → SEGURO

**Cache:** 30 minutos. SSL desativado (`verify=False`) para compatibilidade com o cert da APAC.

---

## Mapa e leitura dos bairros

O mapa do frontend usa **Leaflet** com tiles CartoDB/OpenStreetMap e duas camadas geográficas:

1. **Limite oficial do bairro selecionado** — camada principal de risco no mapa.
2. **Pontos críticos e reports da comunidade** — marcadores pontuais sobrepostos.

### Fonte do GeoJSON dos bairros

O limite dos bairros foi retirado do Portal de Dados Abertos da Prefeitura do Recife:

- Dataset: **Mapas de limites e divisões territoriais**
- Recurso: **Limites dos Bairros - 2023**
- Formato: **GeoJSON**
- Fonte oficial: Prefeitura do Recife
- Licença indicada no portal: **Open Data Commons Open Database License (ODbL)**
- Página do recurso: `https://dados.recife.pe.gov.br/dataset/mapas-de-limites-e-divisoes-territoriais/resource/d5f956ce-7e1f-4c74-839b-06cb490c3721`
- Download direto usado no projeto: `https://dados.recife.pe.gov.br/dataset/ce23d3f4-7474-44d3-b310-19f3afcebf4a/resource/d5f956ce-7e1f-4c74-839b-06cb490c3721/download/limites-dos-bairros-2023.geojson`

Arquivo versionado no frontend:

```text
front_end_hydrarec/src/data/geo/recife_bairros_2023.geojson
```

### Como o mapa escolhe o bairro

No componente `front_end_hydrarec/src/components/map/HydraMap.jsx`:

1. O GeoJSON oficial é carregado como asset do Vite (`?url`), sem embutir os 2 MB dentro do JavaScript principal.
2. O nome do bairro selecionado é normalizado: remove acentos, converte para maiúsculas e compara com `EBAIRRNOMEOF`/`EBAIRRNOME` do GeoJSON.
3. Se houver correspondência, o Leaflet desenha o polígono oficial com `L.geoJSON(feature)` e usa `fitBounds()` para centralizar/enquadrar o bairro real.
4. O preenchimento do polígono usa a cor do nível do Hydra Score (`SEGURO`, `ATENCAO`, `MODERADO`, `ALTO`, `SEVERO`).
5. Se o bairro não existir no GeoJSON, o app cai no fallback antigo de coordenada central (`BAIRRO_COORDS`) e desenha um círculo aproximado.

Na geolocalização automática (`front_end_hydrarec/src/App.jsx`), o app também usa esse mesmo GeoJSON oficial: o ponto GPS do usuário é testado dentro dos polígonos dos bairros. Isso evita o erro antigo de escolher o bairro pelo centro mais próximo, que podia retornar Paissandu mesmo quando a bolinha azul estava dentro de outro bairro real. A busca por Haversine em `BAIRRO_COORDS` ficou apenas como fallback se o GeoJSON não carregar ou se o ponto estiver fora da área mapeada.

### Aliases de nomes

Alguns nomes usados na interface não são idênticos aos nomes oficiais do GeoJSON. O mapa resolve isso com aliases em `HydraMap.jsx`, por exemplo:

| Nome no app | Nome no GeoJSON oficial |
|---|---|
| Recife Antigo | Recife |
| Cohab | Cohab - Ibura de Cima |
| Zumbi do Pacheco | Zumbi |
| Pau-Ferro | Pau Ferro |
| Sítio dos Pintos | Sítio dos Pintos - São Brás |

### Observação importante para apresentação

Antes desta integração, o mapa usava apenas pontos centrais aproximados em `bairro_coords.js`. Isso podia deslocar visualmente a área de risco e também errar a seleção automática por GPS, porque bairros são polígonos irregulares, não pontos. A versão atual usa o **limite oficial do bairro** quando disponível; as coordenadas manuais ficaram apenas como fallback para busca e casos sem correspondência.

---

## LGPD e segurança

O HydraRec foi desenhado para funcionar como sistema cívico de alerta sem coletar identidade direta do morador. A lógica é: receber dados úteis para risco climático, mas evitar armazenar dado pessoal identificável quando ele não é necessário.

### Dados pessoais e anonimização

| Dado | Como é tratado |
|---|---|
| IP do usuário | **Não é armazenado em texto puro.** O backend gera hash SHA-256 com `IP_HASH_SALT`. |
| GPS do report | Usado para localizar a ocorrência no mapa. É obrigatório para validar proximidade, mas não identifica nome/CPF/e-mail. |
| Nome, e-mail, telefone, CPF | **Não são solicitados** no fluxo de report. |
| Foto do report | Opcional. O sistema deve funcionar sem foto. |
| Confirmações de report | Associadas ao report e ao controle anti-abuso, não a uma conta nominal. |

### Hash de IP

Implementação:

```text
back_end_hydrarec/services/security.py
```

O IP é combinado com `IP_HASH_SALT` e convertido para SHA-256. Isso permite aplicar rate limit e reduzir abuso sem salvar o endereço original.

Variável obrigatória no `.env`:

```env
IP_HASH_SALT=string_aleatoria_longa
```

Esse salt não deve ser commitado. Sem o salt, hashes ficam previsíveis e mais fáceis de correlacionar.

### Rate limit de reports

Implementação:

```text
back_end_hydrarec/services/rate_limit.py
back_end_hydrarec/routers/reports.py
```

Regra atual: **1 report a cada 5 minutos por IP hasheado**.

Objetivo:

- evitar spam;
- reduzir reports falsos em massa;
- preservar anonimato operacional;
- manter o painel útil para Defesa Civil/moradores.

### Chaves e segredos

As chaves sensíveis ficam somente no backend:

```env
SUPABASE_SERVICE_KEY=
GEMINI_API_KEY=
NVIDIA_API_KEY=
OPENWEATHER_KEY=
IP_HASH_SALT=
```

O frontend nunca deve receber `SUPABASE_SERVICE_KEY` nem chaves server-side. O `.env` está fora do versionamento e o `.env.example` deve conter apenas valores falsos.

### Supabase e RLS

O banco usa Supabase/PostgreSQL com RLS nas tabelas sensíveis. A `SUPABASE_SERVICE_KEY` fica restrita ao backend para operações administrativas. O frontend conversa com o backend por endpoints REST; ele não deve escrever diretamente em tabelas protegidas.

### Localização no navegador

O navegador pode pedir geolocalização para:

- sugerir o bairro mais próximo;
- posicionar o report no mapa;
- carregar ocorrências próximas.

Se o usuário negar permissão, o app usa o bairro selecionado manualmente e coordenadas de fallback do bairro quando necessário. A localização exata não é usada para cadastro nominal.

### Pontos importantes para apresentação

- O app não pede login para reportar.
- O app não pede nome, CPF, telefone ou e-mail.
- O IP é usado apenas como hash para anti-abuso.
- A localização é coletada para contexto espacial da ocorrência, não para identificação civil.
- O backend centraliza segredos e integrações externas; o frontend não expõe chaves sensíveis.

---

## Hydra Score v2

Score 0–100 com 7 componentes:

| Componente | Máx | Ativação |
|---|---|---|
| Chuva prevista 24h | **35 pts** | Sempre — curva logística `35*(1−e^(−mm/22))` |
| Chuva acumulada 24h | **10 pts** | Só com chuva ativa — curva logística menor |
| Maré | **10 pts** | Com chuva; sem chuva conta só 20% |
| Vulnerabilidade do bairro | **8 pts** | **Só com chuva ≥ 1mm** |
| Altitude baixa | **4/2 pts** | **Só com chuva ≥ 1mm** |
| Instabilidade atmosférica | 6/3 pts | **Só com chuva ≥ 1mm** |
| Reports da comunidade | 10 pts | **Sempre** |

**Decisão de design:** sem chuva, o score fica ≈0 mesmo em bairros vulneráveis. Vulnerabilidade e altitude são *amplificadores* do risco pluvial, não fontes autônomas. Corrige o problema "ATENÇÃO com sol".

**Nerf v2.2 (2026-05):** chuva prevista foi reduzida de 50→35 pts, maré de 15→10 pts e vulnerabilidade de 12×→8×. A leitura fica menos alarmista quando só há chuva leve ou maré alta isolada; extremos continuam chegando em ALTO/SEVERO quando chuva, reports e fontes oficiais concordam.

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
│   ├── forecast.py      # /api/forecast/{bairro} — consenso multi-fonte 6h
│   ├── apac.py          # /api/apac/boletim — boletim oficial APAC
│   └── healthz.py
├── services/
│   ├── risk_score.py    # Hydra Score v2
│   ├── ai_narrative.py  # Gemini 1.5 Flash
│   ├── ai_explain.py    # NVIDIA NIM Nemotron (+ cache 5min)
│   ├── apac_scraper.py  # APAC boletim: JSON API → HTML scraping → None (cache 30min)
│   ├── heat_index.py    # Steadman-NOAA
│   ├── traffic.py       # multiplicador chuva/hora
│   ├── routing.py       # OpenRouteService + haversine
│   ├── rate_limit.py    # anti-spam IP hasheado
│   ├── security.py      # SHA-256 + salt
│   └── weather/
│       ├── open_meteo.py  # dados horários e diários
│       ├── owm.py         # OpenWeatherMap 3h blocks → slots por hora
│       ├── inmet.py       # estações A301/A357 (timeout 2.5s, falha silenciosa)
│       ├── fusion.py      # consenso multi-fonte: média, stdev, badge confiança
│       └── tides.py       # scraping FEMAR
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
| OpenWeatherMap | 15 min | `services/cache.py` (via `_fetch_owm_raw`) |
| INMET A301/A357 | 30 min | `services/cache.py` + stale cache quando API cai |
| APAC boletim | 30 min | `services/cache.py` |
| Maré FEMAR | 1 hora | `services/cache.py` |
| Explicação IA | **5 min** | dict em memória `ai_explain.py` |
| Narrativa IA | Sem cache | Dados mudam por minuto |

---

## Resiliência

Todos os endpoints que fazem múltiplas chamadas externas simultâneas usam `asyncio.gather(..., return_exceptions=True)`. Se um serviço falhar (Open-Meteo, elevation API, tides), o endpoint retorna dados parciais com fallbacks em vez de 400:

| Serviço | Fallback |
|---|---|
| `fetch_weather_consensus` | zeros de chuva, confiança BAIXA |
| `fetch_elevation` | 10.0 m (neutro no Hydra Score) |
| `scrape_tide_data` | `{"height": 1.5, "trend": "Desconhecido"}` |
| `fetch_inmet_nearest` | cache expirado da estação mais próxima, se existir |

`inmet.py` usa primeiro `https://apitempo.inmet.gov.br/estacao/dados/{codigo}/{YYYY-MM-DD}` para A301/A357. Se a API do INMET cair, o sistema usa o último dado em cache como leitura stale e mantém o dashboard funcionando.

`tides.py`, `inmet.py` e `fusion.py` têm proteção interna própria; a proteção no `asyncio.gather` é uma segunda camada.

---

## Testes

```bash
pytest tests/ -v   # 9 passed
```

Casos críticos: `test_jordao_13mm_nao_e_seguro` (regressão bug v1) · `test_dia_de_sol_score_baixo` · `test_score_nao_ultrapassa_100`
