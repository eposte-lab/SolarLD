-- ============================================================
-- 0099 — scan_candidates: predicted_sector columns
-- ============================================================
-- Extends scan_candidates with the sector-aware tags written by L1
-- of the hunter funnel.
--
--   * predicted_sector       — wizard_group from ateco_google_types
--                              the candidate most likely belongs to
--                              (NULL when we couldn't classify).
--   * predicted_ateco_codes  — ATECO codes Haiku returned in L3 as
--                              "questi sembrano i codici reali"
--                              (validated against ateco_google_types
--                              before persistence).
--   * sector_confidence      — float 0.0-1.0 from
--                              sector_target_service.predict_sector
--                              (1.0 exact ATECO match, 0.7 prefix,
--                              0.4 fuzzy name).
--
-- All three are nullable so legacy rows (pre-sprint-A) keep working.
-- The dashboard lead-detail page falls back to "non determinato"
-- when predicted_sector is NULL.
--
-- See plan: shimmying-painting-backus.md, Sprint B.1.

ALTER TABLE scan_candidates
  ADD COLUMN IF NOT EXISTS predicted_sector       TEXT,
  ADD COLUMN IF NOT EXISTS predicted_ateco_codes  TEXT[] NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS sector_confidence      DECIMAL(4,2);

CREATE INDEX IF NOT EXISTS idx_scan_candidates_predicted_sector
  ON scan_candidates(tenant_id, predicted_sector)
  WHERE predicted_sector IS NOT NULL;

COMMENT ON COLUMN scan_candidates.predicted_sector IS
  'wizard_group from ateco_google_types this candidate most likely belongs to. Stamped at L1 by sector_target_service.predict_sector_for_candidate based on ATECO + business_name. NULL when no enabled wizard_group matches.';

COMMENT ON COLUMN scan_candidates.predicted_ateco_codes IS
  'ATECO codes Haiku returned in L3 as the most likely real codes for this candidate (cross-checked against ateco_google_types.ateco_code before persistence to filter LLM hallucinations).';

COMMENT ON COLUMN scan_candidates.sector_confidence IS
  'Confidence (0.0-1.0) of predicted_sector. 1.0 = exact ATECO match in seed; 0.7 = 2-digit prefix match; 0.4 = fuzzy site_signal_keyword match on business_name.';
