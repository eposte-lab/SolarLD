-- Migration 0062 — pipeline_v2_pilot
--
-- Adds the per-tenant feature flag that gates the V2 pipeline
-- (Phase 2 offline filters + Phase 3 email extraction).
--
-- Rollout procedure:
--   1. Set flag for a single pilot tenant:
--        UPDATE tenants SET pipeline_v2_pilot = true
--        WHERE id = '<pilot-tenant-uuid>';
--
--   2. Monitor for 48 hours:
--        -- Audit: all extraction attempts logged?
--        SELECT count(*) FROM email_extraction_log WHERE occurred_at > now() - interval '48h';
--        -- Cost sanity: avg cost ≤ V1 baseline?
--        SELECT avg(cost_cents) FROM email_extraction_log WHERE occurred_at > now() - interval '48h';
--        -- Rejection rate acceptable?
--        SELECT rule, count(*) FROM lead_rejection_log
--        WHERE created_at > now() - interval '48h' GROUP BY rule ORDER BY count DESC;
--
--   3. If all checks pass, promote all tenants to V2:
--        UPDATE tenants SET pipeline_v2_pilot = true;
--
--   4. Instant rollback if needed:
--        UPDATE tenants SET pipeline_v2_pilot = false;
--        -- or per-tenant:
--        UPDATE tenants SET pipeline_v2_pilot = false WHERE id = '<problem-tenant-uuid>';
--
-- Non-pilot tenants: EmailExtractionAgent forwards immediately to scoring_task
-- (zero behaviour change — transparent pass-through).
-- Pilot tenants: full V2 Phase 2 + Phase 3 execution with GDPR audit logging.

BEGIN;

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS pipeline_v2_pilot BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN tenants.pipeline_v2_pilot IS
    'When true, this tenant runs Phase 2 (offline filters) + Phase 3 (email extraction)
     via the V2 pipeline before scoring. Set to true for pilot tenants first; after 48 h
     of successful monitoring run: UPDATE tenants SET pipeline_v2_pilot = true
     to promote all tenants globally. Flip back to false for instant rollback.
     Non-pilot tenants receive a transparent pass-through to scoring_task.';

-- Fast lookup by ops tooling (e.g. "SELECT * FROM tenants WHERE pipeline_v2_pilot = true")
CREATE INDEX IF NOT EXISTS idx_tenants_pipeline_v2_pilot
    ON tenants (pipeline_v2_pilot)
    WHERE pipeline_v2_pilot = TRUE;

COMMIT;
