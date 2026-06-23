-- 0158_active_lead_notified.sql
-- Per-lead "entered Lead Attivi" email notification — idempotency stamp.
--
-- When a lead enters the dashboard "lead attivi" set (engaged AND
-- operator_released_at IS NOT NULL) a single email is auto-sent to the
-- tenant's configured recipients (active_lead_notify_cron, every 15 min).
-- This column makes that one-per-lead and never-repeated.
--
-- CRUCIAL backfill: every lead ALREADY in the active set at rollout is
-- stamped now() so enabling the cron does NOT blast the existing pipeline
-- (those leads were already handled / sent in the manual digest). Only
-- leads that ENTER the active set AFTER this migration will fire a mail.
BEGIN;

ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS active_lead_notified_at TIMESTAMPTZ;

COMMENT ON COLUMN leads.active_lead_notified_at IS
  'When the active-lead notification email was sent for this lead (one-per-lead, never re-sent). NULL = not yet notified. Stamped by active_lead_notify_cron after a successful send. Backfilled to now() at rollout for leads already in "lead attivi" so the cron only fires for future entrants.';

-- Partial index for the cron scan: candidates are released-but-unnotified.
CREATE INDEX IF NOT EXISTS idx_leads_active_lead_unnotified
  ON leads (tenant_id)
  WHERE active_lead_notified_at IS NULL AND operator_released_at IS NOT NULL;

-- Backfill: mark every lead CURRENTLY in "lead attivi" (engaged + released)
-- as already notified. Mirrors the dashboard ENGAGEMENT_OR predicate
-- (apps/dashboard/src/lib/data/leads.ts) so the suppressed set matches
-- exactly what the operator already sees today.
UPDATE leads
SET active_lead_notified_at = now()
WHERE active_lead_notified_at IS NULL
  AND operator_released_at IS NOT NULL
  AND (
       outreach_clicked_at   IS NOT NULL
    OR dashboard_visited_at  IS NOT NULL
    OR whatsapp_initiated_at IS NOT NULL
    OR outreach_replied_at   IS NOT NULL
    OR portal_sessions > 0
    OR engagement_score > 0
    OR last_portal_event_at  IS NOT NULL
    OR pipeline_status IN ('clicked','engaged','whatsapp','appointment','closed_won','closed_lost')
  );

COMMIT;
