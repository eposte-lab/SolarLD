-- 0139 — Scansione territorio: scoping per provincia (no comune).
--
-- Il targeting per comune (mig 0138) era impreciso: la mappatura OSM
-- lavora a raggio attorno al centroide del comune e sconfina sui
-- comuni vicini. Si passa allo scoping per PROVINCIA: una scan job
-- copre regione + un insieme di province (vuoto = tutta la regione).
--
-- A. scan_jobs: `province_codes TEXT[]` (le province coperte dal job);
--    `comune` e `province` (singola) rimossi — sostituiti dall'array.
-- B. scan_candidates / tenant_target_areas: la chiave di scoping del
--    cursore passa da `comune` a `province_code`. `province_code`
--    esiste già su tenant_target_areas; va aggiunto a scan_candidates.

BEGIN;

-- ── A. scan_jobs — province_codes al posto di comune/province ────────
ALTER TABLE scan_jobs
  ADD COLUMN IF NOT EXISTS province_codes TEXT[] NOT NULL DEFAULT '{}';

ALTER TABLE scan_jobs DROP COLUMN IF EXISTS comune;
ALTER TABLE scan_jobs DROP COLUMN IF EXISTS province;

-- ── B. scoping del cursore: comune → province_code ───────────────────
ALTER TABLE scan_candidates
  ADD COLUMN IF NOT EXISTS province_code TEXT;

ALTER TABLE scan_candidates DROP COLUMN IF EXISTS comune;
ALTER TABLE tenant_target_areas DROP COLUMN IF EXISTS comune;

DROP INDEX IF EXISTS idx_scan_candidates_backlog;
CREATE INDEX IF NOT EXISTS idx_scan_candidates_backlog
  ON scan_candidates(tenant_id, province_code, processed_at)
  WHERE processed_at IS NULL;

DROP INDEX IF EXISTS idx_target_areas_tenant_comune;
CREATE INDEX IF NOT EXISTS idx_target_areas_tenant_province
  ON tenant_target_areas(tenant_id, province_code)
  WHERE status = 'active';

COMMIT;
