-- 0112 — email_templates: DB-stored HTML templates for generic_outreach campaigns.
--
-- Phase 2 of "campagne custom": operators write custom HTML email bodies
-- in the dashboard, store them as rows in this table, and associate them
-- with a prospect list (campaign_type='generic_outreach').
--
-- When outreach is launched for a generic_outreach list that has
-- email_template_id set, the OutreachAgent renders the stored Jinja2-
-- compatible HTML instead of the standard Solar template family.
--
-- Variables supported (Jinja2 {{ var }}):
--   greeting_name, business_name, hq_address, hq_cap, hq_city,
--   hq_province, phone, recipient_email, sender_first_name,
--   brand_logo_url, tenant_name, tenant_legal_name, tenant_vat_number,
--   tenant_legal_address, unsubscribe_url, tracking_pixel_url
--
-- GDPR-required variables (must be present to save the template):
--   unsubscribe_url, tenant_legal_name, tenant_vat_number,
--   tenant_legal_address

BEGIN;

-- 1) email_templates table.
CREATE TABLE IF NOT EXISTS email_templates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name            VARCHAR(200) NOT NULL,
    subject         VARCHAR(500) NOT NULL,
    html            TEXT         NOT NULL,
    plain_text      TEXT,
    -- JSON array of variable slugs actually referenced in this template,
    -- detected at save time (for the preview + documentation UI).
    variables_used  JSONB        NOT NULL DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 2) Tenant-scoped index: list page sorts by updated_at DESC.
CREATE INDEX IF NOT EXISTS idx_email_templates_tenant
    ON email_templates(tenant_id, updated_at DESC);

-- 3) updated_at trigger (reuse the pattern from other tables).
CREATE OR REPLACE FUNCTION _set_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS set_email_templates_updated_at ON email_templates;
CREATE TRIGGER set_email_templates_updated_at
    BEFORE UPDATE ON email_templates
    FOR EACH ROW EXECUTE FUNCTION _set_updated_at();

-- 4) FK from prospect_lists → email_templates.
--    ON DELETE SET NULL: deleting a template does not cascade-delete lists;
--    the list just reverts to no custom template.
ALTER TABLE prospect_lists
    ADD COLUMN IF NOT EXISTS email_template_id UUID
        REFERENCES email_templates(id) ON DELETE SET NULL;

-- 5) Index for joining lists ↔ templates.
CREATE INDEX IF NOT EXISTS idx_prospect_lists_email_template
    ON prospect_lists(tenant_id, email_template_id)
    WHERE email_template_id IS NOT NULL;

-- 6) Row-level security (same pattern as other tenant tables).
ALTER TABLE email_templates ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "email_templates_tenant_own" ON email_templates;
CREATE POLICY "email_templates_tenant_own" ON email_templates
    USING (tenant_id = auth.uid()::uuid);

COMMIT;
