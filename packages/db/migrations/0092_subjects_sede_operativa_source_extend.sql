-- 0092_subjects_sede_operativa_source_extend.sql
--
-- Extend the CHECK constraint on subjects.sede_operativa_source to
-- accept the new provenance values produced by the Building
-- Identification Cascade (BIC):
--
--   * 'user_confirmed' — operator clicked a building on the picker
--     map (POST /v1/demo/confirm-building, or the dialog's confirmed
--     short-circuit on /test-pipeline).
--   * 'vision'         — Claude Vision identified the building by
--     reading the company name on the aerial.
--   * 'osm_snap'       — OSM Overpass building polygon match (either
--     by name fuzzy-match or by snapping to the nearest building
--     after a Solar API 404).
--
-- Without this migration, /test-pipeline submits with a confirmed
-- building from the picker fail with 502 "Errore nel salvataggio
-- dell'anagrafica" because the subjects upsert hits the constraint.

ALTER TABLE subjects
  DROP CONSTRAINT IF EXISTS subjects_sede_operativa_source_check;

ALTER TABLE subjects
  ADD CONSTRAINT subjects_sede_operativa_source_check
  CHECK (
    sede_operativa_source IS NULL
    OR sede_operativa_source = ANY (ARRAY[
      'atoka',
      'website_scrape',
      'google_places',
      'mapbox_hq',
      'manual',
      'user_confirmed',
      'vision',
      'osm_snap'
    ])
  );

COMMENT ON CONSTRAINT subjects_sede_operativa_source_check ON subjects IS
  'Allowed values for sede_operativa_source — the BIC produces user_confirmed / vision / osm_snap on top of the legacy 4-tier enum.';
