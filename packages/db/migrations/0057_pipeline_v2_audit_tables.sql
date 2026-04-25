-- ============================================================
-- 0057 — Pipeline v2 audit tables + GDPR enforcement scaffolding
-- ============================================================
-- Foundational schema for the 9-phase GDPR-compliant lead
-- acquisition pipeline (replaces the legacy "scan → enrich →
-- score → outreach" linear flow).
--
-- This migration is purely ADDITIVE:
--   * No data is moved, no existing column is dropped, no policy
--     is changed on existing tables.
--   * `tenants.pipeline_version` defaults to 1 (legacy). The new
--     orchestrator only kicks in when an operator flips a tenant
--     to version 2.
--   * Backward-compatible: legacy code paths (e.g. agents/outreach,
--     hunter funnel) keep working untouched.
--
-- What this introduces:
--   1. `email_extraction_log`  → per-email provenance record
--      (Atoka vs scraping vs MX-pattern), required by GDPR for
--      "where did you get my address" replies.
--   2. `email_blacklist`       → tenant + global suppression list
--      (hard bounce, complaint, optout, manual ban).
--   3. `domain_blacklist`      → catch-all / dispose-mail / blocked
--      registrar domains; blocks at extraction time, before send.
--   4. `deliverability_metrics_daily` → aggregated daily stats per
--      (tenant, domain, mailbox) — feeds the deliverability dashboard
--      and reputation enforcement worker.
--   5. `lead_rejection_log`    → audit trail of every lead rejected
--      by the 6 offline filters (consumi, proprietà, affidabilità,
--      trend, sede operativa, anti-uffici) with the rule label.
--      We need it to (a) prove non-discriminatory targeting and
--      (b) tune filter thresholds without re-running the pipeline.
--   6. `leads.cluster_signature` → geographic + segment fingerprint
--      used by the new orchestrator to diversify across territories
--      (avoid hitting 80017 ten times in a row).
--   7. `tenants.pipeline_version` → feature flag to opt a tenant
--      into the 9-phase pipeline.
--   8. `api_usage_log.phase`   → pipeline phase tag (e.g. "phase2",
--      "phase4"); enables per-phase cost analytics. NULL on legacy
--      rows; populated only by the v2 orchestrator.

BEGIN;

-- pgcrypto provides digest() for email_blacklist.email_hash. Most
-- environments have it; ensure it's present before the tables that
-- reference it are created.
CREATE EXTENSION IF NOT EXISTS pgcrypto;


-- ============================================================
-- 1. email_extraction_log — provenance of every contact
-- ============================================================
-- Why: GDPR art. 14 obliges us to declare the source of personal
-- data on request. We must be able to answer "this email came from
-- Atoka, retrieved on 2026-04-25" within 30 days.
--
-- One row PER (tenant, lead, email) extraction attempt. Successful
-- and failed extractions are both logged — failures matter for the
-- audit trail ("we tried Atoka first, fell back to scraping").

CREATE TABLE IF NOT EXISTS email_extraction_log (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    lead_id         UUID REFERENCES leads(id) ON DELETE SET NULL,
    -- Subject identity (denormalised so the row stays useful even
    -- after the lead is deleted/archived):
    company_name    TEXT,
    domain          TEXT,
    extracted_email TEXT,
    -- Source of the email. Whitelist enforced via CHECK so a typo
    -- in code can't pollute the audit trail.
    source          TEXT NOT NULL CHECK (source IN (
        'atoka',           -- paid Italian business DB lookup
        'website_scrape',  -- mailto: / contact form on tenant site
        'linkedin',        -- LinkedIn Sales Navigator (manual import)
        'manual',          -- operator typed it
        'pec_registry',    -- Camera di Commercio PEC public registry
        'failed'           -- no email found anywhere → row is the failure record
    )),
    confidence      NUMERIC(3, 2),  -- 0..1 — meaningful for scrape only
    cost_cents      INTEGER NOT NULL DEFAULT 0,
    -- Free-form provider response snippet for forensics. Keep small.
    raw_response    JSONB NOT NULL DEFAULT '{}'::JSONB,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_email_extraction_tenant_time
    ON email_extraction_log (tenant_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_email_extraction_email_lookup
    ON email_extraction_log (extracted_email)
    WHERE extracted_email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_email_extraction_lead
    ON email_extraction_log (lead_id)
    WHERE lead_id IS NOT NULL;

ALTER TABLE email_extraction_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY email_extraction_log_tenant_isolation
    ON email_extraction_log
    FOR ALL
    USING (tenant_id = auth_tenant_id());

GRANT SELECT, INSERT ON email_extraction_log TO authenticated;
GRANT USAGE, SELECT ON SEQUENCE email_extraction_log_id_seq TO authenticated;


-- ============================================================
-- 2. email_blacklist — never-contact list
-- ============================================================
-- Why: hard bounce / complaint / explicit unsubscribe must propagate
-- across all campaigns, all sequence steps, all future scans.
-- Today we rely on `subjects.unsubscribed_at` and ad-hoc checks; this
-- gives a single source of truth indexed for fast pre-send lookup.
--
-- Scope:
--   * tenant_id NULL  → GLOBAL ban (e.g. Resend reported a hard bounce
--                       that we want to honour cross-tenant)
--   * tenant_id set   → tenant-scoped ban (one tenant's mistake doesn't
--                       brick another tenant's pipeline)
--
-- Email is stored case-insensitive lowercased + a stable hash so we
-- can also dedupe by the hash without exposing the plaintext in logs.

CREATE TABLE IF NOT EXISTS email_blacklist (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       UUID REFERENCES tenants(id) ON DELETE CASCADE,
    email           TEXT NOT NULL,
    email_hash      TEXT GENERATED ALWAYS AS (
        encode(digest(lower(email), 'sha256'), 'hex')
    ) STORED,
    reason          TEXT NOT NULL CHECK (reason IN (
        'hard_bounce',
        'soft_bounce_repeated',
        'spam_complaint',
        'unsubscribe',
        'manual_ban',
        'role_account',          -- info@, sales@, noreply@ — caught by extractor
        'disposable'             -- 10minutemail / mailinator / etc.
    )),
    source          TEXT,        -- e.g. "resend_webhook", "user_action"
    notes           TEXT,
    blacklisted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- One row per (tenant, email). Global rows have tenant_id NULL
    -- and rely on the partial unique index below.
    UNIQUE (tenant_id, email)
);

-- Partial unique index for global rows (tenant_id IS NULL) — Postgres
-- treats NULLs as distinct in UNIQUE so we need this to enforce a
-- single global ban per address.
CREATE UNIQUE INDEX IF NOT EXISTS idx_email_blacklist_global_unique
    ON email_blacklist (email)
    WHERE tenant_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_email_blacklist_lookup
    ON email_blacklist (email_hash);
CREATE INDEX IF NOT EXISTS idx_email_blacklist_tenant_time
    ON email_blacklist (tenant_id, blacklisted_at DESC)
    WHERE tenant_id IS NOT NULL;

ALTER TABLE email_blacklist ENABLE ROW LEVEL SECURITY;

-- Tenants see their rows + global rows (tenant_id IS NULL).
CREATE POLICY email_blacklist_select
    ON email_blacklist
    FOR SELECT
    USING (tenant_id IS NULL OR tenant_id = auth_tenant_id());

CREATE POLICY email_blacklist_insert
    ON email_blacklist
    FOR INSERT
    WITH CHECK (tenant_id = auth_tenant_id());

GRANT SELECT, INSERT ON email_blacklist TO authenticated;
GRANT USAGE, SELECT ON SEQUENCE email_blacklist_id_seq TO authenticated;


-- ============================================================
-- 3. domain_blacklist — catch-all / dispose / blocked domains
-- ============================================================
-- Why: a single domain may serve hundreds of leads. Blacklisting
-- per-email leaves us hammering the same MX with bounces. Domain-level
-- blacklist short-circuits at extraction time.

CREATE TABLE IF NOT EXISTS domain_blacklist (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       UUID REFERENCES tenants(id) ON DELETE CASCADE,
    domain          TEXT NOT NULL,
    reason          TEXT NOT NULL CHECK (reason IN (
        'catch_all',
        'disposable',
        'dns_invalid',
        'spamhaus_listed',
        'mxtoolbox_blacklisted',
        'manual_ban'
    )),
    source          TEXT,
    notes           TEXT,
    blacklisted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Auto-expire: if the listing was due to a transient block we
    -- want to retest after a while. NULL = permanent.
    expires_at      TIMESTAMPTZ,
    UNIQUE (tenant_id, domain)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_domain_blacklist_global_unique
    ON domain_blacklist (domain)
    WHERE tenant_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_domain_blacklist_active
    ON domain_blacklist (domain)
    WHERE expires_at IS NULL OR expires_at > now();

ALTER TABLE domain_blacklist ENABLE ROW LEVEL SECURITY;

CREATE POLICY domain_blacklist_select
    ON domain_blacklist
    FOR SELECT
    USING (tenant_id IS NULL OR tenant_id = auth_tenant_id());

CREATE POLICY domain_blacklist_insert
    ON domain_blacklist
    FOR INSERT
    WITH CHECK (tenant_id = auth_tenant_id());

GRANT SELECT, INSERT ON domain_blacklist TO authenticated;
GRANT USAGE, SELECT ON SEQUENCE domain_blacklist_id_seq TO authenticated;


-- ============================================================
-- 4. deliverability_metrics_daily — aggregated daily stats
-- ============================================================
-- Why: per-send rows live in `outreach_sends` (millions over time);
-- the dashboard needs O(1) reads of "yesterday's open rate by domain".
-- A daily rollup keyed by (tenant, domain_id, inbox_id, date) is the
-- right granularity for monitoring + reputation enforcement.
--
-- Populated by a nightly job that aggregates from `outreach_sends`
-- + Resend/Gmail webhook events. Idempotent via the unique key.

CREATE TABLE IF NOT EXISTS deliverability_metrics_daily (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    domain_id       UUID REFERENCES tenant_email_domains(id) ON DELETE CASCADE,
    inbox_id        UUID REFERENCES tenant_inboxes(id) ON DELETE CASCADE,
    metric_date     DATE NOT NULL,
    -- Send funnel
    sent_count      INTEGER NOT NULL DEFAULT 0,
    delivered_count INTEGER NOT NULL DEFAULT 0,
    bounced_hard    INTEGER NOT NULL DEFAULT 0,
    bounced_soft    INTEGER NOT NULL DEFAULT 0,
    -- Engagement funnel
    opened_count    INTEGER NOT NULL DEFAULT 0,
    clicked_count   INTEGER NOT NULL DEFAULT 0,
    replied_count   INTEGER NOT NULL DEFAULT 0,
    -- Negative signals
    complained_count INTEGER NOT NULL DEFAULT 0,
    unsubscribed_count INTEGER NOT NULL DEFAULT 0,
    -- Health flags computed by the worker (NULL until populated)
    bounce_rate     NUMERIC(5, 4),  -- bounced_hard / sent
    complaint_rate  NUMERIC(5, 4),  -- complained / delivered
    open_rate       NUMERIC(5, 4),
    click_rate      NUMERIC(5, 4),
    -- Provenance
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, domain_id, inbox_id, metric_date)
);

CREATE INDEX IF NOT EXISTS idx_deliverability_metrics_tenant_date
    ON deliverability_metrics_daily (tenant_id, metric_date DESC);
CREATE INDEX IF NOT EXISTS idx_deliverability_metrics_domain
    ON deliverability_metrics_daily (domain_id, metric_date DESC)
    WHERE domain_id IS NOT NULL;

ALTER TABLE deliverability_metrics_daily ENABLE ROW LEVEL SECURITY;

CREATE POLICY deliverability_metrics_tenant_isolation
    ON deliverability_metrics_daily
    FOR ALL
    USING (tenant_id = auth_tenant_id());

GRANT SELECT ON deliverability_metrics_daily TO authenticated;


-- ============================================================
-- 5. lead_rejection_log — every offline-filter rejection
-- ============================================================
-- Why: the 9-phase pipeline rejects ~80% of candidates BEFORE any
-- API spend (offline filters: consumi, proprietà, affidabilità,
-- trend, sede operativa, anti-uffici). To tune thresholds we need
-- to see the rejection distribution per rule, per ATECO, per region.
-- Without this audit we'd be flying blind.
--
-- Stays cheap: we log the rejection reason + a tiny snapshot of the
-- candidate, NOT the full enrichment payload.

CREATE TABLE IF NOT EXISTS lead_rejection_log (
    id                 BIGSERIAL PRIMARY KEY,
    tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    -- Candidate identity (lead may not exist yet at offline-filter time):
    company_name       TEXT,
    vat_number         TEXT,
    province           TEXT,
    cap                TEXT,
    ateco_code         TEXT,
    -- Phase + rule
    phase              TEXT NOT NULL,        -- 'phase2_offline', 'phase4_solar', etc.
    rule               TEXT NOT NULL,        -- 'consumi_below_threshold', 'sede_legale_mismatch', ...
    rule_threshold     JSONB NOT NULL DEFAULT '{}'::JSONB,
    candidate_value    JSONB NOT NULL DEFAULT '{}'::JSONB,
    rejected_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_lead_rejection_tenant_phase
    ON lead_rejection_log (tenant_id, phase, rejected_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_rejection_rule
    ON lead_rejection_log (rule, rejected_at DESC);

ALTER TABLE lead_rejection_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY lead_rejection_log_tenant_isolation
    ON lead_rejection_log
    FOR ALL
    USING (tenant_id = auth_tenant_id());

GRANT SELECT, INSERT ON lead_rejection_log TO authenticated;
GRANT USAGE, SELECT ON SEQUENCE lead_rejection_log_id_seq TO authenticated;


-- ============================================================
-- 6. leads.cluster_signature — geographic diversification key
-- ============================================================
-- Why: the new orchestrator picks the next 250 sends/day with a
-- diversification step (don't hit 80017 ten times in a row — looks
-- spammy and wastes the territory). The signature is "{cap}|{ateco_root}"
-- and the orchestrator load-balances across distinct signatures.
--
-- Computed at scan time and updated whenever cap/ateco changes.

ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS cluster_signature TEXT;

CREATE INDEX IF NOT EXISTS idx_leads_cluster_signature
    ON leads (tenant_id, cluster_signature)
    WHERE cluster_signature IS NOT NULL;


-- ============================================================
-- 7. tenants.pipeline_version — feature flag for v2 orchestrator
-- ============================================================
-- 1 = legacy (current production)
-- 2 = 9-phase GDPR pipeline (Phase A foundations land first; the
--     orchestrator only flips a tenant once Tasks 4-7 ship and a
--     manual smoke test passes)

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS pipeline_version SMALLINT NOT NULL DEFAULT 1
    CHECK (pipeline_version IN (1, 2));


-- ============================================================
-- 8. api_usage_log.phase — per-phase cost analytics
-- ============================================================
-- Required by Task 8: tag every external-API call with the pipeline
-- phase that triggered it, so we can answer "how much did Phase 4
-- (Solar API + AI render) cost us this month per tenant?"
--
-- Nullable + indexed; legacy code paths leave it NULL.

ALTER TABLE api_usage_log
    ADD COLUMN IF NOT EXISTS phase TEXT;

CREATE INDEX IF NOT EXISTS idx_api_usage_phase
    ON api_usage_log (tenant_id, phase, occurred_at DESC)
    WHERE phase IS NOT NULL;

COMMIT;
