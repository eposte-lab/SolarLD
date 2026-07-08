-- ============================================================
-- 0165 — decision-maker + contact-quality fields on subjects
-- ("Persona responsabile" delta)
-- ============================================================
-- Additive + idempotent. Adds ONLY the columns that do not already
-- exist on subjects. Already present (do NOT re-add):
--   decision_maker_name/role/email/email_verified (0005),
--   decision_maker_phone/phone_source (0076), linkedin_url (0005),
--   decision_maker_email_source/email_fallback (0150).
--
-- Free TEXT (no CHECK) on purpose — same style as decision_maker_email_source
-- — while the value vocabularies settle. Intended values in the comments.
-- All writers are behind default-OFF flags, so this migration is inert until
-- the "persona responsabile" feature is switched on.
-- ============================================================

BEGIN;

ALTER TABLE subjects
  -- where the decision-maker NAME came from: 'registro' | 'linkedin' | 'both'
  --   | 'hunter' | 'website' | 'role_ladder'
  ADD COLUMN IF NOT EXISTS decision_maker_source     TEXT,
  -- name confidence label: 'alta' | 'media' | 'bassa'
  ADD COLUMN IF NOT EXISTS decision_maker_confidence TEXT,
  -- send channel chosen by the cascade: 'email' | 'pec' | 'phone'
  ADD COLUMN IF NOT EXISTS contact_channel           TEXT,
  -- copy tone for the channel: 'personal' | 'pec_safe'
  ADD COLUMN IF NOT EXISTS tone                      TEXT,
  -- NeverBounce verdict for the chosen email: 'valid' | 'accept_all'
  --   | 'invalid' | 'unknown'
  ADD COLUMN IF NOT EXISTS email_status              TEXT,
  -- email confidence label: 'alta' | 'media' | 'bassa'
  ADD COLUMN IF NOT EXISTS email_confidence          TEXT;

COMMIT;
