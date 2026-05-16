-- =====================================================================
-- Migration V4 — Triagem v2
-- Idempotente. Pode rodar várias vezes sem quebrar.
--
-- Adiciona:
--   • reports.photo_ai_is_urban_problem  — gate da Vision IA
--   • reports.bucket                     — classificação automática
--   • reports.rejection_reason           — motivo da rejeição (admin)
--   • tickets.assigned_org               — órgão destino (EMLURB, Celpe…)
--   • tickets.kanban_state               — coluna do kanban
--   • tickets.aggregated_from            — reports agregados a este ticket
--   • tickets.sla_deadline               — prazo SLA por prioridade
-- =====================================================================

BEGIN;

-- -------- reports ----------------------------------------------------
ALTER TABLE reports
  ADD COLUMN IF NOT EXISTS photo_ai_is_urban_problem BOOLEAN;

ALTER TABLE reports
  ADD COLUMN IF NOT EXISTS bucket TEXT;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'reports_bucket_check'
  ) THEN
    ALTER TABLE reports
      ADD CONSTRAINT reports_bucket_check
      CHECK (bucket IS NULL OR bucket IN ('filtrado','revisar','auto_validado'));
  END IF;
END $$;

ALTER TABLE reports
  ADD COLUMN IF NOT EXISTS rejection_reason TEXT;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'reports_rejection_reason_check'
  ) THEN
    ALTER TABLE reports
      ADD CONSTRAINT reports_rejection_reason_check
      CHECK (rejection_reason IS NULL OR rejection_reason IN
        ('duplicado','foto_invalida','fora_escopo','trote'));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_reports_bucket ON reports(bucket);

-- -------- tickets ----------------------------------------------------
ALTER TABLE tickets
  ADD COLUMN IF NOT EXISTS assigned_org TEXT;

ALTER TABLE tickets
  ADD COLUMN IF NOT EXISTS kanban_state TEXT DEFAULT 'aberto';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'tickets_kanban_state_check'
  ) THEN
    ALTER TABLE tickets
      ADD CONSTRAINT tickets_kanban_state_check
      CHECK (kanban_state IN ('aberto','em_atendimento','resolvido','fechado'));
  END IF;
END $$;

ALTER TABLE tickets
  ADD COLUMN IF NOT EXISTS aggregated_from UUID[] DEFAULT ARRAY[]::UUID[];

ALTER TABLE tickets
  ADD COLUMN IF NOT EXISTS sla_deadline TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_tickets_kanban_state ON tickets(kanban_state);

CREATE INDEX IF NOT EXISTS idx_tickets_sla_deadline
  ON tickets(sla_deadline)
  WHERE kanban_state IS DISTINCT FROM 'fechado';

-- -------- backfill ---------------------------------------------------
-- Tickets antigos sem kanban_state ficam como 'aberto' (default já cobre,
-- mas garante consistência se a coluna existir vazia por algum motivo)
UPDATE tickets SET kanban_state = 'aberto' WHERE kanban_state IS NULL;

COMMIT;
