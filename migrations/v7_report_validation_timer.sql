-- v7: validação cidadã com deadline
-- Reports novos entram em validação por uma janela curta. Um worker fecha o
-- ciclo com regra determinística usando votos, confirmações, IA e APAC/CEMADEN.

alter table public.reports
  add column if not exists validation_deadline timestamptz,
  add column if not exists validation_verdict text,
  add column if not exists validation_score int,
  add column if not exists validation_summary text,
  add column if not exists validated_at timestamptz;

alter table public.reports drop constraint if exists reports_status_check;
alter table public.reports
  add constraint reports_status_check
  check (status in (
    'pending',
    'em_validacao',
    'validated',
    'flagged',
    'resolved',
    'rejected',
    'confirmado',
    'provavel',
    'pouca_evidencia',
    'suspeito'
  ));

create index if not exists idx_reports_validation_due
  on public.reports (status, validation_deadline)
  where status = 'em_validacao';

-- Localização opcional da subscription. Sem isso o backend não consegue provar
-- proximidade, então o envio por validação fica em no-op para essa subscription.
alter table public.push_subscriptions
  add column if not exists lat double precision,
  add column if not exists lon double precision,
  add column if not exists updated_at timestamptz default now();

create index if not exists idx_push_subscriptions_geo
  on public.push_subscriptions (lat, lon)
  where lat is not null and lon is not null;
