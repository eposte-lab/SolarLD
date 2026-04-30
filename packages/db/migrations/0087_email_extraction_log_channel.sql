-- ADR-003: contact-extractor channel tagging
--
-- Adds a `channel` column to `email_extraction_log` so the new
-- `contact_extractor.py` wrapper can record which medium it produced
-- (email / whatsapp / phone_only) without renaming the table.  We keep
-- the historic name `email_extraction_log` to avoid touching the 1094
-- LOC of `email_extractor.py` plus every consumer in this commit.
--
-- DEFAULT 'email' covers every existing row at write time; no backfill
-- query is needed.  The CHECK pin is intentional: a future channel
-- requires a deliberate migration, so a typo or a stray value can't
-- silently leak into the funnel.
--
-- The index is PARTIAL on `channel != 'email'`: ~85 % of rows are
-- expected to remain email-channel, and routine queries
-- (`SELECT … WHERE tenant_id = ?`) don't need a channel index because
-- they already use the existing `(tenant_id, occurred_at)` indexes.
-- The partial index keeps writes on the email path zero-overhead and
-- only pays its cost for the minority WhatsApp / phone-only rows that
-- ops genuinely wants to filter on (e.g. "show me the leads we
-- couldn't email in the last 7 days").

ALTER TABLE email_extraction_log
  ADD COLUMN channel TEXT NOT NULL DEFAULT 'email'
  CHECK (channel IN ('email', 'whatsapp', 'phone_only'));

CREATE INDEX IF NOT EXISTS idx_email_extraction_log_non_email_channel
  ON email_extraction_log (tenant_id, occurred_at DESC)
  WHERE channel <> 'email';

COMMENT ON COLUMN email_extraction_log.channel IS
  'Outreach channel produced by contact_extractor: '
  '''email'' (default — Atoka or website scrape), '
  '''whatsapp'' (Atoka WhatsApp number, future cold-WA path), '
  '''phone_only'' (lead has no email, only phone — manual call queue).';
