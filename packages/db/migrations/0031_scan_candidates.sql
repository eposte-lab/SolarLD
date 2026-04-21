-- 0031 — Funnel v2 candidate staging tables.
--
-- The 4-level B2B funnel writes intermediate results at each stage so that:
--   * the dashboard can show a live "waterfall" (L1=5000 → L4=95 → leads=30)
--   * scans are resumable if a worker dies mid-funnel
--   * cost auditing can reconstruct which level spent what
--
-- Flow:
--   L1: insert raw Atoka anagrafica (no Solar, cheap)
--   L2: update `enrichment` JSONB with Places + website signals
--   L3: update `score` + `score_reasons` from Claude Haiku ranker
--   L4: only rows with score ≥ P80 are promoted to `leads` after Solar check
--
-- We use ONE table (`scan_candidates`) with nullable columns rather than
-- three separate tables — candidates are logically the same entity at every
-- stage, and JOINs between L1/L2/L3 would be painful. `stage` tracks the
-- highest level each row has reached.
--
-- Idempotency: UNIQUE(tenant_id, scan_id, vat_number). Re-running an L1 pass
-- on the same scan_id upserts by VAT.
--
-- Retention: scan_candidates are "hot" working set. A scheduled cron (TBD)
-- will move scan_candidates older than 90 days to cold storage — the rows
-- are cheap to keep but bloat the table over time.

BEGIN;

CREATE TABLE IF NOT EXISTS scan_candidates (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    scan_id         uuid NOT NULL,  -- groups candidates from one scan run
    territory_id    uuid REFERENCES territories(id) ON DELETE SET NULL,

    -- ---- L1: Atoka discovery ----
    vat_number      text NOT NULL,
    business_name   text,
    ateco_code      text,
    employees       int,
    revenue_eur     bigint,
    hq_address      text,
    hq_cap          text,
    hq_city         text,
    hq_province     text,
    hq_lat          double precision,
    hq_lng          double precision,
    atoka_payload   jsonb NOT NULL DEFAULT '{}'::jsonb,

    -- ---- L2: Enrichment (nullable until L2 runs) ----
    enrichment      jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Shape: { "phone", "website", "rating", "photos_count",
    --          "place_types", "site_signals": ["capannone","stabilimento"] }

    -- ---- L3: AI proxy score (nullable until L3 runs) ----
    score           smallint CHECK (score IS NULL OR (score BETWEEN 0 AND 100)),
    score_reasons   text[],
    score_flags     text[],

    -- ---- L4: Solar gate outcome (nullable until L4 runs) ----
    roof_id         uuid REFERENCES roofs(id) ON DELETE SET NULL,
    solar_verdict   text CHECK (solar_verdict IS NULL OR solar_verdict IN (
        'accepted','rejected_tech','no_building','api_error','skipped_below_gate'
    )),

    stage           smallint NOT NULL DEFAULT 1 CHECK (stage BETWEEN 1 AND 4),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),

    UNIQUE (tenant_id, scan_id, vat_number)
);

CREATE INDEX IF NOT EXISTS idx_scan_candidates_tenant_scan
    ON scan_candidates (tenant_id, scan_id);

-- Top-N by score retrieval for L4 gating.
CREATE INDEX IF NOT EXISTS idx_scan_candidates_score
    ON scan_candidates (tenant_id, scan_id, score DESC NULLS LAST)
    WHERE score IS NOT NULL;

-- Dashboard waterfall needs per-stage counts quickly.
CREATE INDEX IF NOT EXISTS idx_scan_candidates_stage
    ON scan_candidates (tenant_id, scan_id, stage);

-- ---------------------------------------------------------------------------
-- Per-scan cost telemetry (aggregates, not per-candidate, to keep rows small)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS scan_cost_log (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    scan_id           uuid NOT NULL,
    territory_id      uuid REFERENCES territories(id) ON DELETE SET NULL,
    scan_mode         text NOT NULL,

    atoka_cost_cents  int NOT NULL DEFAULT 0,
    places_cost_cents int NOT NULL DEFAULT 0,
    claude_cost_cents int NOT NULL DEFAULT 0,
    solar_cost_cents  int NOT NULL DEFAULT 0,
    mapbox_cost_cents int NOT NULL DEFAULT 0,
    total_cost_cents  int NOT NULL DEFAULT 0,

    candidates_l1     int NOT NULL DEFAULT 0,
    candidates_l2     int NOT NULL DEFAULT 0,
    candidates_l3     int NOT NULL DEFAULT 0,
    candidates_l4     int NOT NULL DEFAULT 0,
    leads_qualified   int NOT NULL DEFAULT 0,

    started_at        timestamptz NOT NULL DEFAULT now(),
    completed_at      timestamptz,

    UNIQUE (tenant_id, scan_id)
);

CREATE INDEX IF NOT EXISTS idx_scan_cost_log_tenant
    ON scan_cost_log (tenant_id, started_at DESC);

COMMIT;
