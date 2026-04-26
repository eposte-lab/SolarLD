-- Migration 0061 — quarantine_emails (Task 16: content validation gate)
--
-- Stores emails that failed content validation before send.
-- Every quarantined email gets a row here so ops can:
--   1. Review the violations and the email content.
--   2. Approve (mark for resend) or reject (discard).
--   3. Fix the template / copy that caused the violation.
--
-- This is NOT a send log — successful sends go in outreach_sends.
-- This is a pre-send holding area for policy-failed content.
--
-- RLS: tenants can see their own quarantine; service-role can see all.
-- Super-admin review is done via the service role (admin dashboard).

BEGIN;

CREATE TABLE IF NOT EXISTS quarantine_emails (
  id                  UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           UUID          NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  lead_id             UUID          REFERENCES leads(id) ON DELETE SET NULL,

  -- Email content at the time of quarantine (snapshot).
  subject             TEXT          NOT NULL,
  html_snippet        TEXT,                        -- first 1000 chars of HTML body
  text_snippet        TEXT,                        -- first 400 chars of plain text
  email_style         TEXT          NOT NULL DEFAULT 'visual_preventivo',
  sequence_step       INT           NOT NULL DEFAULT 1,

  -- Validation outcome
  validation_score    NUMERIC(5, 3) NOT NULL DEFAULT 0,
  violations          JSONB         NOT NULL DEFAULT '[]',
                                    -- [{rule, field, detail, severity}, ...]
  auto_decision       TEXT          NOT NULL DEFAULT 'quarantine'
                        CHECK (auto_decision IN ('quarantine')),
                        -- Always 'quarantine' for now; future: 'reject' for
                        -- policy-hard-blocked content (e.g. prohibited ATECO).

  -- Manual review by ops
  review_status       TEXT          NOT NULL DEFAULT 'pending_review'
                        CHECK (review_status IN ('pending_review', 'approved', 'rejected')),
  reviewed_at         TIMESTAMPTZ,
  reviewed_by         UUID          REFERENCES auth.users(id) ON DELETE SET NULL,
  review_notes        TEXT,         -- optional ops notes for the audit trail

  -- For approved emails: track if they were re-sent after approval.
  resent_at           TIMESTAMPTZ,
  resent_outreach_id  UUID          REFERENCES outreach_sends(id) ON DELETE SET NULL,

  created_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ   NOT NULL DEFAULT now()
);

COMMENT ON TABLE quarantine_emails IS
  'Emails blocked by content_validator.py before send. Ops reviews here.';
COMMENT ON COLUMN quarantine_emails.html_snippet IS
  'First 1000 characters of the rendered HTML — enough for ops to spot the issue without storing the full email.';
COMMENT ON COLUMN quarantine_emails.violations IS
  'JSON array of ValidationViolation dicts: [{rule, field, detail, severity}, ...]. See content_validator.py for schema.';
COMMENT ON COLUMN quarantine_emails.resent_outreach_id IS
  'Populated by OutreachAgent when the approved quarantine email is actually sent.';

-- Indexes for dashboard queries
CREATE INDEX IF NOT EXISTS idx_quarantine_emails_tenant_id
  ON quarantine_emails (tenant_id);
CREATE INDEX IF NOT EXISTS idx_quarantine_emails_review_status
  ON quarantine_emails (tenant_id, review_status)
  WHERE review_status = 'pending_review';
CREATE INDEX IF NOT EXISTS idx_quarantine_emails_lead_id
  ON quarantine_emails (lead_id)
  WHERE lead_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_quarantine_emails_created_at
  ON quarantine_emails (created_at DESC);

-- auto-update updated_at
CREATE OR REPLACE TRIGGER trg_quarantine_emails_updated_at
  BEFORE UPDATE ON quarantine_emails
  FOR EACH ROW EXECUTE FUNCTION moddatetime(updated_at);

-- RLS
ALTER TABLE quarantine_emails ENABLE ROW LEVEL SECURITY;

-- Tenants see their own quarantine rows.
CREATE POLICY "tenant_quarantine_select" ON quarantine_emails
  FOR SELECT USING (tenant_id = auth_tenant_id());

-- Only service-role writes (OutreachAgent uses service-role client).
-- No INSERT/UPDATE policy for authenticated role — ops reviews happen
-- via admin endpoints (service-role bypass).

COMMIT;
