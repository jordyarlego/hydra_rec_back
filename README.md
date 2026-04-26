# hydra.rec — Backend

API do sistema **HydraRec** de alerta de risco climático para os bairros do Recife, PE.  
Agrega dados de satélite, tábua de marés, altitude e histórico de enchentes para calcular o **Hydra Score** (0-100) por bairro, e gera boletins de Defesa Civil via Gemini.

---

## Endpoints

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/api/dashboard/{bairro}` | Retorna clima atual, Hydra Score e previsão 6h |
| `POST` | `/api/narrative` | Gera boletim IA de Defesa Civil via Gemini |

### Exemplo de resposta — `/api/dashboard/Boa Viagem`

```json
{
  "location": { "name": "Boa Viagem", "latitude": -8.118, "longitude": -34.9 },
  "weather": { "current": { "temperature_2m": 28.5, "precipitation": 0, ... } },
  "risk": {
    "score": 38,
    "nivel": "MODERADO",
    "rawValues": {
      "chuvaPrevista": 12.0,
      "chuva24h": 5.0,
      "mareAltura": 2.1,
      "mareTrend": "Alta",
      "saturacaoSolo": 0.45,
      "altitude": 2,
      "uvIndex": 5,
      "pressao": 1012,
      "rajadaVento": 18.0
    }
  },
  "forecast6h": [
    { "time": "2025-04-25T14:00", "temperature": 29.1, "precipitation": 0.2, "weather_code": 61 }
  ]
}
```

---

## Stack

| Camada | Tecnologia |
|--------|-----------|
| Framework | FastAPI |
| Runtime | Python 3.9+ |
| HTTP Client | httpx (async) |
| HTML Parsing | BeautifulSoup4 |
| IA | Google Generative AI (Gemini) |
| Dados climáticos | Open-Meteo (free, sem chave) |
| Tábua de marés | Scraping — tabuademares.com |

---

## Pré-requisitos

- Python **3.9** ou superior
- Chave de API do **Google AI Studio** (Gemini)  
  → [aistudio.google.com](https://aistudio.google.com)

---

## Instalação e execução

```bash
# 1. Clone o repositório
git clone https://github.com/jordyarlego/hydra_rec_back.git
cd hydra_rec_back

# 2. Crie e ative o ambiente virtual
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Instale as dependências
pip install -r requirements.txt

# 4. Configure as variáveis de ambiente (veja abaixo)
cp .env.example .env

# 5. Rode o servidor
uvicorn main:app --reload
```

API disponível em `http://localhost:8000`  
Documentação automática em `http://localhost:8000/docs`

---

## Variáveis de ambiente

Crie um arquivo `.env` na raiz do projeto:

```env
GEMINI_API_KEY=sua_chave_aqui
ALLOWED_ORIGINS=http://localhost:5173
```

| Variável | Obrigatório | Descrição |
|----------|-------------|-----------|
| `GEMINI_API_KEY` | Sim | Chave do Google AI Studio |
| `ALLOWED_ORIGINS` | Não | Origins permitidos no CORS (padrão: `http://localhost:5173`) |

> O `.env` nunca deve ser commitado. Ele está no `.gitignore`.

---

## Algoritmo Hydra Score

O score (0–100) é calculado com base em 5 componentes:

| Componente | Peso máx | Fonte |
|------------|----------|-------|
| Chuva prevista (próx 24h) | 35 pts | Open-Meteo forecast |
| Chuva acumulada (últimas 24h) | 20 pts | Open-Meteo histórico |
| Tábua de marés | 15 pts | tabuademares.com (scraping) |
| Saturação hídrica do solo | 10 pts | Open-Meteo soil moisture |
| Vulnerabilidade histórica do bairro | 10 pts | Índice estático por bairro |
| Altitude (bônus para bairros baixos) | +10 pts | Open-Meteo elevation |

**Níveis de risco:**
- `BAIXO` — 0 a 29
- `MODERADO` — 30 a 59
- `ALTO` — 60 a 79
- `SEVERO` — 80 a 100

---

## Estrutura do projeto

```
back_end_hydrarec/
├── main.py          # Toda a lógica: endpoints, algoritmo, scraping, cache, IA
├── requirements.txt # Dependências Python
├── .env             # Variáveis locais (não commitado)
└── .env.example     # Template do .env
```

---

## Cache interno

Para evitar requests desnecessários a APIs externas:

| Dado | TTL |
|------|-----|
| Meteorologia (Open-Meteo) | 15 minutos |
| Tábua de marés (scraping) | 1 hora |

O cache é em memória (reinicia com o servidor). Para produção, substituir por Redis.

---

## Coordenadas dos bairros

Todos os 87 bairros do Recife têm coordenadas reais mapeadas estaticamente em `main.py` — sem depender de geocoding externo, que não reconhece a maioria dos bairros.
