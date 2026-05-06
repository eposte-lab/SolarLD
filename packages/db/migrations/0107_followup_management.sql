-- 0107_followup_management.sql
--
-- Make follow-up auto-cron toggleable per tenant + add the conflict
-- prevention plumbing that lets the cron skip leads with a recent
-- manual follow-up.
--
-- Also fixes a missing column referenced in the dashboard /leads
-- engagement filter (commit 0aea429): leads.outreach_replied_at, set
-- when a `lead_replies` row is inserted (handled in API/service code,
-- not by a DB trigger — keeps the data flow visible in the codebase).

-- 1) Tenant-level toggle. Default TRUE so existing tenants keep their
--    current behaviour (cold cadence + engagement scenarios run).
ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS followup_auto_enabled BOOLEAN NOT NULL DEFAULT TRUE;

-- 2) Mark a send as manually triggered by the operator. The cold-cadence
--    cron uses this in addition to the (lead_id, sequence_step) dedup to
--    decide whether to fire the next step.
ALTER TABLE outreach_sends
  ADD COLUMN IF NOT EXISTS is_manual BOOLEAN NOT NULL DEFAULT FALSE;

-- 3) Reply-tracking column on leads. Populated by the API when a
--    `lead_replies` row is inserted (see routes/replies.py). Used by
--    the /leads engagement filter to surface leads who answered the
--    outreach email.
ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS outreach_replied_at TIMESTAMPTZ;

-- 3a) Backfill existing replies — for every lead with at least one reply,
--     set outreach_replied_at to the most recent received_at.
UPDATE leads l
SET outreach_replied_at = sub.last_reply_at
FROM (
  SELECT lead_id, MAX(received_at) AS last_reply_at
  FROM lead_replies
  GROUP BY lead_id
) sub
WHERE l.id = sub.lead_id
  AND l.outreach_replied_at IS NULL;

-- 4) Index for "has the lead recently received a manual follow-up?"
--    queries used by the cron skip logic.
CREATE INDEX IF NOT EXISTS idx_leads_last_followup_recent
  ON leads(tenant_id, last_followup_sent_at DESC)
  WHERE last_followup_sent_at IS NOT NULL;

-- 5) Index for the /leads engagement filter (replied_at lookup).
CREATE INDEX IF NOT EXISTS idx_leads_replied_at
  ON leads(tenant_id, outreach_replied_at DESC)
  WHERE outreach_replied_at IS NOT NULL;
