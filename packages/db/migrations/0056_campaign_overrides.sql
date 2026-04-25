-- ============================================================
-- 0056 — campaign_overrides (Sprint 3)
-- ============================================================
-- Temporary configuration overrides scoped to an acquisition
-- campaign and a time window.
--
-- Why overrides exist:
--   A campaign typically has a stable config but sometimes the operator
--   needs a short-run variation: "test a different email for 3 days",
--   "scan only CAP 80017 this week", "A/B the subject line for 48 h".
--
--   Without overrides the user edits the campaign config (losing the
--   original) or creates a duplicate (doubles confusion + metrics).
--
--   With overrides the base config stays intact; the runtime
--   (OutreachAgent) shallow-merges the active override patch on top
--   of the base before sending.
--
-- Patch semantics:
--   `patch` is a JSONB shallow merge applied to the config block
--   identified by `override_type`:
--     mail       → merged into outreach_config
--     geo_subset → merged into sorgente_config
--     ab_test    → signals agent to route to experiment_id
--     all        → merged into all 5 module configs (key-level)
--
-- Duration limit: end_at <= start_at + 90 days (validated at API layer).

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type WHERE typname = 'campaign_override_type'
    ) THEN
        CREATE TYPE campaign_override_type AS ENUM (
            'mail', 'geo_subset', 'ab_test', 'all'
        );
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS campaign_overrides (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id     UUID NOT NULL REFERENCES acquisition_campaigns(id) ON DELETE CASCADE,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    label           TEXT NOT NULL DEFAULT '',
    override_type   campaign_override_type NOT NULL DEFAULT 'all',
    start_at        TIMESTAMPTZ NOT NULL,
    end_at          TIMESTAMPTZ NOT NULL,
    patch           JSONB NOT NULL DEFAULT '{}'::JSONB,
    experiment_id   UUID REFERENCES template_experiments(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    CONSTRAINT campaign_overrides_window_valid CHECK (
        end_at > start_at
        AND end_at <= start_at + INTERVAL '90 days'
    )
);

-- Hot path: resolve active overrides at runtime for a given campaign.
-- (Plain index, not partial — `now()` is not IMMUTABLE so cannot appear
-- in an index predicate. The composite is still a tight scan.)
CREATE INDEX IF NOT EXISTS idx_campaign_overrides_active
    ON campaign_overrides (campaign_id, start_at, end_at);

CREATE INDEX IF NOT EXISTS idx_campaign_overrides_tenant
    ON campaign_overrides (tenant_id, created_at DESC);

ALTER TABLE campaign_overrides ENABLE ROW LEVEL SECURITY;

-- Tenants see only their own rows; service role bypasses RLS.
CREATE POLICY campaign_overrides_tenant_isolation
    ON campaign_overrides
    FOR ALL
    USING (tenant_id = auth_tenant_id());

GRANT SELECT, INSERT, UPDATE, DELETE ON campaign_overrides TO authenticated;

COMMIT;
