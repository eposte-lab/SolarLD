-- 0091_known_company_buildings.sql
--
-- Cache table for the Building Identification Cascade (BIC).
--
-- Once we've resolved which exact building belongs to a company —
-- whether through the multi-signal cascade (Atoka civic + Google
-- Places multi-query + OSM name match + Gemini Vision on the aerial)
-- or a user click on the picker map — we persist the result keyed
-- by VAT number so subsequent runs short-circuit Stages 1-6 and
-- start from the confirmed building.
--
-- Why VAT-keyed (not tenant-scoped):
--   * VAT numbers are nationally unique to a legal entity, so the
--     building doesn't change across tenants. If Tenant A confirmed
--     MULTILOG's capannone, Tenant B should benefit from that signal
--     too — they're targeting the same physical roof.
--   * tenant_id is recorded for auditability ("which tenant did the
--     resolution work?") but it's NOT part of the lookup key.
--
-- The source_chain JSONB stores the per-stage candidates that voted
-- for the winning cluster, so we can debug "why did we pick this
-- building?" without re-running the cascade. Shape:
--   [
--     {"stage": "atoka", "weight": 0.4, "lat": ..., "lng": ...},
--     {"stage": "places_q1_first_token", "weight": 0.25, "place_id": "ChIJ..."},
--     {"stage": "vision", "weight": 0.85, "reasoning": "Building 3 has 'MULTILOG' painted on the loading-bay roof"},
--   ]

CREATE TABLE IF NOT EXISTS known_company_buildings (
  vat_number TEXT PRIMARY KEY,
  tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL,
  lat DOUBLE PRECISION NOT NULL,
  lng DOUBLE PRECISION NOT NULL,
  -- GeoJSON Polygon with the building footprint when known (from OSM
  -- or Google Solar). Null when the cascade only converged on a point
  -- (e.g. user clicked a freehand pin on the map).
  polygon_geojson JSONB,
  -- One of: 'high' | 'medium' | 'low' | 'user_confirmed'.
  -- 'user_confirmed' supersedes everything — never overwrite by an
  -- automated resolution that would downgrade the entry.
  confidence TEXT NOT NULL CHECK (
    confidence IN ('high', 'medium', 'low', 'user_confirmed')
  ),
  source_chain JSONB NOT NULL DEFAULT '[]'::jsonb,
  resolved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- When a user clicked the picker, both fields are set; otherwise null.
  confirmed_by_user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
  confirmed_at TIMESTAMPTZ,
  -- Cost telemetry — sum of API costs (Atoka + Places + Solar +
  -- Vision) spent to resolve this entry. Lets us see at a glance
  -- whether re-resolving a stale entry would be cheap or expensive.
  cost_cents INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS known_company_buildings_tenant_idx
  ON known_company_buildings (tenant_id, resolved_at DESC);

CREATE INDEX IF NOT EXISTS known_company_buildings_confidence_idx
  ON known_company_buildings (confidence, resolved_at DESC);

COMMENT ON TABLE known_company_buildings IS
  'BIC cache: VAT-keyed building identification. A hit short-circuits the cascade in operating_site_resolver / building_identification.identify_building.';

COMMENT ON COLUMN known_company_buildings.confidence IS
  'high|medium|low from automated cascade voting; user_confirmed when the operator clicked a building on the picker map. user_confirmed entries are never auto-overwritten.';

COMMENT ON COLUMN known_company_buildings.source_chain IS
  'Array of {stage, weight, ...stage-specific-fields} objects from the winning vote cluster. Used for "why did we pick this building?" debugging without re-running the cascade.';
