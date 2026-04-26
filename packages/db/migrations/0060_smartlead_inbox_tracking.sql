-- Migration 0060 — Smartlead warm-up tracking columns on tenant_inboxes
--
-- Adds two columns to `tenant_inboxes` so the Smartlead sync service
-- (Task 14 / smartlead_service.py) can:
--   1. Store the Smartlead numeric account ID alongside the OAuth inbox
--      row so daily sync doesn't need a slow API list-all call.
--   2. Persist the most recent health score (0-100) from Smartlead's
--      warm-up analysis so the dashboard can surface inbox health without
--      an extra Smartlead round-trip.
--
-- Both columns are nullable (not all inboxes use Smartlead warm-up;
-- Resend inboxes and SMTP-only inboxes leave these null).
--
-- Related files:
--   apps/api/src/services/smartlead_service.py   (Task 14)
--   apps/api/src/scripts/shadow_domain_setup.py  (Task 13)

BEGIN;

-- 1. Smartlead numeric account ID (populated on enroll_inbox() success)
ALTER TABLE tenant_inboxes
  ADD COLUMN IF NOT EXISTS smartlead_account_id BIGINT;

COMMENT ON COLUMN tenant_inboxes.smartlead_account_id IS
  'Smartlead.ai numeric account ID. NULL for inboxes not enrolled in Smartlead warm-up.';

-- 2. Latest warm-up health score from Smartlead (0-100 float, synced daily)
ALTER TABLE tenant_inboxes
  ADD COLUMN IF NOT EXISTS smartlead_health_score NUMERIC(5,2)
    CHECK (smartlead_health_score IS NULL OR (smartlead_health_score >= 0 AND smartlead_health_score <= 100));

COMMENT ON COLUMN tenant_inboxes.smartlead_health_score IS
  'Latest warm-up health score from Smartlead (0-100). NULL until first sync. Synced daily by cron.';

-- Index for the daily sync query that filters by provider + smartlead ID
CREATE INDEX IF NOT EXISTS idx_tenant_inboxes_smartlead_id
  ON tenant_inboxes (smartlead_account_id)
  WHERE smartlead_account_id IS NOT NULL;

COMMIT;
