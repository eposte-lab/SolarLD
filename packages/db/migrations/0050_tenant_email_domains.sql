-- Migration 0050 — tenant_email_domains: multi-domain email setup
--
-- Sprint 6.2. Each tenant may have:
--   * 1 "brand" domain (ex: agenda-pro.it) used for transactional email
--     (Resend) and all user-facing communication.
--   * N "outreach" domains (ex: agendasolar.it, get-agenda.it) used
--     exclusively for cold B2B prospecting via Google Workspace OAuth
--     inboxes. These domains are never used for login / notifications so
--     reputation damage is contained.
--
-- Outreach domains each get a custom tracking host (e.g. go.agendasolar.it
-- → CNAME to track.solarld.app) so the pixel/click URLs look like they
-- come from the sender's own domain.
--
-- This table is the source of truth for:
--   - DNS verification status (SPF / DKIM / DMARC / tracking CNAME)
--   - Per-domain daily soft-cap (sum of all inbox caps ≤ this)
--   - Auto-pause if reputation alarm fires
--
-- Backward compat: existing tenants continue to use tenants.email_from_domain
-- (retained). On first save of a new domain row the migration seeder below
-- backfills a "brand" row from that column and links existing inboxes.

BEGIN;

-- ── tenant_email_domains ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tenant_email_domains (
    id                      uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               uuid        NOT NULL
                                REFERENCES tenants(id) ON DELETE CASCADE,

    -- The domain itself, lowercased and trimmed.
    domain                  text        NOT NULL
                                CHECK (length(domain) > 3 AND domain NOT LIKE '%@%'),

    -- "brand" = transactional (Resend); "outreach" = cold B2B (Gmail/M365).
    purpose                 text        NOT NULL DEFAULT 'outreach'
                                CHECK (purpose IN ('brand','outreach')),

    -- Which send provider handles this domain. Flows to new inboxes
    -- created under it as a default. Per-inbox provider can still be
    -- overridden in tenant_inboxes.provider.
    default_provider        text        NOT NULL DEFAULT 'resend'
                                CHECK (default_provider IN ('resend','gmail_oauth','m365_oauth','smtp')),

    -- Optional per-domain tracking hostname.
    -- When set, outreach emails use this host for pixel + click URLs
    -- instead of the shared track.solarld.app.
    -- Example: "go.agendasolar.it" → CNAME → track.solarld.app
    tracking_host           text,

    -- Resend domain id (null for Gmail outreach domains that don't need Resend).
    resend_domain_id        text,

    -- DNS verification timestamps — null means unverified.
    verified_at             timestamptz,   -- overall (all critical records OK)
    spf_verified_at         timestamptz,
    dkim_verified_at        timestamptz,
    dmarc_verified_at       timestamptz,
    tracking_cname_verified_at timestamptz,

    -- Latest DMARC policy found during verification (none/quarantine/reject).
    dmarc_policy            text
                                CHECK (dmarc_policy IS NULL OR
                                       dmarc_policy IN ('none','quarantine','reject')),

    -- Soft daily send cap: sum of all active inbox caps on this domain
    -- should not exceed this. Informational only; hard enforcement is per-inbox.
    daily_soft_cap          int         NOT NULL DEFAULT 300
                                CHECK (daily_soft_cap > 0 AND daily_soft_cap <= 10000),

    -- Auto-pause: if domain reputation alarms fire, OutreachAgent skips
    -- all inboxes under this domain until paused_until has passed.
    paused_until            timestamptz,
    pause_reason            text,

    -- Last time a live DNS check was run (for "verify now" debounce in UI).
    last_dns_check_at       timestamptz,

    active                  bool        NOT NULL DEFAULT true,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),

    -- One row per (tenant, domain) pair.
    UNIQUE (tenant_id, domain)
);

-- Fast lookup of "active outreach domains for this tenant"
CREATE INDEX IF NOT EXISTS tenant_email_domains_tenant_purpose_idx
    ON tenant_email_domains (tenant_id, purpose, active)
    WHERE active = true;

-- Tracking host lookup in the TenantMiddleware (Sprint 6.2 phase B).
CREATE UNIQUE INDEX IF NOT EXISTS tenant_email_domains_tracking_host_idx
    ON tenant_email_domains (tracking_host)
    WHERE tracking_host IS NOT NULL;


-- ── Link tenant_inboxes to their domain ─────────────────────────────────────

ALTER TABLE tenant_inboxes
    ADD COLUMN IF NOT EXISTS domain_id uuid
        REFERENCES tenant_email_domains(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS tenant_inboxes_domain_id_idx
    ON tenant_inboxes (domain_id)
    WHERE domain_id IS NOT NULL;


-- ── Backfill: create a "brand" domain row for every tenant that has
--             email_from_domain configured, and link existing inboxes ────────

DO $$
DECLARE
    t record;
    new_domain_id uuid;
BEGIN
    FOR t IN
        SELECT id, email_from_domain
        FROM tenants
        WHERE email_from_domain IS NOT NULL AND email_from_domain != ''
    LOOP
        -- Insert brand domain (skip if already exists from a prior run).
        INSERT INTO tenant_email_domains
            (tenant_id, domain, purpose, default_provider,
             verified_at, active)
        VALUES
            (t.id, lower(trim(t.email_from_domain)), 'brand', 'resend',
             -- Mark as already verified (tenant set it up during onboarding).
             now(), true)
        ON CONFLICT (tenant_id, domain) DO NOTHING
        RETURNING id INTO new_domain_id;

        -- If the row already existed, fetch its id.
        IF new_domain_id IS NULL THEN
            SELECT id INTO new_domain_id
            FROM tenant_email_domains
            WHERE tenant_id = t.id AND domain = lower(trim(t.email_from_domain));
        END IF;

        -- Link existing inboxes on this tenant to the brand domain.
        UPDATE tenant_inboxes
        SET domain_id = new_domain_id
        WHERE tenant_id = t.id AND domain_id IS NULL;
    END LOOP;
END;
$$;


-- ── RLS ──────────────────────────────────────────────────────────────────────

ALTER TABLE tenant_email_domains ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_email_domains_tenant_isolation
    ON tenant_email_domains
    FOR ALL
    USING (tenant_id = auth_tenant_id());

GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_email_domains TO authenticated;

COMMIT;
