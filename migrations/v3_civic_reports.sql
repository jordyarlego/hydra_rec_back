-- =========================================================
-- HydraRec V3 · Plataforma Cívica de Reports · 2026-05-15
-- Apply via Supabase SQL Editor after backing up.
-- Idempotent: safe to re-run.
-- =========================================================

-- ---------- 0. Helper: detecta role admin via JWT ----------

create or replace function public.current_role_is_admin()
returns boolean
language sql
stable
as $$
  select coalesce(
    (auth.jwt() -> 'user_metadata' ->> 'role') = 'admin',
    false
  );
$$;

-- =========================================================
-- 1. weather_snapshots — clima APAC cruzado com cada report
-- =========================================================

create table if not exists public.weather_snapshots (
  id                  uuid primary key default uuid_generate_v4(),
  lat                 double precision not null,
  lon                 double precision not null,
  station_id          text,
  station_name        text,
  station_distance_m  int,
  rain_1h_mm          double precision,
  rain_24h_mm         double precision,
  temp_c              double precision,
  humidity_pct        double precision,
  wind_kmh            double precision,
  source              text not null check (source in (
                        'cemaden','meteorologia24h','climatologico','none'
                      )),
  raw                 jsonb,
  captured_at         timestamptz not null default now()
);

create index if not exists idx_weather_snapshots_geo
  on public.weather_snapshots (lat, lon, captured_at desc);

create index if not exists idx_weather_snapshots_captured
  on public.weather_snapshots (captured_at desc);

-- =========================================================
-- 2. reports — extensão V3 (foto, cruzamento, validação, status)
-- =========================================================

-- 2.1 Atualiza CHECK do `type` para incluir buraco/lixo/iluminacao
alter table public.reports drop constraint if exists reports_type_check;
alter table public.reports
  add constraint reports_type_check
  check (type in (
    'alagamento','deslizamento','queda_arvore','via_intransitavel',
    'poste_caido','buraco','lixo','iluminacao','outro'
  ));

-- 2.2 Colunas novas
alter table public.reports
  add column if not exists photo_url            text,
  add column if not exists photo_ai_description text,
  add column if not exists photo_ai_confidence  double precision,
  add column if not exists weather_snapshot_id  uuid references public.weather_snapshots(id),
  add column if not exists ai_validation_score  double precision,
  add column if not exists ai_validation_notes  text,
  add column if not exists likes_up             int default 0,
  add column if not exists likes_down           int default 0,
  add column if not exists status               text default 'pending',
  add column if not exists ticket_id            uuid,
  add column if not exists admin_notes          text;

-- 2.3 CHECK do status
alter table public.reports drop constraint if exists reports_status_check;
alter table public.reports
  add constraint reports_status_check
  check (status in ('pending','validated','flagged','resolved','rejected'));

-- 2.4 Índices
create index if not exists idx_reports_status
  on public.reports (status, created_at desc);

create index if not exists idx_reports_weather_snapshot
  on public.reports (weather_snapshot_id);

-- =========================================================
-- 3. report_likes — votos ↑↓ ponderados
-- =========================================================

create table if not exists public.report_likes (
  id          uuid primary key default uuid_generate_v4(),
  report_id   uuid not null references public.reports(id) on delete cascade,
  ip_hash     text not null,
  vote        smallint not null check (vote in (-1, 1)),
  weight      double precision default 1.0,
  created_at  timestamptz default now(),
  unique (report_id, ip_hash)
);

create index if not exists idx_report_likes_report
  on public.report_likes (report_id);

-- =========================================================
-- 4. tickets — chamados internos / prefeitura
-- =========================================================

create table if not exists public.tickets (
  id              uuid primary key default uuid_generate_v4(),
  report_id       uuid references public.reports(id),
  bairro          text,
  type            text,
  priority        text not null default 'media'
                  check (priority in ('baixa','media','alta','urgente')),
  status          text not null default 'aberto'
                  check (status in ('aberto','triagem','em_andamento','aguardando','resolvido','cancelado')),
  assigned_to     text,
  external_ref    text,
  notes           text,
  created_by      uuid references auth.users(id),
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);

create index if not exists idx_tickets_status
  on public.tickets (status, priority desc, created_at desc);

create index if not exists idx_tickets_report
  on public.tickets (report_id);

-- Trigger updated_at em tickets
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_tickets_touch on public.tickets;
create trigger trg_tickets_touch
  before update on public.tickets
  for each row execute function public.touch_updated_at();

-- =========================================================
-- 5. apac_stations_cache — cache local de estações APAC
-- =========================================================

create table if not exists public.apac_stations_cache (
  id              text primary key,
  name            text not null,
  kind            text not null check (kind in ('cemaden','meteorologia24h','climatologico')),
  lat             double precision not null,
  lon             double precision not null,
  last_payload    jsonb,
  last_fetched_at timestamptz default now()
);

create index if not exists idx_apac_stations_kind
  on public.apac_stations_cache (kind);

-- =========================================================
-- 6. admin_audit — auditoria de ações admin
-- =========================================================

create table if not exists public.admin_audit (
  id            bigserial primary key,
  user_id       uuid references auth.users(id),
  action        text not null,
  target_table  text,
  target_id     uuid,
  diff          jsonb,
  created_at    timestamptz default now()
);

create index if not exists idx_admin_audit_target
  on public.admin_audit (target_table, target_id);

create index if not exists idx_admin_audit_user_recent
  on public.admin_audit (user_id, created_at desc);

-- =========================================================
-- 7. RLS — Row Level Security
-- =========================================================

-- 7.1 weather_snapshots
alter table public.weather_snapshots enable row level security;
drop policy if exists "Public can read weather snapshots" on public.weather_snapshots;
create policy "Public can read weather snapshots"
  on public.weather_snapshots for select
  using (true);

-- 7.2 reports — admin pode atualizar
drop policy if exists "Admin can update reports" on public.reports;
create policy "Admin can update reports"
  on public.reports for update
  using (public.current_role_is_admin())
  with check (public.current_role_is_admin());

-- 7.3 report_likes — leitura pública (count). Escrita via service_role no backend.
alter table public.report_likes enable row level security;
drop policy if exists "Public can read likes" on public.report_likes;
create policy "Public can read likes"
  on public.report_likes for select
  using (true);

-- 7.4 tickets — apenas admin lê/escreve
alter table public.tickets enable row level security;
drop policy if exists "Admin tickets all" on public.tickets;
create policy "Admin tickets all"
  on public.tickets for all
  using (public.current_role_is_admin())
  with check (public.current_role_is_admin());

-- 7.5 admin_audit — apenas admin lê (escrita via service_role)
alter table public.admin_audit enable row level security;
drop policy if exists "Admin reads audit" on public.admin_audit;
create policy "Admin reads audit"
  on public.admin_audit for select
  using (public.current_role_is_admin());

-- 7.6 apac_stations_cache — leitura pública
alter table public.apac_stations_cache enable row level security;
drop policy if exists "Public reads apac stations" on public.apac_stations_cache;
create policy "Public reads apac stations"
  on public.apac_stations_cache for select
  using (true);

-- =========================================================
-- 8. Storage bucket (executar via painel ou comentado abaixo)
-- =========================================================
--
-- No painel Supabase → Storage → Create bucket:
--   Nome:               report-photos
--   Public:             ON (read)
--   File size limit:    5242880  (5 MB)
--   Allowed mime types: image/jpeg, image/png, image/webp
--
-- Policy de leitura pública é automática quando bucket = public.
-- Upload acontece pelo backend usando SUPABASE_SERVICE_KEY.

-- =========================================================
-- Fim do delta V3
-- =========================================================
