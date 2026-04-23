-- ============================================================
-- 0048 — leads.source  (engagement-origin column)
-- ============================================================
--
-- Context
-- -------
-- The `leads` table has historically conflated two concepts:
--   1. "Outreach candidate" — a Solar-qualified subject the system
--      has scored and *may* send an email to (source IS NULL).
--   2. "Active lead"       — a person who actively engaged by
--      clicking the email CTA or replying (source IS NOT NULL).
--
-- Before this migration the dashboard "Lead Attivi" counter simply
-- counted ALL rows in `leads`, making every scored subject appear
-- as an active lead.  That does not match the product model: an
-- installer expects "Lead Attivi" to mean people who showed intent,
-- not just a scan artefact.
--
-- This migration adds `leads.source` so the two populations can be
-- distinguished without a schema break.  Existing rows keep
-- source = NULL (candidate, no engagement recorded).  The API layer
-- sets source when a real engagement event fires:
--
--   cta_click     — lead submitted the portal appointment form
--   email_reply   — lead replied to the outreach email
--   whatsapp_reply — lead replied via WhatsApp
--
-- Dashboard query for Lead Attivi changes to:
--   SELECT count(*) FROM leads WHERE tenant_id = $1 AND source IS NOT NULL
-- ============================================================

ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS source TEXT
    CHECK (source IN ('cta_click', 'email_reply', 'whatsapp_reply'));

COMMENT ON COLUMN leads.source IS
  'Engagement channel that promoted this candidate to an active lead.
   NULL = outreach candidate (scored, may have been emailed, no intent signal yet).
   cta_click | email_reply | whatsapp_reply = active lead.';

-- Partial index — the vast majority of rows will be NULL; the index
-- only covers the active-lead minority, keeping it small.
CREATE INDEX IF NOT EXISTS idx_leads_source
  ON leads (tenant_id, source)
  WHERE source IS NOT NULL;
