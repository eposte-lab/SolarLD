-- ============================================================
-- 0166 — energivori Delta 2: contact-gate bookkeeping on items
-- ============================================================
-- The gate (registro decision-maker + personal-email verify) runs BEFORE the
-- costly roof(Google Solar)/render(Replicate). DROP companies are NOT deleted:
-- they are marked here + kept as a reusable "retry later" queue, and never
-- reach validation/render (validation_status='skipped').
--
--   funnel_excluded_reason : why the company was dropped (NULL = passed / not
--     gated). One of: generic_email_only | no_decision_maker | no_domain |
--     unverifiable_strict.
--   enriched_at            : when the gate enrichment ran (UTC) — the cutoff for
--     idempotent re-enrichment (skip if recent, e.g. < 90 days).
--   enrichment_outcome     : JSONB snapshot of what was found (decision-maker,
--     domain, email_status/confidence/source, candidates) — so an old record
--     can be re-tried once Hunter/registro data changes.
--
-- Additive + idempotent; inert until email_gate_enabled is switched on.
-- ============================================================

BEGIN;

ALTER TABLE prospect_list_items
  ADD COLUMN IF NOT EXISTS funnel_excluded_reason TEXT,
  ADD COLUMN IF NOT EXISTS enriched_at            TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS enrichment_outcome     JSONB;

-- Query the DROP queue by reason + age (candidates for a second enrichment pass).
CREATE INDEX IF NOT EXISTS idx_pli_excluded_retry
  ON prospect_list_items (tenant_id, funnel_excluded_reason, enriched_at)
  WHERE funnel_excluded_reason IS NOT NULL;

COMMIT;
