-- 0023: conversions — closed-loop attribution (Part B.6)
--
-- One row per (lead, stage). The pixel endpoint inserts idempotently
-- (ON CONFLICT DO NOTHING). The POST endpoint upserts so a CRM
-- operator can correct the amount after the fact.
--
-- Stage lifecycle:
--   booked   — appointment confirmed in the lead-portal or CRM
--   quoted   — formal quote issued
--   won      — contract signed / payment received
--   lost     — deal fell through
--
-- The API also advances leads.pipeline_status to closed_won / closed_lost
-- when stage = won / lost is recorded.

CREATE TABLE IF NOT EXISTS public.conversions (
    id           UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id    UUID        NOT NULL REFERENCES public.tenants(id)  ON DELETE CASCADE,
    lead_id      UUID        NOT NULL REFERENCES public.leads(id)    ON DELETE CASCADE,
    stage        TEXT        NOT NULL,
    amount_cents INTEGER,                     -- nullable: pixel can't carry a value
    source       TEXT        NOT NULL DEFAULT 'pixel',  -- 'pixel' | 'api' | 'manual'
    closed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT conversions_stage_check
        CHECK (stage IN ('booked', 'quoted', 'won', 'lost')),

    -- Exactly one row per (lead, stage) — allows idempotent upserts.
    CONSTRAINT conversions_lead_stage_uniq
        UNIQUE (lead_id, stage)
);

-- Tenant-scoped time-range scans (overview KPIs, funnel card)
CREATE INDEX IF NOT EXISTS conversions_tenant_closed_at_idx
    ON public.conversions (tenant_id, closed_at DESC);

-- Dashboard lookup by lead (detail page — "this lead converted?")
CREATE INDEX IF NOT EXISTS conversions_lead_id_idx
    ON public.conversions (lead_id);

-- -------------------------------------------------------------------------
-- Row-level security
-- -------------------------------------------------------------------------

ALTER TABLE public.conversions ENABLE ROW LEVEL SECURITY;

-- Authenticated dashboard reads — scoped to the current tenant.
-- Writes always go through the service-role API (no INSERT policy needed).
CREATE POLICY "conversions_tenant_select"
    ON public.conversions FOR SELECT
    USING (tenant_id = auth_tenant_id());
