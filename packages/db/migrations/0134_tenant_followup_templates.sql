-- 0134_tenant_followup_templates.sql
--
-- Aggiunge tenants.followup_templates: override per-tenant dei 4
-- template del compositore follow-up (oggetto + corpo). Forma:
--   { "<template_id>": { "subject": "...", "body": "..." }, ... }
-- NULL = usa i default hardcoded del dashboard. Modificabile dalla
-- pagina Impostazioni → Template follow-up.

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS followup_templates JSONB;
