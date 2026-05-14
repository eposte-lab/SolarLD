-- ============================================================
-- 0122 — scan_jobs (replaces scan_schedules)
-- ============================================================
-- Refactor totale: la pagina /territorio è una coda di lavori
-- (scan_jobs), non più scan_schedules ricorrenti. Ogni job:
--   - rappresenta un territorio + settori da scansionare
--   - ha un daily_validated_cap (max lead VALIDI post-L5 al giorno)
--   - viene consumato dal cron in priority order
--   - quando esaurisce il territorio → status='exhausted'
--   - flag always_active → restart auto cercando aziende nuove
--
-- scan_schedules era stata introdotta in 0120 ma mai usata in
-- produzione → drop pulito.

DROP TABLE IF EXISTS scan_schedules CASCADE;

CREATE TABLE IF NOT EXISTS scan_jobs (
  id                       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id                UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name                     TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),

  -- Territorio (almeno UNO dei 3 deve essere valorizzato)
  region                   TEXT,
  province                 TEXT,
  comune                   TEXT,

  sector_filters           TEXT[] NOT NULL DEFAULT '{}'::text[],

  -- Max contatti VALIDATI (post-L5) prodotti per giorno
  daily_validated_cap      INT NOT NULL DEFAULT 200
                            CHECK (daily_validated_cap BETWEEN 1 AND 5000),

  -- Priorità: ASC = top consumato per primo (drag-drop riassegna)
  priority                 INT NOT NULL DEFAULT 100,

  status                   TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN (
                              'pending','in_progress','paused',
                              'paused_daily_cap','exhausted','archived'
                            )),

  -- "Sempre attivo": quando exhausted → restart auto cercando aziende nuove
  always_active            BOOLEAN NOT NULL DEFAULT false,

  -- Telemetria
  valid_leads_total        INT NOT NULL DEFAULT 0,   -- cumulativo post-L5
  valid_leads_today        INT NOT NULL DEFAULT 0,   -- reset midnight tenant tz
  valid_leads_today_date   DATE,
  candidates_scanned_total INT NOT NULL DEFAULT 0,   -- raw Places visti
  last_run_at              TIMESTAMPTZ,
  last_error               TEXT,

  created_by               UUID REFERENCES auth.users(id) ON DELETE SET NULL,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT scan_jobs_territory_required CHECK (
    region IS NOT NULL OR province IS NOT NULL OR comune IS NOT NULL
  )
);

-- Hot path: prossimo job da eseguire (priority ASC)
CREATE INDEX IF NOT EXISTS idx_scan_jobs_queue
  ON scan_jobs(tenant_id, priority)
  WHERE status IN ('pending','in_progress','paused_daily_cap');

-- Generic listing per status
CREATE INDEX IF NOT EXISTS idx_scan_jobs_tenant_status
  ON scan_jobs(tenant_id, status);

CREATE TRIGGER trg_scan_jobs_updated_at
  BEFORE UPDATE ON scan_jobs
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE scan_jobs ENABLE ROW LEVEL SECURITY;

CREATE POLICY scan_jobs_tenant_isolation ON scan_jobs
  FOR ALL TO authenticated
  USING (tenant_id = auth_tenant_id())
  WITH CHECK (tenant_id = auth_tenant_id());

COMMENT ON TABLE scan_jobs IS
  'Operator-defined scan queue: each job = territory + sectors + daily cap. Worker consumes by priority, stops at daily_validated_cap leads.';
