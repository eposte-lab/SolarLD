-- ============================================================
-- 0076 — Decision-maker phone on subjects (free Atoka + scrape)
-- ============================================================
--
-- Purpose
--   Surface the decision-maker phone in the lead anagrafica.
--   Atoka already returns it inside `raw.phones[]` /
--   `raw.contacts[].value` / `raw.base.phone` as part of the
--   `includeContacts:true` bundle we already pay for (€0.15/lookup),
--   so capturing it costs nothing extra. The website email scraper
--   is also extended in the same loop to catch phones on the
--   "Contatti" page when Atoka doesn't have one.
--
-- Source provenance
--   Stored alongside the phone so the UI can show a small badge
--   ("Atoka", "Sito web", "Manuale") and ops can audit data
--   quality. NULL = no phone found yet — the column itself is
--   nullable.
-- ============================================================

ALTER TABLE subjects
  ADD COLUMN IF NOT EXISTS decision_maker_phone TEXT;

ALTER TABLE subjects
  ADD COLUMN IF NOT EXISTS decision_maker_phone_source TEXT
  CHECK (
    decision_maker_phone_source IN ('atoka', 'website_scrape', 'manual')
    OR decision_maker_phone_source IS NULL
  );

-- Partial index to quickly answer "how many lead have a phone?"
-- without scanning the rows where the field is NULL (the majority
-- on day-1 until Atoka backfill catches up).
CREATE INDEX IF NOT EXISTS idx_subjects_has_phone
  ON subjects (tenant_id)
  WHERE decision_maker_phone IS NOT NULL;
