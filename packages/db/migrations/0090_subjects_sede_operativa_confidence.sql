-- 0090_subjects_sede_operativa_confidence.sql
--
-- Demo polish (Sprint 2): persist the confidence bucket the
-- operating-site cascade has been returning all along but never
-- writing to the database. The CreativeAgent's hard confidence gate
-- (Sprint 2.1) and the /admin/demo-runs roof badge both read this
-- column — without it, low-confidence "centroide HQ" runs slip past
-- the gate and the dashboard cannot show provenance for any run.
--
-- Backfill maps the existing source values via the same
-- source→confidence mapping the resolver already applies in code so
-- historical demo runs render correctly in the new dashboard column.

ALTER TABLE subjects
  ADD COLUMN IF NOT EXISTS sede_operativa_confidence TEXT;

UPDATE subjects
SET sede_operativa_confidence = CASE sede_operativa_source
  WHEN 'atoka'           THEN 'high'
  WHEN 'website_scrape'  THEN 'medium'
  WHEN 'google_places'   THEN 'medium'
  WHEN 'osm_snap'        THEN 'medium'
  WHEN 'mapbox_hq'       THEN 'low'
  WHEN 'unresolved'      THEN 'none'
  ELSE NULL
END
WHERE sede_operativa_source IS NOT NULL
  AND sede_operativa_confidence IS NULL;

COMMENT ON COLUMN subjects.sede_operativa_confidence IS
  'Confidence bucket for the resolved operating site: high (Atoka match), medium (website scrape / Google Places / OSM snap), low (Mapbox HQ centroid — review before render), none (unresolved). Populated by operating_site_resolver alongside sede_operativa_source.';
