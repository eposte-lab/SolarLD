-- ============================================================
-- 0045 — Seed default acquisition_campaigns from tenant_modules
-- ============================================================
--
-- For every existing tenant that has at least one tenant_modules row,
-- create ONE "Campagna Default" acquisition_campaign by aggregating
-- their five module configs into the new table.
--
-- Tenants created after this migration get their default campaign
-- automatically via the route layer (POST /v1/acquisition-campaigns
-- with is_default=true, or auto-created on first login).
--
-- Idempotent: the partial unique index on (tenant_id WHERE is_default)
-- means re-running this migration no-ops if the row already exists.
--
-- Historical outreach_sends rows are NOT back-filled with
-- acquisition_campaign_id — they remain NULL (meaning "pre-campaign
-- era"). Analytics queries filter NULL to mean "organic / legacy send".
--
-- Impl note: we pivot `tenant_modules` into one row per tenant using
-- `jsonb_object_agg(module_key, config)`, then read each module out
-- with `->`. `MAX(jsonb)` is NOT available in Postgres — aggregating
-- by key-value is the portable approach.

BEGIN;

INSERT INTO acquisition_campaigns (
    tenant_id,
    name,
    is_default,
    status,
    sorgente_config,
    tecnico_config,
    economico_config,
    outreach_config,
    crm_config
)
SELECT
    t.tenant_id,
    'Campagna Default'                               AS name,
    TRUE                                             AS is_default,
    'active'                                         AS status,
    COALESCE(t.modules->'sorgente',  '{}'::JSONB)    AS sorgente_config,
    COALESCE(t.modules->'tecnico',   '{}'::JSONB)    AS tecnico_config,
    COALESCE(t.modules->'economico', '{}'::JSONB)    AS economico_config,
    COALESCE(t.modules->'outreach',  '{}'::JSONB)    AS outreach_config,
    COALESCE(t.modules->'crm',       '{}'::JSONB)    AS crm_config
FROM (
    -- Pivot the 5 module rows into one wide row per tenant.
    SELECT
        tenant_id,
        jsonb_object_agg(module_key, config) AS modules
    FROM tenant_modules
    GROUP BY tenant_id
) AS t
ON CONFLICT DO NOTHING;   -- partial unique index on (tenant_id) WHERE is_default

COMMIT;
