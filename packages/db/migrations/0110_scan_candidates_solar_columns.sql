-- 0110 — scan_candidates: add per-candidate Solar metric columns (v3 funnel).
--
-- The v3 funnel computes Solar API metrics (area, kWp, sunshine hours, panel
-- count) for every candidate that reaches L4 — both accepted and rejected.
-- Previously these were only kept in the in-memory `SolarQualified` dataclass
-- and never persisted on rejected candidates; accepted candidates had the data
-- only on the linked `roofs` row.
--
-- Adding them directly to `scan_candidates` enables:
--   1. /contatti KPI: "kWp installabili totali" over accepted rows (query
--      avoids a join to roofs which can be expensive at scale).
--   2. Demo re-qualification: operator can trigger a re-evaluate of rejected
--      rows with relaxed demo thresholds without paying the Solar API again
--      (the values are already stored here).
--   3. Future "why was this rejected?" UI: show the actual metrics that failed.

BEGIN;

ALTER TABLE scan_candidates
  ADD COLUMN IF NOT EXISTS solar_kw_installable  NUMERIC(8, 2),
  ADD COLUMN IF NOT EXISTS solar_area_m2          NUMERIC(10, 2),
  ADD COLUMN IF NOT EXISTS solar_sunshine_hours   NUMERIC(8, 1),
  ADD COLUMN IF NOT EXISTS solar_panels_count     SMALLINT;

-- Partial index: fast sum of kWp over accepted rows (used by /contatti KPI).
CREATE INDEX IF NOT EXISTS idx_sc_solar_accepted_kwp
  ON scan_candidates (tenant_id, solar_kw_installable)
  WHERE solar_verdict = 'accepted' AND solar_kw_installable IS NOT NULL;

COMMIT;
