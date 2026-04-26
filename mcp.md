# Backend HydraRec MCP

Este arquivo documenta a arquitetura de processamento e raspagem oficial em Python (FastAPI).

## 1. Coleta de Dados Orgânicos Integrados
- O Backend abandona qualquer tipo de "mock".
- **Raspagem Real de Maré**: Usamos `BeautifulSoup` para ler a DOM ao vivo (`.tabla_mareas_marea_altura_numero`) do `tabuademares.com`, extraindo a métrica literal flutuante do mar do Recife.
- **Satélite Avancado (INMET/INPE proxied via OpenMeteo)**: Incorporamos agora os nós críticos:
   - Pressão Atmosférica
   - Índice de UV
   - Umidade do Solo Profundo
   - Rajadas de Vento (Gusts)
   Esses dados formam o verdadeiro score de saturação pesada!

## 2. API Gateway
- Rota: `/api/dashboard/{bairro}`
- A rota engloba um pipeline inteiro de dados: `Geocoding -> Meteorologia Satélite -> Maré Local -> Algoritmo Hydra -> Output JSON`.
