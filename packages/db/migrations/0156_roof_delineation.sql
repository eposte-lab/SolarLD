-- 0156_roof_delineation.sql
-- Operator roof-delineation override (Feature 2).
--
-- For warm/hot leads the operator can trace the REAL usable roof area on the
-- aerial; the system then keeps only the Google Solar panels inside that
-- polygon and recomputes the sizing/ROI from that subset. This column stores
-- that manual override so it (a) flows to email/dossier numbers and (b) is NOT
-- overwritten by the automatic realistic-sizing trim on a re-scan.
--
-- Shape (jsonb):
--   {
--     "polygon_geojson": { "type": "Polygon", "coordinates": [[[lng,lat], ...]] },
--     "kept_panel_count": 142,
--     "area_sqm": 1180.5,
--     "kwp": 58.2,
--     "by_user_id": "<uuid>",
--     "at": "2026-06-17T20:00:00Z"
--   }
-- NULL = no manual override (automatic sizing applies). Additive + nullable, so
-- this is a zero-downtime migration.
BEGIN;

ALTER TABLE roofs ADD COLUMN IF NOT EXISTS delineation jsonb;

COMMENT ON COLUMN roofs.delineation IS
  'Operator manual roof-delineation override (polygon + recomputed sizing). '
  'NULL = automatic realistic-sizing applies. See Feature 2 / routes/leads roof-delineation.';

COMMIT;
