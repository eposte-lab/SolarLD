-- ============================================================
-- 0044 — acquisition_campaigns
-- ============================================================
--
-- Introduces `acquisition_campaigns` — the strategic entity that
-- describes a tenant's targeting strategy for one acquisition run.
--
-- Each campaign encapsulates:
--   • The five module configs (sorgente, tecnico, economico, outreach, crm)
--     — denormalised here so the campaign is a self-contained snapshot that
--     doesn't drift if the tenant later edits their wizard settings.
--   • Which sending inboxes to use (inbox_ids[] filter, or NULL = all).
--   • A cron schedule for automated re-scans.
--   • A monthly spend cap in euro-cents (NULL = unlimited).
--
-- Relationship to outreach_sends:
--   outreach_sends.acquisition_campaign_id → acquisition_campaigns.id
--   This makes it trivial to answer "all email sends in campaign X" and
--   compute per-campaign ROI / deliverability without a full table scan.
--
-- leads.acquisition_campaign_id is also added so scan candidates and
-- freshly created leads are attributed to the campaign that spawned them,
-- enabling per-campaign funnel analytics.

BEGIN;

-- ── 1. Enum for campaign lifecycle ────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'acquisition_campaign_status') THEN
        CREATE TYPE acquisition_campaign_status AS ENUM (
            'draft',     -- created but not yet activated
            'active',    -- currently running / accepting scans
            'paused',    -- manually paused; resumes on user action
            'archived'   -- ended; historical data preserved
        );
    END IF;
END $$;

-- ── 2. Main table ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS acquisition_campaigns (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    -- Human label (e.g. "Manifatturiero Nord Italia Q3 2026")
    name            TEXT NOT NULL DEFAULT 'Campagna Default',
    description     TEXT,

    -- Snapshot of the 5 wizard module configs at creation/last edit.
    -- Pydantic models in `tenant_module_service.py` define the schema.
    -- Stored as independent JSONB columns (one per module) so partial
    -- updates are cheap and there is no cross-module merge complexity.
    sorgente_config  JSONB NOT NULL DEFAULT '{}'::JSONB,
    tecnico_config   JSONB NOT NULL DEFAULT '{}'::JSONB,
    economico_config JSONB NOT NULL DEFAULT '{}'::JSONB,
    outreach_config  JSONB NOT NULL DEFAULT '{}'::JSONB,
    crm_config       JSONB NOT NULL DEFAULT '{}'::JSONB,

    -- Optional inbox restriction: if non-empty only these inboxes are
    -- used for sends attributed to this campaign. NULL / empty = all
    -- active tenant inboxes (default round-robin).
    inbox_ids        UUID[] DEFAULT NULL,

    -- Cron expression for automated re-scans (NULL = manual only).
    -- Standard 5-field POSIX cron; validated at the API layer.
    schedule_cron    TEXT DEFAULT NULL,

    -- Monthly send budget in euro-cents (NULL = unlimited).
    budget_cap_cents INTEGER DEFAULT NULL CHECK (budget_cap_cents IS NULL OR budget_cap_cents > 0),

    -- Campaigns migrated from tenant_modules get is_default=true.
    -- Each tenant should have at most one default campaign; enforced
    -- with a partial unique index below.
    is_default       BOOLEAN NOT NULL DEFAULT FALSE,

    status           acquisition_campaign_status NOT NULL DEFAULT 'active',

    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── 3. Indexes ────────────────────────────────────────────────────────

-- Primary look-up pattern: all campaigns for a tenant, sorted by newest.
CREATE INDEX IF NOT EXISTS idx_acq_campaigns_tenant
    ON acquisition_campaigns (tenant_id, created_at DESC);

-- Enforce one "default" campaign per tenant.
CREATE UNIQUE INDEX IF NOT EXISTS idx_acq_campaigns_one_default
    ON acquisition_campaigns (tenant_id)
    WHERE is_default = TRUE;

-- ── 4. auto updated_at trigger ────────────────────────────────────────
CREATE TRIGGER trg_acq_campaigns_updated_at
    BEFORE UPDATE ON acquisition_campaigns
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── 5. RLS ────────────────────────────────────────────────────────────
ALTER TABLE acquisition_campaigns ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS acquisition_campaigns_all ON acquisition_campaigns;
CREATE POLICY acquisition_campaigns_all ON acquisition_campaigns
    FOR ALL
    USING  (tenant_id = auth_tenant_id() OR auth.role() = 'service_role')
    WITH CHECK (tenant_id = auth_tenant_id() OR auth.role() = 'service_role');

GRANT SELECT, INSERT, UPDATE, DELETE ON acquisition_campaigns TO authenticated;

-- ── 6. FK on outreach_sends ───────────────────────────────────────────
-- Attribute each individual send to the acquisition campaign that drove it.
-- ON DELETE SET NULL: deleting a campaign preserves send history.
ALTER TABLE outreach_sends
    ADD COLUMN IF NOT EXISTS acquisition_campaign_id UUID
        REFERENCES acquisition_campaigns(id) ON DELETE SET NULL;

-- The main analytics query: "all sends in campaign X" → must be fast.
CREATE INDEX IF NOT EXISTS idx_outreach_sends_campaign
    ON outreach_sends (acquisition_campaign_id)
    WHERE acquisition_campaign_id IS NOT NULL;

-- ── 7. FK on leads ────────────────────────────────────────────────────
-- Track which campaign spawned this lead (set by the scanner/hunter).
ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS acquisition_campaign_id UUID
        REFERENCES acquisition_campaigns(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_leads_campaign
    ON leads (acquisition_campaign_id)
    WHERE acquisition_campaign_id IS NOT NULL;

-- ── 8. FK on scan_candidates ──────────────────────────────────────────
-- Candidates found during a campaign scan — links raw discoveries back
-- to the strategy that produced them.
ALTER TABLE scan_candidates
    ADD COLUMN IF NOT EXISTS acquisition_campaign_id UUID
        REFERENCES acquisition_campaigns(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_scan_candidates_campaign
    ON scan_candidates (acquisition_campaign_id)
    WHERE acquisition_campaign_id IS NOT NULL;

COMMIT;
