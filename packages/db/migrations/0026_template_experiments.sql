-- ============================================================
-- Migration 0026 — template_experiments: A/B subject-line tests
-- on outreach emails (Part B.4, tier=enterprise).
-- ============================================================
--
-- Each experiment pits two email subject lines against each other on
-- the first-contact outreach step.  The OutreachAgent samples a variant
-- at send-time (random < split_pct/100 → variant A, else → variant B)
-- and records the choice in campaigns.experiment_variant.
--
-- Winner declaration is manual (operator looks at the Bayesian stats
-- card in /experiments and clicks "Dichiara vincitore") or can be
-- triggered automatically by the nightly stats cron once
-- P(A > B) or P(B > A) crosses 0.95.

CREATE TABLE IF NOT EXISTS public.template_experiments (
  id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id             UUID        NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  name                  TEXT        NOT NULL,

  -- Subject lines under test
  variant_a_subject     TEXT        NOT NULL,
  variant_b_subject     TEXT        NOT NULL,

  -- Fraction of sends going to variant A (1–99). Default 50/50.
  split_pct             SMALLINT    NOT NULL DEFAULT 50
                                    CHECK (split_pct BETWEEN 1 AND 99),

  -- Lifecycle
  started_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at              TIMESTAMPTZ,           -- NULL = still running
  winner                TEXT        CHECK (winner IN ('a', 'b')),
  winner_declared_at    TIMESTAMPTZ,

  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_experiments_tenant
  ON public.template_experiments (tenant_id, started_at DESC);

-- One active experiment per tenant at a time is enforced by the API layer
-- (not a DB constraint — it lets us keep history).

-- ── campaigns: track which experiment & variant each email belongs to ──

ALTER TABLE public.campaigns
  ADD COLUMN IF NOT EXISTS experiment_id      UUID
    REFERENCES public.template_experiments(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS experiment_variant TEXT
    CHECK (experiment_variant IN ('a', 'b'));

CREATE INDEX IF NOT EXISTS idx_campaigns_experiment
  ON public.campaigns (experiment_id, experiment_variant)
  WHERE experiment_id IS NOT NULL;

-- ── RLS ──

ALTER TABLE public.template_experiments ENABLE ROW LEVEL SECURITY;

CREATE POLICY "experiments_tenant_select"
  ON public.template_experiments
  FOR SELECT
  USING (tenant_id = auth_tenant_id());

-- INSERT / UPDATE / DELETE only via API service-role (bypasses RLS).
