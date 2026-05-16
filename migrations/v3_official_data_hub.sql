-- =========================================================
-- Official Data Hub — dados urbanos oficiais do Recife
-- HydraRec V3 · 2026-05-15
-- Aplicar no Supabase SQL Editor após v3_civic_reports.sql
-- =========================================================

-- ── 1. Bairros / RPAs / Microrregiões normalizados ───────

create table if not exists public.official_neighborhoods (
  id               uuid primary key default uuid_generate_v4(),
  name             text not null,
  rpa              text,
  rpa_code         int,
  microregion      text,
  microregion_code int,
  source           text default 'geojson_recife_2023',
  geom_geojson     jsonb,
  raw              jsonb,
  imported_at      timestamptz default now()
);

create unique index if not exists idx_official_neigh_name
  on public.official_neighborhoods (lower(name));

-- ── 2. Logradouros / trechos de rua ──────────────────────

create table if not exists public.official_roads (
  id                  uuid primary key default uuid_generate_v4(),
  name                text,
  neighborhood        text,
  rpa                 text,
  microregion         text,
  pavement_type       text,
  transport_corridor  boolean default false,
  source              text,
  lat                 double precision,
  lon                 double precision,
  geom_geojson        jsonb,
  raw                 jsonb,
  imported_at         timestamptz default now()
);

create index if not exists idx_official_roads_neigh
  on public.official_roads (neighborhood);

create index if not exists idx_official_roads_geo
  on public.official_roads (lat, lon)
  where lat is not null and lon is not null;

-- ── 3. Chamados de serviços oficiais (EMLURB 156, Defesa Civil) ──

create table if not exists public.official_service_requests (
  id                uuid primary key default uuid_generate_v4(),
  external_id       text,
  source            text not null,
  agency            text,
  service_type      text,
  category          text,
  status            text,
  description       text,
  neighborhood      text,
  rpa               text,
  microregion       text,
  street_name       text,
  lat               double precision,
  lon               double precision,
  opened_at         timestamptz,
  closed_at         timestamptz,
  raw               jsonb,
  imported_at       timestamptz default now()
);

create unique index if not exists idx_official_sr_ext
  on public.official_service_requests (source, external_id)
  where external_id is not null;

create index if not exists idx_official_sr_category
  on public.official_service_requests (category, service_type);
create index if not exists idx_official_sr_neighborhood
  on public.official_service_requests (neighborhood, rpa);
create index if not exists idx_official_sr_opened
  on public.official_service_requests (opened_at desc);
create index if not exists idx_official_sr_geo
  on public.official_service_requests (lat, lon)
  where lat is not null and lon is not null;

-- ── 4. Ativos urbanos (postes, lixeiras) ─────────────────

create table if not exists public.official_assets (
  id           uuid primary key default uuid_generate_v4(),
  external_id  text,
  asset_type   text not null,
  name         text,
  neighborhood text,
  street_name  text,
  lat          double precision,
  lon          double precision,
  source       text,
  raw          jsonb,
  imported_at  timestamptz default now()
);

create index if not exists idx_official_assets_type
  on public.official_assets (asset_type);
create index if not exists idx_official_assets_geo
  on public.official_assets (lat, lon)
  where lat is not null and lon is not null;

-- ── 5. Cruzamento report ↔ dados oficiais ────────────────

create table if not exists public.report_official_crossings (
  id                                  uuid primary key default uuid_generate_v4(),
  report_id                           uuid not null references public.reports(id) on delete cascade,
  neighborhood                        text,
  rpa                                 text,
  rpa_code                            int,
  microregion                         text,
  nearest_road_id                     uuid references public.official_roads(id),
  nearest_road_name                   text,
  nearest_official_request_id         uuid references public.official_service_requests(id),
  nearest_official_request_type       text,
  nearest_official_request_distance_m int,
  recurrence_score                    double precision default 0,
  official_priority_score             double precision default 0,
  notes                               text,
  created_at                          timestamptz default now()
);

create unique index if not exists idx_report_crossings_report
  on public.report_official_crossings (report_id);
create index if not exists idx_report_crossings_neigh
  on public.report_official_crossings (neighborhood, rpa);

-- ── 6. Log de importações ─────────────────────────────────

create table if not exists public.official_import_log (
  id          bigserial primary key,
  source      text not null,
  records_ok  int default 0,
  records_err int default 0,
  duration_s  double precision,
  error       text,
  started_at  timestamptz default now()
);

-- ════════════════════════════════════════════════════════
-- RLS — todas as tabelas de dados oficiais são públicas
-- ════════════════════════════════════════════════════════

alter table public.official_neighborhoods enable row level security;
drop policy if exists "Public read official_neighborhoods" on public.official_neighborhoods;
create policy "Public read official_neighborhoods"
  on public.official_neighborhoods for select using (true);

alter table public.official_roads enable row level security;
drop policy if exists "Public read official_roads" on public.official_roads;
create policy "Public read official_roads"
  on public.official_roads for select using (true);

alter table public.official_service_requests enable row level security;
drop policy if exists "Public read official_service_requests" on public.official_service_requests;
create policy "Public read official_service_requests"
  on public.official_service_requests for select using (true);

alter table public.official_assets enable row level security;
drop policy if exists "Public read official_assets" on public.official_assets;
create policy "Public read official_assets"
  on public.official_assets for select using (true);

alter table public.report_official_crossings enable row level security;
drop policy if exists "Public read report_official_crossings" on public.report_official_crossings;
create policy "Public read report_official_crossings"
  on public.report_official_crossings for select using (true);

alter table public.official_import_log enable row level security;
drop policy if exists "Admin read import log" on public.official_import_log;
create policy "Admin read import log"
  on public.official_import_log for select
  using (public.current_role_is_admin());
