# HydraRec — Deploy no Render

O jeito mais simples é subir **somente o repositório do backend** (`hydra_rec_back`) no Render.

Por quê: o build do frontend já está dentro de `back_end_hydrarec/static/`, e o FastAPI serve API + site em um serviço só. Não precisa deploy separado do front.

## Antes do deploy

1. No frontend local, rode:

```bash
cd ../front_end_hydrarec
npm run build
```

Isso atualiza `back_end_hydrarec/static/`.

2. No backend, valide:

```bash
cd ../back_end_hydrarec
venv/bin/python -m pytest -q
```

## Banco Supabase

Rode o SQL completo:

```text
supabase_schema.sql
```

Se você já rodou a versão anterior, a principal diferença é que esta versão remove Telegram da documentação ativa e garante RLS em `reputation`. A tabela antiga `telegram_subscribers`, se já existir, não atrapalha o sistema; ela só fica sem uso.

## Criar serviço no Render

1. Entre em `https://render.com`.
2. Clique em **New +**.
3. Escolha **Blueprint** se o Render detectar `render.yaml`, ou **Web Service** se preferir manual.
4. Conecte o repositório:

```text
https://github.com/jordyarlego/hydra_rec_back
```

5. Se for manual, configure:

```text
Runtime: Python
Build Command: pip install -r requirements.txt
Start Command: uvicorn main:app --host 0.0.0.0 --port $PORT
Health Check Path: /api/healthz
```

## Variáveis de ambiente

Obrigatórias:

```env
SUPABASE_URL=
SUPABASE_KEY=
SUPABASE_SERVICE_KEY=
IP_HASH_SALT=
ALLOWED_ORIGINS=https://SEU-APP.onrender.com
```

Recomendadas:

```env
GEMINI_API_KEY=
NVIDIA_API_KEY=
OPENWEATHER_KEY=
VAPID_PUBLIC_KEY=
VAPID_PRIVATE_KEY=
VAPID_EMAIL=mailto:seu-email@dominio.com
PUSH_TEST_TOKEN=
```

Sem `OPENWEATHER_KEY`, o app ainda usa Open-Meteo e INMET. Sem chaves de IA, alguns fluxos usam fallback local.

## Push

Para gerar VAPID localmente:

```bash
cd back_end_hydrarec
venv/bin/python -m py_vapid --gen --json
```

Depois coloque as chaves no Render.

Para testar push em produção:

1. Abra o app no domínio Render.
2. Clique no sino de notificações e permita.
3. Rode:

```bash
curl -X POST https://SEU-APP.onrender.com/api/push/test \
  -H "X-Push-Test-Token: SEU_PUSH_TEST_TOKEN"
```

Resposta esperada:

```json
{ "ok": true, "sent": 1 }
```

`sent: 0` significa que não há navegador inscrito ou VAPID não está configurado.

## Testes após deploy

Abra:

```text
https://SEU-APP.onrender.com/
https://SEU-APP.onrender.com/api/healthz
https://SEU-APP.onrender.com/api/dashboard/Boa Viagem
https://SEU-APP.onrender.com/api/reports/nearby?lat=-8.1195&lon=-34.9008&radius=2000
https://SEU-APP.onrender.com/api/apac/boletim
```

`/api/healthz` pode mostrar IA, OpenWeather ou Push como `not_configured`. Para deploy mínimo, `open_meteo` e `supabase` precisam estar `ok`.
