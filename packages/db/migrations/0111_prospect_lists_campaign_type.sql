-- 0111 — prospect_lists: add campaign_type for non-Solar campaigns.
--
-- The "Trova aziende" feature was originally Solar-only: every list
-- went through L4 Solar gate (200 m² / 60 kWp / 1200 h sunshine) before
-- becoming leads. The new "campagna custom" path lets a tenant target
-- companies that don't need rooftop validation (e.g. amministratori di
-- condominio for a service offering, dental clinics for a B2B product,
-- etc.) and ship them directly to the outreach pipeline with their
-- own custom HTML email template.
--
-- This migration adds the schema flag that controls bypass at validation
-- time. The bypass logic itself lives in
-- `apps/api/src/services/prospect_list_validation.py`.

BEGIN;

-- 1) New column on prospect_lists.
ALTER TABLE prospect_lists
  ADD COLUMN IF NOT EXISTS campaign_type VARCHAR(20)
    DEFAULT 'solar_rooftop'
    CHECK (campaign_type IN ('solar_rooftop', 'generic_outreach'));

-- 2) Backfill existing rows explicitly to the default — guards against
--    NULL values inserted before the DEFAULT was added.
UPDATE prospect_lists SET campaign_type = 'solar_rooftop' WHERE campaign_type IS NULL;

-- 3) Extend scan_candidates.solar_verdict to surface the non-Solar
--    bypass without abusing 'accepted' (which means "passed Solar gate"
--    everywhere else in the codebase).
ALTER TABLE scan_candidates DROP CONSTRAINT IF EXISTS scan_candidates_solar_verdict_check;

ALTER TABLE scan_candidates ADD CONSTRAINT scan_candidates_solar_verdict_check
  CHECK (
    (solar_verdict IS NULL)
    OR (solar_verdict = ANY (ARRAY[
      'accepted',
      'rejected_tech',
      'no_building',
      'api_error',
      'skipped_below_gate',
      'skipped_non_solar'
    ]))
  );

-- 4) Index for filtering campaign_type — used by the dashboard list page.
CREATE INDEX IF NOT EXISTS idx_prospect_lists_campaign_type
  ON prospect_lists(tenant_id, campaign_type);

COMMIT;
