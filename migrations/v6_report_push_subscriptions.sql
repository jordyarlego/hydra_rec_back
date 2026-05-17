-- v6: vincula push subscription a um report específico
-- Pra notificar SÓ o cidadão dono do report quando o ticket muda de estado.

create table if not exists public.report_push_subscriptions (
  id                  uuid primary key default uuid_generate_v4(),
  report_id           uuid not null references public.reports(id) on delete cascade,
  push_endpoint       text not null,
  last_notified_state text,
  created_at          timestamptz default now(),
  unique (report_id, push_endpoint)
);

create index if not exists idx_rps_report
  on public.report_push_subscriptions (report_id);

create index if not exists idx_rps_endpoint
  on public.report_push_subscriptions (push_endpoint);

-- RLS: público pode inserir (cidadão anônimo se inscreve), apenas service
-- key pode ler/atualizar (worker que envia push).
alter table public.report_push_subscriptions enable row level security;

drop policy if exists "Public insert report subscription"
  on public.report_push_subscriptions;
create policy "Public insert report subscription"
  on public.report_push_subscriptions
  for insert
  with check (true);

-- Marca a última coluna de estado que o worker viu por ticket, pra não
-- re-notificar o mesmo estado em loop. Se preferir, a coluna last_notified_state
-- na tabela acima já cumpre por subscription — mas mantemos esse cache por ticket
-- pra worker fazer 1 query rápida.
alter table public.tickets
  add column if not exists last_pushed_state text;
