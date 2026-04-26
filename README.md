# HydraRec — Sistema de Alerta Climático Hiperlocal

Dashboard de risco de alagamento para os **91 bairros do Recife, PE**.  
Agrega dados meteorológicos de satélite, tábua de marés, altitude e histórico de enchentes para calcular o **Hydra Score** (0–100) por bairro, com boletins de Defesa Civil gerados pelo Gemini.

---

## Como rodar

```bash
# Dentro de back_end_hydrarec/
source venv/bin/activate        # Windows: venv\Scripts\activate
uvicorn main:app --reload
```

Acesse `http://localhost:8000` — o dashboard abre direto no navegador.  
Documentação automática da API em `http://localhost:8000/docs`.

---

## Variáveis de ambiente

Crie `.env` na raiz do projeto:

```env
GEMINI_API_KEY=sua_chave_aqui
ALLOWED_ORIGINS=http://localhost:8000
```

---

## Endpoints

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/` | Serve o dashboard (index.html) |
| `GET` | `/api/dashboard/{bairro}` | Clima atual, Hydra Score, previsão 6h e diária |
| `POST` | `/api/narrative` | Boletim em linguagem natural via Gemini 2.5 Flash |
| `POST` | `/api/scores` | Scores de múltiplos bairros em paralelo |
| `GET` | `/manifest.json` | PWA manifest |
| `GET` | `/sw.js` | Service worker (cache offline) |
| `GET` | `/icon.svg` | Ícone do app |

---

## Algoritmo Hydra Score

| Componente | Peso máx | Fonte |
|------------|----------|-------|
| Chuva prevista (próx. 24h) | 35 pts | Open-Meteo forecast |
| Chuva acumulada (últ. 24h) | 20 pts | Open-Meteo histórico |
| Tábua de marés | 15 pts | tabuademares.com (scraping) |
| Saturação hídrica do solo | 10 pts | Open-Meteo soil moisture |
| Vulnerabilidade histórica | 10 pts | Índice estático por bairro |
| Altitude (bônus baixos) | +10 pts | Open-Meteo elevation |

**Níveis:** BAIXO (0–29) · MODERADO (30–59) · ALTO (60–79) · SEVERO (80–100)

---

## Stack

| Camada | Tecnologia |
|--------|-----------|
| Backend | FastAPI + Python 3.13 |
| IA | Gemini 2.5 Flash (google-genai SDK) |
| Clima | Open-Meteo (gratuito, sem chave) |
| Marés | Scraping — tabuademares.com |
| Frontend | React 18 via CDN + Babel Standalone (sem build step) |
| PWA | manifest.json + service worker |

---

## Estrutura

```
back_end_hydrarec/
├── main.py           # Backend completo: API, algoritmo, scraping, IA
├── requirements.txt
├── .env              # Não commitado
└── static/
    ├── index.html    # Frontend React (self-contained)
    ├── manifest.json # PWA
    ├── sw.js         # Service worker
    └── icon.svg      # Ícone do app
```

---

## Cache

| Dado | TTL |
|------|-----|
| Meteorologia (Open-Meteo) | 15 min |
| Tábua de marés | 1 hora |
