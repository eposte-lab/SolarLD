-- ============================================================
-- 0120 — scan_schedules
-- ============================================================
-- Punto E avanzato del feedback cliente Total Trade: dare al tenant
-- la possibilità di **schedulare** le scansioni territoriali invece di
-- limitarsi al cron 04:30 globale, e di **distribuire** una scansione
-- grossa (es. 500 candidati) su più giorni quando il daily_target_cap
-- è inferiore.
--
-- Modello dati
-- ------------
-- Una `scan_schedule` rappresenta un'esecuzione ripetuta del funnel v3
-- limitata ai territory_ids e wizard_groups scelti dall'operatore.
-- Il worker `scan_schedules_cron` (vedi cron.py) legge le schedule
-- attive ogni mattina, le quali "spendono" un budget giornaliero
-- pari a `daily_cap`. Quando il budget esaurisce un giorno, l'avanzo
-- viene riportato al `next_run_at` del giorno successivo.
--
-- Politica scheduling
-- -------------------
-- * frequency_days = 1   → ogni giorno
-- * frequency_days = 3   → ogni 3 giorni
-- * frequency_days = 7   → settimanale
-- * frequency_days = 0   → one-shot (next_run_at fissato manualmente,
--                          dopo l'esecuzione la riga viene archiviata)
--
-- Il `next_run_at` viene aggiornato dal worker dopo ogni run usando
-- now() + frequency_days * 1 day. last_run_at tiene il timestamp
-- dell'ultima esecuzione.
--
-- Il backfill `roofs.territory_id` per i roof legacy (pre-funnel-v3)
-- è una migrazione di dati separata, applicata via Supabase MCP per
-- evitare update cross-tenant da SQL Migration runner.

CREATE TABLE IF NOT EXISTS scan_schedules (
  id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

  -- Identificativo human-readable per l'operatore.
  name                TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),

  -- Filtri di scansione. Array vuoto = "tutte le zone / tutti i settori
  -- del tenant". Quando non-vuoti restringono il bounding box.
  territory_ids       UUID[] NOT NULL DEFAULT '{}'::uuid[],
  sector_filters      TEXT[] NOT NULL DEFAULT '{}'::text[],

  -- Budget giornaliero in candidati Places. Default 100; cap 5000.
  daily_cap           INTEGER NOT NULL DEFAULT 100
                       CHECK (daily_cap BETWEEN 1 AND 5000),

  -- Periodicità in giorni. 0 = one-shot, 1 = daily, 7 = settimanale.
  frequency_days      SMALLINT NOT NULL DEFAULT 1
                       CHECK (frequency_days BETWEEN 0 AND 90),

  -- Stato lifecycle.
  status              TEXT NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active', 'paused', 'archived')),

  next_run_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_run_at         TIMESTAMPTZ,
  last_run_candidates INTEGER,
  last_run_cost_eur   NUMERIC(8, 2),

  created_by          UUID REFERENCES auth.users(id) ON DELETE SET NULL,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scan_schedules_tenant_status
  ON scan_schedules(tenant_id, status);

-- Hot path per il cron.
CREATE INDEX IF NOT EXISTS idx_scan_schedules_active_due
  ON scan_schedules(next_run_at)
  WHERE status = 'active';

CREATE TRIGGER trg_scan_schedules_updated_at
  BEFORE UPDATE ON scan_schedules
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE scan_schedules ENABLE ROW LEVEL SECURITY;

CREATE POLICY scan_schedules_tenant_isolation ON scan_schedules
  FOR ALL TO authenticated
  USING (tenant_id = auth_tenant_id())
  WITH CHECK (tenant_id = auth_tenant_id());

COMMENT ON TABLE scan_schedules IS
  'Operator-defined recurring scans: territory subset + sector filters + daily budget + frequency. Replaces the global 04:30 cron for tenants who want per-territory control.';
COMMENT ON COLUMN scan_schedules.frequency_days IS
  '0 = one-shot, 1 = daily, 3 = ogni 3 giorni, 7 = settimanale (max 90).';
COMMENT ON COLUMN scan_schedules.daily_cap IS
  'Max candidates the scheduler will request per single run. Daily-budget control to amortize big territory scans (es. 500 candidates @ 100/day = 5 giorni).';
