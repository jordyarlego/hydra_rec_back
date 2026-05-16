# HydraRec V3 — Deploy no Render

O jeito mais simples é subir um serviço único no Render: FastAPI serve a API e os estáticos gerados pelo Vite.

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

Antes do deploy, rode no Supabase SQL Editor:

```text
back_end_hydrarec/migrations/v3_civic_reports.sql
back_end_hydrarec/migrations/v3_official_data_hub.sql
```

Crie também o bucket Storage `report-photos` como público para leitura. Upload continua sendo feito apenas pelo backend com `SUPABASE_SERVICE_KEY`.

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
SUPABASE_KEY=                 # anon/publishable
SUPABASE_SERVICE_KEY=
SUPABASE_STORAGE_BUCKET=report-photos
IP_HASH_SALT=
ALLOWED_ORIGINS=https://SEU-APP.onrender.com
```

Recomendadas:

```env
GEMINI_API_KEY=               # foto + validação IA
NVIDIA_API_KEY=               # narrativa, opcional
SUPABASE_JWT_SECRET=          # opcional; fallback valida no Supabase Auth
VAPID_PUBLIC_KEY=
VAPID_PRIVATE_KEY=
VAPID_EMAIL=mailto:seu-email@dominio.com
PUSH_TEST_TOKEN=
```

V3 usa apenas os JSONs oficiais APAC para clima operacional. Não configure `OPENWEATHER_KEY`; está deprecado. Sem chaves de IA, fluxos usam fallback local/heurístico.

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
https://SEU-APP.onrender.com/api/weather?lat=-8.1195&lon=-34.9008
https://SEU-APP.onrender.com/api/reports/nearby?lat=-8.1195&lon=-34.9008&radius=2000
https://SEU-APP.onrender.com/api/apac/boletim
https://SEU-APP.onrender.com/admin
```

`/api/healthz` pode mostrar IA ou Push como `not_configured`. Para deploy mínimo, `supabase`, `storage` e `apac` precisam estar operacionais.
