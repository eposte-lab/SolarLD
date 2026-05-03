-- ============================================================
-- 0097 — ateco_google_types: sector-aware extension
-- ============================================================
-- Adds the metadata the sector-aware hunter funnel needs to:
--   * filter site signal scanning (L2) by per-sector keywords,
--   * tag scan_candidates with a predicted_sector (L1 → L3),
--   * weight BIC voting (stage 4) with osm_landuse_hints,
--   * surface "Settori target" multi-select in onboarding.
--
-- All columns are nullable / default-empty so the migration is
-- backward-compatible: tenants that haven't opted into sector-
-- aware mode keep the legacy behaviour driven by ateco_codes
-- alone.
--
-- Companion seed in 0098_ateco_seed_sector_keywords.sql adds the
-- new wizard_groups (industry_heavy, food_production, hospitality_large,
-- hospitality_food_service, agricultural_intensive) and populates
-- the new columns for the existing rows.
--
-- See plan: shimmying-painting-backus.md, Sprint A.

ALTER TABLE ateco_google_types
  ADD COLUMN IF NOT EXISTS osm_landuse_hints       JSONB    NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS osm_additional_tags     JSONB    NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS places_keywords         TEXT[]   NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS places_excluded_types   TEXT[]   NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS site_signal_keywords    TEXT[]   NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS min_zone_area_m2        INTEGER,
  ADD COLUMN IF NOT EXISTS search_radius_m         INTEGER  NOT NULL DEFAULT 1500,
  ADD COLUMN IF NOT EXISTS typical_kwp_range_min   INTEGER,
  ADD COLUMN IF NOT EXISTS typical_kwp_range_max   INTEGER;

COMMENT ON COLUMN ateco_google_types.osm_landuse_hints IS
  'Lista [{landuse: "industrial", weight: 1.0}, ...] consultata come signal in L2 enrichment e BIC voting (stage 4). NON guida una query Overpass — il discovery resta Atoka-driven. Esempio: per industry_heavy il building dentro un poligono landuse=industrial riceve +0.15 al voting score.';

COMMENT ON COLUMN ateco_google_types.osm_additional_tags IS
  'Tag OSM aggiuntivi che identificano una struttura tipica del settore (es. man_made=works per industry_heavy, tourism=hotel per hospitality_large). Stesso meccanismo di voting di osm_landuse_hints.';

COMMENT ON COLUMN ateco_google_types.places_keywords IS
  'Keyword italiane usate quando il funnel chiama Google Places Text Search per qualificare un candidato di questo wizard_group. Es. "stabilimento metalmeccanico" per industry_heavy.';

COMMENT ON COLUMN ateco_google_types.places_excluded_types IS
  'Tipi Google Places da escludere quando si cerca questo settore (es. car_repair per industry_heavy, perché Places lo confonde con officine industriali).';

COMMENT ON COLUMN ateco_google_types.site_signal_keywords IS
  'Token che L2 cerca nellHTML del sito (title, meta description, body slice). Per industry_heavy: capannone, stabilimento. Per hospitality_large: hotel, resort, congresso. Sostituisce la lista hardcoded in level2_enrichment.py.';

COMMENT ON COLUMN ateco_google_types.min_zone_area_m2 IS
  'Soglia minima di area utile (m²) sotto la quale il candidato viene penalizzato in L3. Per industry_heavy: 5000. Per hospitality_food_service: 500.';

COMMENT ON COLUMN ateco_google_types.search_radius_m IS
  'Raggio per Places Nearby quando si scansiona attorno a un seme (zona industriale, hotel cluster, ecc.). Default 1500m. Per logistics 2500m, hospitality_food_service 500m.';

COMMENT ON COLUMN ateco_google_types.typical_kwp_range_min IS
  'kWp tipico minimo per il settore — usato in L3 solar_potential_score per penalizzare tetti troppo piccoli rispetto al fabbisogno settoriale.';

COMMENT ON COLUMN ateco_google_types.typical_kwp_range_max IS
  'kWp tipico massimo per il settore — usato in L3 solar_potential_score per identificare tetti enormi (potenziale very_promising).';
