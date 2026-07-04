-- ============================================================
-- 0163 — allow 'survey' as a decision_maker_phone_source
-- ============================================================
-- The dossier survey widget (progressive "one question at a time"
-- quiz) ends by asking the prospect for their phone. A self-provided
-- number is the HOTTEST contact we can get, so it is written to
-- subjects.decision_maker_phone with source='survey' (it beats any
-- scraped/Atoka number). Extend the CHECK to allow the new source.
-- ============================================================

ALTER TABLE subjects
  DROP CONSTRAINT IF EXISTS subjects_decision_maker_phone_source_check;

ALTER TABLE subjects
  ADD CONSTRAINT subjects_decision_maker_phone_source_check
  CHECK (
    decision_maker_phone_source IN ('atoka', 'website_scrape', 'manual', 'survey')
    OR decision_maker_phone_source IS NULL
  );
