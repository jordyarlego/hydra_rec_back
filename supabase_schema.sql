create extension if not exists "uuid-ossp";

create table if not exists public.reports (
    id              uuid primary key default uuid_generate_v4(),
    type            text not null check (type in
        ('alagamento','deslizamento','queda_arvore','via_intransitavel','poste_caido','outro')),
    severity        text not null check (severity in ('leve','moderado','grave')),
    description     text,
    lat             double precision not null,
    lon             double precision not null,
    bairro          text,
    ip_hash         text not null,
    user_agent      text,
    photo_path      text,
    photo_verified  boolean default false,
    created_at      timestamptz default now(),
    confirmed_count int default 0,
    resolved        boolean default false,
    resolved_at     timestamptz,
    check (lat between -8.16 and -7.93 and lon between -35.02 and -34.83)
);

create index if not exists idx_reports_geo on public.reports using btree (lat, lon);
create index if not exists idx_reports_recent on public.reports (created_at desc);
create index if not exists idx_reports_bairro_recent on public.reports (bairro, created_at desc);
create index if not exists idx_reports_unresolved on public.reports (resolved, created_at desc)
    where resolved = false;

create table if not exists public.alerts (
    id                       uuid primary key default uuid_generate_v4(),
    bairro                   text not null,
    type                     text not null,
    message                  text not null,
    severity                 text not null check (severity in ('moderado','alto','severo')),
    triggered_by_report_ids  uuid[],
    created_at               timestamptz default now(),
    expires_at               timestamptz not null,
    active                   boolean default true
);

create index if not exists idx_alerts_active on public.alerts (active, expires_at desc);
create index if not exists idx_alerts_bairro on public.alerts (bairro, active);

create table if not exists public.rate_limits (
    ip_hash       text primary key,
    last_action   timestamptz default now(),
    action_count  int default 1,
    blocked_until timestamptz
);

create table if not exists public.push_subscriptions (
    id           uuid primary key default uuid_generate_v4(),
    ip_hash      text not null,
    endpoint     text not null unique,
    p256dh       text not null,
    auth         text not null,
    bairro       text,
    min_severity text default 'alto',
    created_at   timestamptz default now()
);

create table if not exists public.weather_history (
    id              serial primary key,
    bairro          text not null,
    date            date not null,
    rain_total_mm   double precision,
    temp_max        double precision,
    temp_min        double precision,
    hydra_score_max int,
    snapshot        jsonb,
    unique (bairro, date)
);

create index if not exists idx_weather_history_bairro_date
    on public.weather_history (bairro, date desc);

create table if not exists public.reputation (
    ip_hash           text primary key,
    reports_total     int default 0,
    reports_confirmed int default 0,
    reports_rejected  int default 0,
    trust_score       double precision default 0.5,
    updated_at        timestamptz default now()
);

create table if not exists public.apac_bulletins (
    id             serial primary key,
    published_at   date not null unique,
    alert_level    text,
    bairros_alerta text[],
    raw_text       text,
    pdf_url        text,
    fetched_at     timestamptz default now()
);

alter table public.reports enable row level security;
alter table public.alerts enable row level security;
alter table public.push_subscriptions enable row level security;
alter table public.weather_history enable row level security;
alter table public.reputation enable row level security;
alter table public.apac_bulletins enable row level security;

drop policy if exists "Public can read recent unresolved reports" on public.reports;
create policy "Public can read recent unresolved reports"
    on public.reports for select
    using (resolved = false and created_at > now() - interval '24 hours');

drop policy if exists "Public can read active alerts" on public.alerts;
create policy "Public can read active alerts"
    on public.alerts for select
    using (active = true and expires_at > now());

drop policy if exists "Public can read weather history" on public.weather_history;
create policy "Public can read weather history"
    on public.weather_history for select
    using (true);

drop policy if exists "Public can read apac bulletins" on public.apac_bulletins;
create policy "Public can read apac bulletins"
    on public.apac_bulletins for select
    using (true);
