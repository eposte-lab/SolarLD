-- 0152 — Contact-enrichment waterfall: per-domain cache + per-lead outcome.
--
-- The contact-enrichment waterfall (Hunter-first → name+pattern guess → role
-- ladder, all verified, catch-all gated) needs two cheap stores:
--
--   1. domain_intel — a GLOBAL (not tenant-scoped) cache so we pay Hunter /
--      catch-all detection ONCE per domain, reused across every lead/tenant on
--      that domain. A domain's mail pattern + catch-all status is tenant-
--      independent. Service-role only (no RLS policy → invisible to anon/
--      authenticated), consistent with premium_contact_usage.
--
--   2. Per-lead OUTCOME on leads (best_contact_email / contact_outcome / cost /
--      enriched_at) — the spec's terminal state, written every run. This also
--      gives idempotency (skip when contact_outcome already set, unless forced)
--      without a separate jobs table (arq already has its own job store).
--
-- Additive + idempotent.

-- 1) Per-domain intelligence cache --------------------------------------------
CREATE TABLE IF NOT EXISTS public.domain_intel (
  domain          TEXT PRIMARY KEY,
  email_pattern   TEXT,            -- Hunter pattern, e.g. '{first}.{last}'
  accept_all      BOOLEAN,         -- Hunter accept_all (domain-level)
  catch_all       BOOLEAN,         -- resolved catch-all (NULL = not yet detected)
  mx_valid        BOOLEAN,
  last_checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.domain_intel ENABLE ROW LEVEL SECURITY;

-- 2) Per-lead enrichment outcome ----------------------------------------------
ALTER TABLE public.leads
  ADD COLUMN IF NOT EXISTS best_contact_email           TEXT,
  ADD COLUMN IF NOT EXISTS contact_outcome              TEXT,  -- done|done_unverified|phone_queue|needs_manual|failed
  ADD COLUMN IF NOT EXISTS contact_enrichment_cost_cents INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS contact_enriched_at          TIMESTAMPTZ;
