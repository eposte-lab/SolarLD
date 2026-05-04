-- 0105 — scan_candidates: add v3 geocentric columns (no-Atoka funnel).
--
-- Makes scan_candidates support BOTH v2 (Atoka/VAT-keyed) and v3
-- (Places/google_place_id-keyed) rows in the same table.
-- The full wipe of v2 data is a separate step (0100, deferred).

BEGIN;

ALTER TABLE scan_candidates ALTER COLUMN vat_number DROP NOT NULL;
ALTER TABLE scan_candidates DROP CONSTRAINT scan_candidates_tenant_id_scan_id_vat_number_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_sc_v2_vat_unique ON scan_candidates(tenant_id, scan_id, vat_number) WHERE vat_number IS NOT NULL;
ALTER TABLE scan_candidates ADD COLUMN IF NOT EXISTS google_place_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_sc_v3_place_unique ON scan_candidates(tenant_id, google_place_id) WHERE google_place_id IS NOT NULL;
ALTER TABLE scan_candidates ADD COLUMN IF NOT EXISTS scraped_data JSONB NOT NULL DEFAULT '{}'::jsonb, ADD COLUMN IF NOT EXISTS contact_extraction JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE scan_candidates ADD COLUMN IF NOT EXISTS building_quality_score SMALLINT;
ALTER TABLE scan_candidates ADD COLUMN IF NOT EXISTS proxy_score_data JSONB;
ALTER TABLE scan_candidates ADD COLUMN IF NOT EXISTS funnel_version SMALLINT NOT NULL DEFAULT 2;
ALTER TABLE scan_candidates ADD COLUMN IF NOT EXISTS recommended_for_rendering BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE scan_candidates DROP CONSTRAINT scan_candidates_stage_check;
ALTER TABLE scan_candidates ADD CONSTRAINT scan_candidates_stage_check CHECK (stage >= 1 AND stage <= 5);
CREATE INDEX IF NOT EXISTS idx_sc_funnel_version ON scan_candidates(tenant_id, funnel_version, stage);
CREATE INDEX IF NOT EXISTS idx_sc_recommended ON scan_candidates(tenant_id, recommended_for_rendering) WHERE recommended_for_rendering = true;
CREATE INDEX IF NOT EXISTS idx_sc_google_place_id ON scan_candidates(google_place_id) WHERE google_place_id IS NOT NULL;

COMMIT;
