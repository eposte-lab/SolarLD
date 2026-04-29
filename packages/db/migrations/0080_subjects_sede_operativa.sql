-- Operating site (sede operativa) coordinates on the subject row.
--
-- Until now we've stored only a single address bag on `subjects` (the
-- legal HQ), and let the rooftop pipeline forward-geocode that into a
-- single (lat, lng) pair on `roofs`. That's a problem in practice:
-- Italian P.IVA records routinely list a notary's office, an
-- accountant, or the centroid of an industrial cluster as the legal
-- HQ — none of which is the building we actually want to paint solar
-- panels onto. The "sede operativa" (operating site) is a separate
-- entry on Atoka's `locations[]` array, and is the one we want for
-- rendering.
--
-- This migration adds a parallel address bag for the operating site,
-- plus a `sede_operativa_source` provenance column so the dashboard
-- can show a badge ("Sede operativa: Atoka / Sito web / Google Places
-- / Centroide HQ") and ops can audit which leads need a manual
-- address upgrade.
--
-- Resolver cascade (see operating_site_resolver.py, Sprint Demo
-- Polish Phase B.4):
--   1. atoka         — Atoka locations[] entry typed as
--                      operating/secondary/production (highest)
--   2. website_scrape — schema.org/<address>/regex match on the
--                      company website
--   3. google_places  — Places API text search for the legal name
--   4. mapbox_hq      — fallback to forward-geocoding the legal HQ
--                      (status quo; flagged as low confidence)
--
-- All four feed the same set of columns so downstream code never has
-- to branch on the source — only the badge does.

ALTER TABLE subjects
  ADD COLUMN IF NOT EXISTS sede_operativa_address text,
  ADD COLUMN IF NOT EXISTS sede_operativa_cap text,
  ADD COLUMN IF NOT EXISTS sede_operativa_city text,
  ADD COLUMN IF NOT EXISTS sede_operativa_province text,
  ADD COLUMN IF NOT EXISTS sede_operativa_lat double precision,
  ADD COLUMN IF NOT EXISTS sede_operativa_lng double precision,
  ADD COLUMN IF NOT EXISTS sede_operativa_source text
    CHECK (sede_operativa_source IS NULL OR sede_operativa_source IN (
      'atoka', 'website_scrape', 'google_places', 'mapbox_hq', 'manual'
    ));

-- Quick lookup for the audit dashboard ("how many leads still rely on
-- the low-confidence HQ centroid?"). Partial index keeps it small —
-- production volume is dominated by atoka/website_scrape.
CREATE INDEX IF NOT EXISTS subjects_sede_operativa_source_idx
  ON subjects (sede_operativa_source)
  WHERE sede_operativa_source IS NOT NULL;

COMMENT ON COLUMN subjects.sede_operativa_source IS
  'Provenance of sede_operativa_* coords. NULL = not yet resolved.';
