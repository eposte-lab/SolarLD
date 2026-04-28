-- ============================================================
-- 0069 — Tenant custom HTML email template
-- ============================================================
-- Sprint 9 Fase C.1.
--
-- Tenants with an in-house designer (or a strong brand identity)
-- want to control the entire email HTML, not just copy variables.
-- We let them upload a Jinja2-compatible HTML template that becomes
-- the rendering source for their outreach emails, subject to:
--
--   1. Jinja2 syntax validation (no parse errors).
--   2. Required-variable presence check — certain GDPR-mandatory
--      placeholders MUST be in the template or we reject the upload
--      (e.g. {{ unsubscribe_url }}, {{ tracking_pixel_url }},
--       {{ tenant_legal_name }}, {{ tenant_vat_number }},
--       {{ tenant_legal_address }}).
--   3. bleach HTML sanitization — <script>, <iframe>, onclick=, and
--      javascript: hrefs are stripped before storage.
--
-- The uploaded file is stored at:
--   branding/{tenant_id}/email_template.html.j2
-- inside the existing `branding` Storage bucket (already has a
-- tenant-scoped RLS policy via storage.foldername).
--
-- Fallback resolution order (email_template_service.py):
--   1. custom_email_template_active=true  → custom template
--   2. template_family='premium'          → outreach_solarld_premium.html.j2
--   3. else                               → legacy (outreach_b2b*.j2 / conversational)

BEGIN;

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS custom_email_template_path        TEXT,
  ADD COLUMN IF NOT EXISTS custom_email_template_uploaded_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS custom_email_template_active      BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN tenants.custom_email_template_path IS
  'Sprint 9 Fase C — Storage path inside the branding bucket: '
  '"branding/{tenant_id}/email_template.html.j2". Set by '
  'POST /v1/branding/email-template, cleared by DELETE.';

COMMENT ON COLUMN tenants.custom_email_template_active IS
  'Sprint 9 Fase C — When true the email_template_service uses the '
  'custom HTML over the SolarLead premium / legacy fallbacks. The '
  'tenant can toggle this from the dashboard without re-uploading.';

-- template_family column controls the non-custom fallback path.
-- Values: 'premium' (new default), 'legacy_visual', 'plain_conversational'
ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS email_template_family TEXT
    NOT NULL DEFAULT 'premium'
    CHECK (email_template_family IN ('premium', 'legacy_visual', 'plain_conversational'));

COMMENT ON COLUMN tenants.email_template_family IS
  'Sprint 9 Fase C — Selects the SolarLead-provided fallback template '
  'family when custom_email_template_active=false. "premium" uses '
  'outreach_solarld_premium.html.j2; "legacy_visual" uses the old '
  'outreach_b2b.html.j2; "plain_conversational" uses the 60-80 word '
  'cold-B2B family from Sprint 6.3.';

COMMIT;
