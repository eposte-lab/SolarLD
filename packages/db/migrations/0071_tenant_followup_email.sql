-- Migration 0071: dedicated follow-up sender address per tenant
--
-- Adds `followup_from_email` — an optional full email address used
-- as the FROM for manual and automated follow-up sends.
-- When null, all sends fall back to `outreach@{email_from_domain}`.
--
-- Example: 'Rossi Energia <followup@rossi-energia.it>'
-- or just 'followup@rossi-energia.it' — both are accepted.
--
-- The address must be on a domain that the tenant has verified with
-- their email provider (Resend / Gmail OAuth) — no automated validation
-- here, it is the operator's responsibility.

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS followup_from_email TEXT;

COMMENT ON COLUMN tenants.followup_from_email IS
  'Optional dedicated FROM address for follow-up emails. '
  'Falls back to outreach@{email_from_domain} when null.';
