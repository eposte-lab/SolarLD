-- ============================================================
-- 0063 — prospect_lists + prospect_list_items
-- ============================================================
-- Standalone "Trova aziende" prospector — lets the operator
-- search Atoka directly (ATECO + geo + revenue + employees +
-- keyword), save the result set as a named list, export CSV,
-- and optionally promote selected companies into the regular
-- subjects → scan_candidates → leads pipeline.
--
-- Why a *separate* table rather than reusing scan_candidates:
--   • scan_candidates is owned by HunterAgent's L1-L4 funnel and
--     has a strict (tenant_id, scan_id, vat_number) uniqueness
--     guarded by RLS. A prospector list is a user-curated view
--     and shouldn't pollute funnel telemetry.
--   • Lists are durable artefacts: an operator builds "Studi
--     amministratori NA" once, comes back next month, exports
--     CSV again. scan_candidates rows are scoped to a single
--     funnel run and pruned by retention policy.
--   • An item can live in multiple lists (cross-list reuse) —
--     would break scan_candidates' UNIQUE constraint.
--
-- The Atoka payload is captured verbatim so the operator can
-- export, re-import, or migrate to subjects later without a
-- re-fetch.

BEGIN;

-- ------------------------------------------------------------
-- prospect_lists — saved searches owned by a tenant user
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS prospect_lists (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    description     TEXT,
    -- The exact search criteria used to build the list. Stored
    -- so the operator can "rebuild" / refresh later, and so we
    -- can show provenance in the UI.
    search_filter   JSONB NOT NULL DEFAULT '{}'::JSONB,
    -- Optional preset code so we can show a chip ("Amministratori
    -- condominio", "Capannoni industriali", …). NULL = ad-hoc.
    preset_code     TEXT,
    -- Cached counts — refreshed by the API on item insertion /
    -- deletion. Avoids a count(*) on every list-page render.
    item_count      INTEGER NOT NULL DEFAULT 0,
    -- Promotion telemetry: how many of these prospects have
    -- already been pushed into subjects/scan_candidates.
    imported_count  INTEGER NOT NULL DEFAULT 0,
    -- Optional link to the campaign launched on this list (set
    -- when the operator clicks "Lancia campagna").
    launched_campaign_id UUID REFERENCES acquisition_campaigns(id) ON DELETE SET NULL,
    launched_at     TIMESTAMPTZ,
    created_by      UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_prospect_lists_tenant_recent
    ON prospect_lists (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_prospect_lists_preset
    ON prospect_lists (tenant_id, preset_code)
    WHERE preset_code IS NOT NULL;

-- ------------------------------------------------------------
-- prospect_list_items — companies inside a list
-- ------------------------------------------------------------
-- One row per (list, company). vat_number is the natural key
-- because Atoka returns one record per P.IVA. We snapshot the
-- full Atoka profile so the list is durable independently of
-- Atoka's catalog churn.
CREATE TABLE IF NOT EXISTS prospect_list_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    list_id         UUID NOT NULL REFERENCES prospect_lists(id) ON DELETE CASCADE,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    -- Identity ----------------------------------------------------------------
    vat_number      TEXT NOT NULL,
    legal_name      TEXT NOT NULL,
    -- Firmographics — denormalised from atoka_payload for fast
    -- table render + sortable columns.
    ateco_code      TEXT,
    ateco_description TEXT,
    employees       INTEGER,
    revenue_eur     BIGINT,
    -- Address -----------------------------------------------------------------
    hq_address      TEXT,
    hq_cap          TEXT,
    hq_city         TEXT,
    hq_province     TEXT,
    hq_lat          NUMERIC(9, 6),
    hq_lng          NUMERIC(9, 6),
    -- Contact (best-effort, often null on discovery search) ------------------
    website_domain  TEXT,
    decision_maker_name  TEXT,
    decision_maker_role  TEXT,
    decision_maker_email TEXT,
    linkedin_url    TEXT,
    -- Raw payload — full Atoka profile dict, for forensic
    -- debugging and re-export.
    atoka_payload   JSONB NOT NULL DEFAULT '{}'::JSONB,
    -- Promotion bookkeeping ---------------------------------------------------
    imported_subject_id UUID REFERENCES subjects(id) ON DELETE SET NULL,
    imported_at      TIMESTAMPTZ,
    -- Audit -------------------------------------------------------------------
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- A company can appear in many lists, but only once per list.
    UNIQUE (list_id, vat_number)
);

CREATE INDEX IF NOT EXISTS idx_prospect_list_items_list
    ON prospect_list_items (list_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_prospect_list_items_tenant_vat
    ON prospect_list_items (tenant_id, vat_number);

CREATE INDEX IF NOT EXISTS idx_prospect_list_items_imported
    ON prospect_list_items (list_id, imported_at)
    WHERE imported_at IS NOT NULL;

-- ------------------------------------------------------------
-- updated_at trigger on prospect_lists
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION prospect_lists_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS prospect_lists_updated_at ON prospect_lists;
CREATE TRIGGER prospect_lists_updated_at
    BEFORE UPDATE ON prospect_lists
    FOR EACH ROW EXECUTE FUNCTION prospect_lists_set_updated_at();

-- ------------------------------------------------------------
-- RLS — tenant isolation
-- ------------------------------------------------------------
-- Both tables are tenant-scoped. The API uses the service-role
-- client and filters explicitly by tenant_id, but RLS is the
-- defence in depth against anon key misuse from the dashboard.

ALTER TABLE prospect_lists ENABLE ROW LEVEL SECURITY;
ALTER TABLE prospect_list_items ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS prospect_lists_tenant_iso ON prospect_lists;
CREATE POLICY prospect_lists_tenant_iso ON prospect_lists
    FOR ALL
    TO authenticated
    USING (tenant_id = auth_tenant_id())
    WITH CHECK (tenant_id = auth_tenant_id());

DROP POLICY IF EXISTS prospect_list_items_tenant_iso ON prospect_list_items;
CREATE POLICY prospect_list_items_tenant_iso ON prospect_list_items
    FOR ALL
    TO authenticated
    USING (tenant_id = auth_tenant_id())
    WITH CHECK (tenant_id = auth_tenant_id());

-- Service role bypasses RLS (already standard config).

COMMIT;
