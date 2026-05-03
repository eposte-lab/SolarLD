-- ============================================================
-- 0103 — known_company_buildings: place_id support (FLUSSO 1 v3)
-- ============================================================
-- Adds Google Places anchoring to the building cache so the v3 funnel
-- can short-circuit Solar API calls keyed on `google_place_id` instead
-- of Atoka VAT.
--
-- Strategy: ADDITIVE. The existing VAT primary key stays in place so
-- the v2 BIC cache continues to work during the v2 → v3 rollout.
-- The wipe migration (0100, ships LAST) will then drop the BIC-only
-- columns. Until then the table tolerates two parallel keying schemes.
--
-- Backward-compat:
--   * v2 (BIC) inserts with vat_number set, google_place_id = NULL
--   * v3 (Solar direct) inserts with google_place_id set, vat_number = NULL
--   * Demolition: vat_number, source_chain, confirmed_by_user_id,
--     polygon_geojson, confidence dropped (Sprint 1.1+1.3).

-- 1) Make vat_number nullable so v3 rows without a VAT can be inserted.
--    The PK is dropped first; we'll re-add a CHECK constraint to ensure
--    at least one anchor column is populated.
ALTER TABLE known_company_buildings
  DROP CONSTRAINT IF EXISTS known_company_buildings_pkey;

ALTER TABLE known_company_buildings
  ALTER COLUMN vat_number DROP NOT NULL;

-- 2) Add the v3 anchor + Solar payload columns.
ALTER TABLE known_company_buildings
  ADD COLUMN IF NOT EXISTS id UUID DEFAULT gen_random_uuid(),
  ADD COLUMN IF NOT EXISTS google_place_id  TEXT,
  ADD COLUMN IF NOT EXISTS solar_building_insights JSONB,
  ADD COLUMN IF NOT EXISTS solar_data_layers       JSONB,
  ADD COLUMN IF NOT EXISTS first_discovered_at     TIMESTAMPTZ DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS last_seen_at            TIMESTAMPTZ DEFAULT NOW();

-- Surrogate UUID PK so both v2 and v3 rows have a stable identity
-- regardless of which anchor (vat or place_id) they use.
UPDATE known_company_buildings SET id = gen_random_uuid() WHERE id IS NULL;

ALTER TABLE known_company_buildings
  ALTER COLUMN id SET NOT NULL;

ALTER TABLE known_company_buildings
  ADD CONSTRAINT known_company_buildings_pkey PRIMARY KEY (id);

-- 3) Unique anchors — sparse so NULL rows don't collide.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_kcb_vat
  ON known_company_buildings(vat_number)
  WHERE vat_number IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uniq_kcb_place_id
  ON known_company_buildings(google_place_id)
  WHERE google_place_id IS NOT NULL;

-- 4) Sanity: at least one anchor must be set on every row.
ALTER TABLE known_company_buildings
  DROP CONSTRAINT IF EXISTS kcb_one_anchor_required;

ALTER TABLE known_company_buildings
  ADD CONSTRAINT kcb_one_anchor_required
    CHECK (vat_number IS NOT NULL OR google_place_id IS NOT NULL);

COMMENT ON COLUMN known_company_buildings.google_place_id IS
  'v3 anchor — Google Places place_id. v3 rows have this set and vat_number NULL. The Solar API result is cached under solar_building_insights so the next scan hitting the same place_id skips the $0.02 Solar call.';

COMMENT ON COLUMN known_company_buildings.solar_building_insights IS
  'Raw Google Solar buildingInsights:findClosest payload. Populated by level4_solar_qualify on cache miss; consulted on next cycle to avoid re-paying Solar API.';

COMMENT ON COLUMN known_company_buildings.solar_data_layers IS
  'Raw Solar dataLayers payload. Populated lazily during L6 asset generation when we generate the panelled rendering.';
