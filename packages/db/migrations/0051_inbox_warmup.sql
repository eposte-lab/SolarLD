-- Migration 0051 — inbox warm-up tracking
--
-- Sprint 6.3. A brand-new Google Workspace inbox (or any cold-outreach
-- inbox) must ramp up slowly over 21 days before hitting its steady-
-- state daily cap. Sending 50/day from day 1 is the fastest path to a
-- spam folder that stays there.
--
-- Warm-up curve (per-inbox, per calendar day):
--   Week 1 (days 1-7):   10/day
--   Week 2 (days 8-14):  25/day
--   Week 3 (days 15-21): 40/day
--   Day 22+:             steady-state daily_cap (default 50)
--
-- Two generated columns avoid any cron requirement — the DB always
-- reports the correct phase based on the current date.
--
-- email_style column: controls which template family the OutreachAgent
-- picks for this inbox:
--   "visual_preventivo" — rich HTML with hero image + ROI card (legacy,
--     default for brand/Resend inboxes).
--   "plain_conversational" — 60-80-word plain-text-feel HTML, no images
--     (default for outreach/Gmail inboxes — higher cold B2B reply rate).

BEGIN;

-- NOTE: the original draft of this migration included two STORED generated
-- columns (warmup_phase_day, warmup_completed) derived from warmup_started_at
-- via CURRENT_DATE / NOW(). Postgres rejects those ("generation expression is
-- not immutable" — 42P17) because STORED columns must be deterministic.
-- Those values are now computed on the fly in rate_limit_service from
-- warmup_started_at, which is the single source of truth.

ALTER TABLE tenant_inboxes
    ADD COLUMN IF NOT EXISTS warmup_started_at timestamptz,
    ADD COLUMN IF NOT EXISTS email_style text NOT NULL DEFAULT 'visual_preventivo'
        CHECK (email_style IN ('visual_preventivo','plain_conversational'));

COMMENT ON COLUMN tenant_inboxes.warmup_started_at IS
    'Set on first successful send from this inbox. Drives daily cap '
    'calculation during the 21-day warm-up ramp. NULL = not started yet.';

COMMENT ON COLUMN tenant_inboxes.email_style IS
    'Template family: visual_preventivo (legacy, hero image + ROI card) '
    'or plain_conversational (60-80 words, no images, cold outreach). '
    'Outreach domain inboxes default to plain_conversational after migration.';

-- Backfill: set email_style='plain_conversational' for inboxes already
-- linked to an outreach domain (Sprint 6.2). Brand inboxes keep the default.
UPDATE tenant_inboxes ti
SET email_style = 'plain_conversational'
WHERE EXISTS (
    SELECT 1
    FROM tenant_email_domains ted
    WHERE ted.id = ti.domain_id
      AND ted.purpose = 'outreach'
);

-- Backfill warmup_started_at for inboxes that have sent before (they
-- already did some warm-up; set to 22 days ago so they're in steady state).
UPDATE tenant_inboxes
SET warmup_started_at = NOW() - INTERVAL '22 days'
WHERE last_sent_at IS NOT NULL
  AND warmup_started_at IS NULL;

COMMIT;
