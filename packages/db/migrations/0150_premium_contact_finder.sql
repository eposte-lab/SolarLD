-- 0150 — Premium decision-maker contact finder: provenance + capped budget.
--
-- The v3 funnel only scrapes the website, so the chosen email is often a
-- generic role inbox (info@, or an inferred info@<domain>). We add an optional
-- "premium finder" step (Hunter domain-search + NeverBounce validation) that
-- upgrades a weak email to a named decision-maker. Two things need persisting:
--
--   1. PROVENANCE on subjects — so the UI can show a "premium / verified"
--      badge and the operator/audit can see WHERE the email came from. The
--      decision_maker_role column already exists (0005/0095); we only add the
--      source marker + a fallback (the original website email we keep as a
--      backup).
--
--   2. A CAPPED, ATOMIC BUDGET — the operator funds the finder with a fixed
--      euro budget (default €50). We track spend + lookups per tenant and
--      expose a SECURITY DEFINER reserve function that atomically charges a
--      lookup ONLY if it stays within budget, so concurrent funnel workers
--      and on-demand clicks can never overspend.
--
-- Additive + idempotent.

-- 1) Provenance on subjects ---------------------------------------------------
ALTER TABLE public.subjects
  ADD COLUMN IF NOT EXISTS decision_maker_email_source TEXT,   -- 'website_scrape' | 'premium_finder' | 'manual'
  ADD COLUMN IF NOT EXISTS decision_maker_email_fallback TEXT; -- original website email kept as backup

-- 2) Per-tenant capped budget -------------------------------------------------
CREATE TABLE IF NOT EXISTS public.premium_contact_usage (
  tenant_id    UUID PRIMARY KEY REFERENCES public.tenants(id) ON DELETE CASCADE,
  budget_cents INTEGER NOT NULL DEFAULT 5000,   -- €50.00
  spend_cents  INTEGER NOT NULL DEFAULT 0,
  lookups      INTEGER NOT NULL DEFAULT 0,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Service-role only (written by the worker/API). No RLS policy → invisible to
-- anon/authenticated, consistent with other internal accounting tables.
ALTER TABLE public.premium_contact_usage ENABLE ROW LEVEL SECURITY;

-- Atomically charge ONE lookup. Returns TRUE iff the charge stayed within the
-- tenant's budget (so the caller may proceed with the paid API call); FALSE
-- when the budget is exhausted (caller must skip the lookup).
CREATE OR REPLACE FUNCTION public.reserve_premium_budget(
  p_tenant_id UUID,
  p_cost_cents INTEGER
) RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_ok BOOLEAN;
BEGIN
  INSERT INTO public.premium_contact_usage (tenant_id)
  VALUES (p_tenant_id)
  ON CONFLICT (tenant_id) DO NOTHING;

  UPDATE public.premium_contact_usage
     SET spend_cents = spend_cents + p_cost_cents,
         lookups     = lookups + 1,
         updated_at  = now()
   WHERE tenant_id = p_tenant_id
     AND spend_cents + p_cost_cents <= budget_cents
  RETURNING TRUE INTO v_ok;

  RETURN COALESCE(v_ok, FALSE);
END;
$$;
