-- 0138 — Scansione territorio: scoping per comune, cursore di consumo,
--        cap totale.
--
-- Tre blocchi additivi (sicuri col codice vecchio: le colonne hanno
-- default sensati e 'completed' resta inutilizzato finché i worker
-- nuovi non vengono rilasciati).
--
-- A. tenant_target_areas: `comune` per filtrare le zone per scan job;
--    metriche di consumo (last_discovered_at, candidates_found,
--    depleted) per la finestra di ri-scoperta e la saturazione.
-- B. scan_jobs: `total_validated_cap` (cap totale di lead) + stato
--    'completed'; tenants.max_total_validated_cap come tetto di piano.
-- C. scan_candidates: `processed_at` — il cursore di consumo. NULL =
--    candidato ancora in coda di lavorazione; valorizzato = già
--    processato (diventato lead o scartato), non va ri-lavorato.
--    `comune` per isolare il backlog di una scansione dalle altre.

BEGIN;

-- ── A. tenant_target_areas — scoping + saturazione ───────────────────
ALTER TABLE tenant_target_areas
  ADD COLUMN IF NOT EXISTS comune             TEXT,
  ADD COLUMN IF NOT EXISTS last_discovered_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS candidates_found   INT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS depleted           BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_target_areas_tenant_comune
  ON tenant_target_areas(tenant_id, comune)
  WHERE status = 'active';

-- ── B. scan_jobs — cap totale + stato 'completed' ────────────────────
ALTER TABLE scan_jobs
  ADD COLUMN IF NOT EXISTS total_validated_cap INT NOT NULL DEFAULT 5000
    CHECK (total_validated_cap BETWEEN 1 AND 50000);

ALTER TABLE scan_jobs DROP CONSTRAINT IF EXISTS scan_jobs_status_check;
ALTER TABLE scan_jobs ADD CONSTRAINT scan_jobs_status_check CHECK (
  status IN (
    'pending','in_progress','paused',
    'paused_daily_cap','exhausted','completed','archived'
  )
);

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS max_total_validated_cap INT;

-- ── C. scan_candidates — cursore di consumo ──────────────────────────
ALTER TABLE scan_candidates
  ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS comune       TEXT;

CREATE INDEX IF NOT EXISTS idx_scan_candidates_backlog
  ON scan_candidates(tenant_id, comune, processed_at)
  WHERE processed_at IS NULL;

COMMIT;
