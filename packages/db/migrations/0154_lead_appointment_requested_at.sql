-- 0154_lead_appointment_requested_at.sql
-- Authoritative "ha richiesto contatto" signal + retroactive engagement floor.
--
-- A lead who submits the in-portal contact/appointment form is the strongest
-- hand-raise in the funnel. Until now that action gave ZERO engagement boost:
-- engagement_service scored only portal_events + email opens/clicks + bolletta,
-- and the +50 portal.appointment_click weight was never fired by the client.
--
-- This migration adds the authoritative timestamp the score now reads (it
-- floors such a lead to "caldo", independent of the 30-day portal-events
-- window) and backfills it for everyone who has ALREADY requested contact:
--   1) unmoderated / approved → events row `lead.appointment_requested`
--   2) moderated (held or decided) → pending_inbound_requests row
--   3) legacy parked leads → pipeline_status='appointment' fallback
-- then floors their engagement_score so past requesters read as "caldo" too.
BEGIN;

ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS appointment_requested_at TIMESTAMPTZ;

COMMENT ON COLUMN leads.appointment_requested_at IS
  'When the lead submitted the in-portal contact/appointment form. Authoritative hand-raise signal: drives the engagement hot-floor in engagement_service.compute_score. Stamped by routes/public.py (appointment endpoint, moderated + normal) and routes/admin.py (moderated approval).';

-- Partial index — the score recompute / hot-lead queries filter on "has a
-- request". Small set, so a partial index keeps it cheap.
CREATE INDEX IF NOT EXISTS idx_leads_appointment_requested_at
  ON leads (appointment_requested_at)
  WHERE appointment_requested_at IS NOT NULL;

-- 1) Backfill from the events stream (unmoderated tenants + approved
--    moderated requests both emit lead.appointment_requested).
WITH ev AS (
  SELECT lead_id, MIN(occurred_at) AS at
  FROM events
  WHERE event_type = 'lead.appointment_requested'
  GROUP BY lead_id
)
UPDATE leads l
SET appointment_requested_at = ev.at
FROM ev
WHERE l.id = ev.lead_id
  AND l.appointment_requested_at IS NULL;

-- 2) Backfill from the moderation inbound queue — covers moderated tenants
--    (e.g. Total Trade) whose requests sit in pending_inbound_requests and
--    never emitted an event while pending.
WITH pir AS (
  SELECT lead_id, MIN(created_at) AS at
  FROM pending_inbound_requests
  GROUP BY lead_id
)
UPDATE leads l
SET appointment_requested_at = pir.at
FROM pir
WHERE l.id = pir.lead_id
  AND l.appointment_requested_at IS NULL;

-- 3) Fallback: leads parked in pipeline_status='appointment' with neither an
--    event nor a queue row (legacy / manually advanced). Best-effort time.
UPDATE leads
SET appointment_requested_at = COALESCE(updated_at, created_at, now())
WHERE pipeline_status = 'appointment'
  AND appointment_requested_at IS NULL;

-- 4) Floor the engagement score for every backfilled requester so past
--    contact requests immediately read as "caldo" (>=60). compute_score
--    keeps this floor (APPOINTMENT_HOT_FLOOR=70) going forward.
UPDATE leads
SET engagement_score = GREATEST(COALESCE(engagement_score, 0), 70),
    engagement_peak_score = GREATEST(COALESCE(engagement_peak_score, 0), 70),
    engagement_score_updated_at = now()
WHERE appointment_requested_at IS NOT NULL;

COMMIT;
