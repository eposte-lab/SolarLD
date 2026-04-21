-- 0033 — Italian CAP income/demographics reference table + B2C audiences.
--
-- Drives `scan_mode='b2c_residential'`: instead of scanning roofs
-- one-by-one (expensive and premature — we have no evidence the owner
-- cares about solar), we materialise *audience segments* — groups of
-- CAPs that match the tenant's income + household-type ICP — and
-- target each segment through letter / Meta / door-to-door.
--
-- Data sources:
--   geo_income_stats — one row per CAP. Loaded from ISTAT "redditi
--     e ricchezza" + MEF "dichiarazioni dei redditi per comune" by
--     the `scripts/load_istat_income.py` one-shot. Reload when ISTAT
--     publishes new aggregates (roughly yearly). Global, not
--     tenant-scoped — no RLS.
--   b2c_audiences — one row per (tenant, scan, CAP). Produced by
--     `hunter_b2c._run_b2c_residential`. Tenant-scoped.
--
-- Residents don't have lead rows here: the funnel is inverted. Solar
-- qualification only fires *after* engagement (reply to letter, form
-- submit from Meta ad, agent visit confirmation) — Phase 3.6 wires
-- that path via `b2c_post_engagement_qualify` task.

BEGIN;

CREATE TABLE IF NOT EXISTS geo_income_stats (
    cap                       text PRIMARY KEY,
    provincia                 text NOT NULL,
    regione                   text NOT NULL,
    comune                    text,
    -- Average declared income in EUR (ISTAT `reddito medio dichiarato`).
    -- Integer — sub-euro precision is meaningless at CAP aggregates.
    reddito_medio_eur         int,
    -- Resident population at the CAP (from ISTAT postal stats).
    popolazione               int,
    -- Share of dwellings that are detached/semi-detached single-family
    -- homes. Key signal: solar sells best to people who own their roof,
    -- not to condominium apartments.
    case_unifamiliari_pct     smallint CHECK (
        case_unifamiliari_pct IS NULL
        OR case_unifamiliari_pct BETWEEN 0 AND 100
    ),
    -- Free-form bucket for source metadata (year, ISTAT table id,
    -- smoothing flags). We prefer a JSONB blob over separate columns
    -- because the ISTAT schema shifts yearly and pinning columns to
    -- the current vintage would mean a migration every reload.
    source_metadata           jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at                timestamptz NOT NULL DEFAULT now()
);

-- Hunters filter by reddito_min / case_unifamiliari_pct_min, usually
-- combined with a province/region clause. These two indexes cover
-- both common access patterns.
CREATE INDEX IF NOT EXISTS idx_geo_income_reddito
    ON geo_income_stats (provincia, reddito_medio_eur DESC)
    WHERE reddito_medio_eur IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_geo_income_unifamiliari
    ON geo_income_stats (regione, case_unifamiliari_pct DESC)
    WHERE case_unifamiliari_pct IS NOT NULL;


-- ---------------------------------------------------------------------------
-- b2c_audiences — materialised segments addressable by outreach channel
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS b2c_audiences (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    scan_id           uuid NOT NULL,  -- groups audiences created in one run
    territory_id      uuid REFERENCES territories(id) ON DELETE SET NULL,

    cap               text NOT NULL REFERENCES geo_income_stats(cap),
    provincia         text NOT NULL,
    regione           text NOT NULL,

    -- Snapshot of the filter values at creation time — makes historical
    -- auditing possible even if ISTAT refreshes the underlying row.
    reddito_bucket    text NOT NULL CHECK (reddito_bucket IN (
        'basso','medio','alto','premium'
    )),
    stima_contatti    int NOT NULL DEFAULT 0,
    -- What outreach channels can currently target this audience. Re-
    -- computed on each audience refresh because the tenant's outreach
    -- module may have toggled channels on/off between runs.
    canali_attivi     jsonb NOT NULL DEFAULT '[]'::jsonb,

    -- Per-audience engagement rollup, written by Phase 3 tracking.
    -- Letter campaigns land 'letter_sent' → reply comes via Pixart
    -- scan webhooks (out of scope today) or manual mark-as-engaged.
    letters_sent      int NOT NULL DEFAULT 0,
    meta_leads        int NOT NULL DEFAULT 0,
    replies           int NOT NULL DEFAULT 0,
    qualified_roofs   int NOT NULL DEFAULT 0,  -- post-engagement Solar

    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),

    UNIQUE (tenant_id, scan_id, cap)
);

CREATE INDEX IF NOT EXISTS idx_b2c_audiences_tenant
    ON b2c_audiences (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_b2c_audiences_scan
    ON b2c_audiences (tenant_id, scan_id);

-- RLS: tenants see only their own audiences.
ALTER TABLE b2c_audiences ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS b2c_audiences_own ON b2c_audiences;
CREATE POLICY b2c_audiences_own ON b2c_audiences
    FOR ALL
    USING (tenant_id = auth.uid() OR auth.role() = 'service_role')
    WITH CHECK (tenant_id = auth.uid() OR auth.role() = 'service_role');

COMMIT;
