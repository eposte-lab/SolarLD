-- 0115 — tenant-level outreach kill-switch.
--
-- Adds `outreach_blocked` to tenants. When TRUE the OutreachAgent
-- intercepts the call to the email provider (Resend) and records the
-- send as `status='blocked_demo'` in `outreach_sends`, transitioning
-- the lead to pipeline_status='sent' + outreach_sent_at=now() WITHOUT
-- actually contacting the prospect. Everything else (rendering, A/B
-- accounting, follow-up scheduling, telemetry) runs identically to a
-- normal tenant.
--
-- Use case: customer-facing demos on the production tenant. The whole
-- pipeline is exercised end-to-end exactly as it will be for real
-- clients, but no email leaves the system.
--
-- Default FALSE so production behaviour is unchanged for existing
-- tenants. The flag is operator-toggleable via direct DB update; no
-- API endpoint exposed (intentionally — this is a safety mechanism,
-- not a tenant-tunable setting).

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS outreach_blocked BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN tenants.outreach_blocked IS
  'When TRUE, OutreachAgent records sends as status=blocked_demo in outreach_sends and never calls the email provider. Used for customer-facing demos on production data. Default FALSE.';

-- Partial index: only blocked tenants pay the lookup cost; the common
-- case (false) skips the index entirely via planner heuristics.
CREATE INDEX IF NOT EXISTS idx_tenants_outreach_blocked
  ON tenants(id)
  WHERE outreach_blocked = TRUE;
