-- ============================================================
-- 0119 — tenant EPC / webhook / branding settings
-- ============================================================
-- Adds three per-tenant fields needed for the client-feedback sprint:
--
--   epc_enabled              Toggle the EPC commercial section on the
--                            lead portal (Energy Performance Contract).
--                            When true, the portal renders an animated
--                            "zero investimento" proposition powered by
--                            the tenant's brand name.
--
--   appointment_webhook_url  If set, the public API POSTs appointment
--                            form submissions to this URL (CRM webhook:
--                            HubSpot, Pipedrive, n8n, Zapier, …).
--                            Fail-open: a webhook timeout never blocks
--                            the lead from seeing the confirmation.
--
--   privacy_policy_url       Tenant-specific privacy policy URL shown
--                            in the GDPR consent checkbox on the
--                            appointment form. Falls back to "/privacy"
--                            if null.

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS epc_enabled              BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS appointment_webhook_url  TEXT,
  ADD COLUMN IF NOT EXISTS privacy_policy_url       TEXT;

COMMENT ON COLUMN tenants.epc_enabled IS
  'Show EPC (Energy Performance Contract) commercial section on the lead portal';

COMMENT ON COLUMN tenants.appointment_webhook_url IS
  'Webhook URL for appointment form submissions (CRM integration). POST, fail-open, 5s timeout.';

COMMENT ON COLUMN tenants.privacy_policy_url IS
  'Tenant privacy policy URL shown in GDPR consent on the portal appointment form. Default: /privacy';
