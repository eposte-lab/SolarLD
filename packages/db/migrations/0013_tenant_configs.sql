-- ============================================================
-- 0013 — tenant_configs (Sprint 9)
-- ============================================================
-- Per-tenant operational config for Hunter scan mode, technical
-- filters, scoring thresholds, and monthly budgets.
--
-- Replaces the ad-hoc `tenants.settings` JSONB bucket for
-- scan-related knobs. Created once per tenant during the onboarding
-- wizard; editable later from /settings.
--
-- Three `scan_mode` values drive HunterAgent dispatch:
--   b2b_precision   — Google Places discovery PRE, Solar API POST.
--                     Low-cost, high-precision for B2B-only installers.
--   opportunistic   — geographic grid sampling (current behavior).
--                     Mixed B2B/B2C coverage, pre-filter by tech only.
--   volume          — grid sampling + very permissive filters.
--                     Maximizes outreach volume; lower quality leads.

CREATE TABLE IF NOT EXISTS tenant_configs (
  id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id               UUID NOT NULL UNIQUE
                          REFERENCES tenants(id) ON DELETE CASCADE,

  -- -----------------------------------------------------------
  -- SCAN MODE — dispatcher key for HunterAgent
  -- -----------------------------------------------------------
  scan_mode               TEXT NOT NULL DEFAULT 'b2b_precision'
    CHECK (scan_mode IN ('b2b_precision', 'opportunistic', 'volume')),

  -- -----------------------------------------------------------
  -- TARGET SEGMENTS — what the installer sells
  -- -----------------------------------------------------------
  target_segments         TEXT[] NOT NULL DEFAULT ARRAY['b2b'],

  -- -----------------------------------------------------------
  -- GOOGLE PLACES discovery (Tier 0) — used by b2b_precision
  -- -----------------------------------------------------------
  -- Google Places `types` to query via Nearby Search. Seeded by the
  -- wizard from the ateco_google_types mapping table.
  place_type_whitelist    TEXT[] NOT NULL DEFAULT ARRAY['establishment'],

  -- Priority map: Google type → score (higher first during ranking).
  -- Example: {"supermarket": 10, "warehouse": 8, "car_repair": 6}
  place_type_priority     JSONB NOT NULL DEFAULT '{}'::jsonb,

  -- -----------------------------------------------------------
  -- ATECO FILTERING (Tier 2 — used during Atoka enrichment)
  -- -----------------------------------------------------------
  -- Kept as metadata for when the installer runs enrichment on a
  -- converted lead. Also used by the wizard to derive
  -- place_type_whitelist via the mapping table.
  ateco_whitelist         TEXT[],
  ateco_blacklist         TEXT[],
  ateco_priority          JSONB,

  -- -----------------------------------------------------------
  -- SIZE FILTERS (B2B) — only meaningful post-Atoka enrichment
  -- -----------------------------------------------------------
  min_employees           INT,
  max_employees           INT,
  min_revenue_eur         BIGINT,
  max_revenue_eur         BIGINT,

  -- -----------------------------------------------------------
  -- TECHNICAL FILTERS — per-segment knobs applied post-Solar-scan
  -- -----------------------------------------------------------
  technical_filters       JSONB NOT NULL DEFAULT '{
    "b2b": {
      "min_area_sqm": 500,
      "min_kwp": 50,
      "max_shading": 0.4,
      "min_exposure_score": 0.7
    },
    "b2c": {
      "min_area_sqm": 60,
      "min_kwp": 3,
      "max_shading": 0.5,
      "min_exposure_score": 0.6
    }
  }'::jsonb,

  -- -----------------------------------------------------------
  -- SCORING
  -- -----------------------------------------------------------
  scoring_threshold       INT NOT NULL DEFAULT 60
    CHECK (scoring_threshold BETWEEN 0 AND 100),

  scoring_weights         JSONB NOT NULL DEFAULT '{
    "b2b": {"kwp":25,"consumption":25,"solvency":20,"incentives":15,"distance":15},
    "b2c": {"kwp":20,"consumption":25,"solvency":15,"incentives":20,"distance":20}
  }'::jsonb,

  -- -----------------------------------------------------------
  -- BUDGET CAPS (euros, evaluated monthly by cost-aware workers)
  -- -----------------------------------------------------------
  monthly_scan_budget_eur     NUMERIC(10, 2) NOT NULL DEFAULT 1500,
  monthly_outreach_budget_eur NUMERIC(10, 2) NOT NULL DEFAULT 2000,

  -- -----------------------------------------------------------
  -- SCAN STRATEGY (geographic priority + density)
  -- -----------------------------------------------------------
  scan_priority_zones     TEXT[] NOT NULL DEFAULT ARRAY['capoluoghi'],
    -- values: 'capoluoghi' | 'comuni_>20k' | 'comuni_>10k' | 'comuni_>5k' | 'all'
  scan_grid_density_m     INT NOT NULL DEFAULT 30
    CHECK (scan_grid_density_m BETWEEN 10 AND 500),

  -- -----------------------------------------------------------
  -- ENRICHMENT (Tier 2 — Atoka)
  -- -----------------------------------------------------------
  -- Manual-only in Sprint 9. Set when the installer clicks
  -- "Arricchisci per contratto" on a converted lead.
  atoka_enabled           BOOLEAN NOT NULL DEFAULT false,
  atoka_monthly_cap_eur   NUMERIC(10, 2) NOT NULL DEFAULT 0,

  -- -----------------------------------------------------------
  -- WIZARD STATE
  -- -----------------------------------------------------------
  -- NULL until the installer completes the 5-step onboarding.
  -- The dashboard redirects to /onboarding when NULL.
  wizard_completed_at     TIMESTAMPTZ,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- Segment sanity check
  CONSTRAINT tenant_configs_segments_valid CHECK (
    target_segments <@ ARRAY['b2b', 'b2c']::TEXT[] AND
    array_length(target_segments, 1) >= 1
  )
);

CREATE INDEX idx_tenant_configs_scan_mode ON tenant_configs(scan_mode);
CREATE INDEX idx_tenant_configs_wizard_pending
  ON tenant_configs(tenant_id)
  WHERE wizard_completed_at IS NULL;

CREATE TRIGGER trg_tenant_configs_updated_at
  BEFORE UPDATE ON tenant_configs
  FOR EACH ROW
  EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- RLS — tenants can read/update their own config
-- ============================================================
ALTER TABLE tenant_configs ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_configs_all ON tenant_configs
  FOR ALL
  USING (tenant_id = auth_tenant_id())
  WITH CHECK (tenant_id = auth_tenant_id());

-- ============================================================
-- BACKFILL — preserve behavior for existing tenants
-- ============================================================
-- Existing tenants are currently running the grid-sampling Hunter
-- (equivalent to 'opportunistic' mode). Insert a default row with
-- wizard_completed_at = now() so they don't get redirected to the
-- new /onboarding wizard. Fresh tenants get NULL and the dashboard
-- redirects them.
INSERT INTO tenant_configs (tenant_id, scan_mode, wizard_completed_at)
SELECT t.id, 'opportunistic', now()
FROM tenants t
LEFT JOIN tenant_configs tc ON tc.tenant_id = t.id
WHERE tc.id IS NULL;
