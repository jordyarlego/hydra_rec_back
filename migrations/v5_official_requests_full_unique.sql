-- v5_official_requests_full_unique.sql
-- Permite que o PostgREST/Supabase use on_conflict=(source,external_id)
-- no upsert incremental das bases EMLURB/Defesa Civil.
--
-- O índice v3 era parcial (where external_id is not null), suficiente para
-- evitar duplicatas, mas nem sempre aceito pelo PostgREST como alvo de upsert.

create unique index if not exists idx_official_sr_ext_full
  on public.official_service_requests (source, external_id);
