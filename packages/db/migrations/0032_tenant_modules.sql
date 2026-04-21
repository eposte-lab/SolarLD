-- 0032 — Modular wizard: per-tenant configurable modules.
--
-- Replaces the monolithic `tenant_configs` row with 5 independent JSONB
-- config rows — one per module. The wizard becomes a sequence of
-- module-specific forms, each independently skippable and editable
-- post-onboarding from `/settings/modules/<key>`.
--
-- Modules:
--   sorgente  — discovery source (ATECO + size + geo for B2B;
--               CAP income bands for B2C)
--   tecnico   — roof technical thresholds (kW, m², exposure) + Solar
--               gate fraction
--   economico — pricing + scan budget cap + ROI target
--   outreach  — active channels (email/postal/WA/Meta), tone, CTA
--   crm       — pipeline labels + webhooks + HMAC + SLA
--
-- Why 5 rows instead of 1 wide row: each module has its own validation
-- schema, its own "active" toggle, and its own audit trail. Editing
-- Outreach shouldn't touch Sorgente's `updated_at`. Also makes it
-- trivial to add a 6th module (e.g. `analytics`) without a schema
-- migration — just a new CHECK value.
--
-- Coexistence: `tenant_configs` stays for legacy callers during the
-- Phase 2 cut-over. New scans (scan_mode='b2b_funnel_v2') read from
-- `tenant_modules`; legacy scan modes keep reading `tenant_configs`.
-- Phase 4 retires `tenant_configs` once all tenants have migrated.

BEGIN;

CREATE TABLE IF NOT EXISTS tenant_modules (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    module_key  text NOT NULL CHECK (module_key IN (
        'sorgente','tecnico','economico','outreach','crm'
    )),
    -- Free-form per-module config. Pydantic schemas in
    -- `tenant_module_service.py` validate shape on write.
    config      jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Inactive modules are skipped by the runtime (e.g. outreach
    -- disabled → no emails/letters sent, but scans still run).
    active      boolean NOT NULL DEFAULT true,
    -- Bumped on every upsert — used by optimistic-lock UI to detect
    -- concurrent edits from two browser tabs.
    version     int NOT NULL DEFAULT 1,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),

    UNIQUE (tenant_id, module_key)
);

CREATE INDEX IF NOT EXISTS idx_tenant_modules_tenant
    ON tenant_modules (tenant_id);

-- Bump `updated_at` + `version` on every UPDATE automatically so
-- service-layer code can't forget. Uses the standard Supabase idiom.
CREATE OR REPLACE FUNCTION tenant_modules_touch() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    -- Only bump version if config actually changed — pure `active`
    -- toggles don't count as a schema-invalidating edit.
    IF NEW.config IS DISTINCT FROM OLD.config THEN
        NEW.version := OLD.version + 1;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tenant_modules_touch ON tenant_modules;
CREATE TRIGGER trg_tenant_modules_touch
    BEFORE UPDATE ON tenant_modules
    FOR EACH ROW EXECUTE FUNCTION tenant_modules_touch();

-- ---------------------------------------------------------------------------
-- RLS — tenants see only their own module rows.
-- ---------------------------------------------------------------------------

ALTER TABLE tenant_modules ENABLE ROW LEVEL SECURITY;

-- Service role bypasses RLS (the API always runs with service role).
-- The policy below covers direct anon/authenticated access if ever
-- wired (today only service role touches this table).
DROP POLICY IF EXISTS tenant_modules_own ON tenant_modules;
CREATE POLICY tenant_modules_own ON tenant_modules
    FOR ALL
    USING (tenant_id = auth.uid() OR auth.role() = 'service_role')
    WITH CHECK (tenant_id = auth.uid() OR auth.role() = 'service_role');

-- ---------------------------------------------------------------------------
-- Backfill: one row per module per existing tenant with sane defaults.
-- Config JSON shapes are documented in `tenant_module_service.py`
-- (pydantic models). Keep these in sync.
-- ---------------------------------------------------------------------------

INSERT INTO tenant_modules (tenant_id, module_key, config, active)
SELECT
    t.id,
    mk,
    CASE mk
        WHEN 'sorgente' THEN jsonb_build_object(
            'ateco_codes', '[]'::jsonb,
            'min_employees', 20,
            'max_employees', 250,
            'min_revenue_eur', 2000000,
            'max_revenue_eur', 50000000,
            'province', '[]'::jsonb,
            'regioni', '[]'::jsonb,
            'cap', '[]'::jsonb,
            -- B2C-only fields; ignored when scan_mode is B2B.
            'reddito_min_eur', 35000,
            'case_unifamiliari_pct_min', 40
        )
        WHEN 'tecnico' THEN jsonb_build_object(
            'min_kwp', 50,
            'min_area_sqm', 500,
            'max_shading', 0.4,
            'min_exposure_score', 0.7,
            'orientamenti_ok', jsonb_build_array('S','SE','SO','E','O'),
            'solar_gate_pct', 0.20,
            'solar_gate_min_candidates', 20
        )
        WHEN 'economico' THEN jsonb_build_object(
            'ticket_medio_eur', 25000,
            'roi_target_years', 6,
            'budget_scan_eur', 50,
            'budget_outreach_eur_month', 2000
        )
        WHEN 'outreach' THEN jsonb_build_object(
            'channels', jsonb_build_object(
                'email', true,
                'postal', false,
                'whatsapp', false,
                'meta_ads', false
            ),
            'tone_of_voice', 'professionale-diretto',
            'cta_primary', 'Prenota un sopralluogo gratuito'
        )
        WHEN 'crm' THEN jsonb_build_object(
            'webhook_url', null,
            'webhook_secret', null,
            'pipeline_labels', jsonb_build_array(
                'nuovo','contattato','in-valutazione','preventivo','chiuso'
            ),
            'sla_hours_first_touch', 24
        )
    END,
    -- Legacy tenants get modules pre-activated so flows don't break,
    -- except Meta/postal which need extra setup.
    true
FROM tenants t
CROSS JOIN (VALUES ('sorgente'),('tecnico'),('economico'),('outreach'),('crm')) AS m(mk)
ON CONFLICT (tenant_id, module_key) DO NOTHING;

COMMIT;
