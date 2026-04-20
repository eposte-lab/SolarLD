-- ============================================================
-- 0020 — domain reputation + warm-up
-- ============================================================
--
-- Deliverability guardrails (Part B.5 in the product plan):
--
--   1. `tenants.email_from_domain_verified_at` — the day the tenant
--      proved DKIM/SPF/DMARC. The outreach rate-limiter looks at
--      (now - verified_at) to decide whether to apply warm-up caps
--      (20 → 2000 mail/die over 7 days) or the steady-state cap.
--      Nullable: null = never verified → treated as cold domain.
--
--   2. `domain_reputation` — one row per (tenant_id, domain, as_of_date)
--      written by the nightly `reputation_digest` cron. The dashboard
--      reads the latest row to render the "Reputazione dominio" card
--      and decide whether to show the red banner.
--
--      Metrics are computed over the last 7 days of activity (rolling
--      window ending on `as_of_date`):
--
--        sent_count        — count(*) from campaigns where status in
--                            ('sent','delivered','failed') and channel=email
--        delivered_count   — count(*) from campaigns where status='delivered'
--        bounced_count     — count(distinct events.lead_id) where
--                            event_type='lead.email_bounced'
--        complained_count  — same, event_type='lead.email_complained'
--        opened_count      — count(distinct leads.id) where
--                            outreach_opened_at not null AND in (campaigns)
--
--      Rates are computed in Python (trivially derivable from counts)
--      to keep the SQL simple. The dashboard reads counts + the small
--      snapshot of rates for history plotting.
--
-- Hard floor: if we ever drop this table, the warm-up logic still
-- works because it reads `tenants.email_from_domain_verified_at`
-- directly — reputation is strictly additive signal.

-- ---------------------------------------------------------------
-- 1) tenants: warm-up trigger column
-- ---------------------------------------------------------------

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS email_from_domain_verified_at TIMESTAMPTZ;

COMMENT ON COLUMN tenants.email_from_domain_verified_at IS
  'When the outbound domain was verified (DKIM/SPF/DMARC). '
  'NULL => cold domain, warm-up caps apply indefinitely until set. '
  'Within 7 days of this timestamp the outreach rate-limiter applies '
  'a daily-ramp cap (20/50/100/200/500/1000/2000). After 7 days the '
  'steady-state hourly cap takes over.';

-- ---------------------------------------------------------------
-- 2) domain_reputation: nightly snapshot
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS domain_reputation (
  id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  email_from_domain   TEXT NOT NULL,
  as_of_date          DATE NOT NULL,

  -- Counts over the rolling 7-day window ending on as_of_date.
  sent_count          INTEGER NOT NULL DEFAULT 0,
  delivered_count     INTEGER NOT NULL DEFAULT 0,
  bounced_count       INTEGER NOT NULL DEFAULT 0,
  complained_count    INTEGER NOT NULL DEFAULT 0,
  opened_count        INTEGER NOT NULL DEFAULT 0,

  -- Precomputed rates (0..1) for UI. Null when denominator = 0.
  -- delivery_rate = delivered / sent
  -- bounce_rate   = bounced   / sent     (the industry-standard base)
  -- complaint_rate= complained/ delivered
  -- open_rate     = opened    / delivered
  delivery_rate       NUMERIC(5, 4),
  bounce_rate         NUMERIC(5, 4),
  complaint_rate      NUMERIC(5, 4),
  open_rate           NUMERIC(5, 4),

  -- Alarm flags — cheap precompute so the dashboard doesn't need
  -- to re-derive thresholds. Sources:
  --   bounce_rate > 0.05     → AWS SES sends a warning; >0.10 suspends
  --   complaint_rate > 0.003 → AWS SES warning; >0.005 suspends
  alarm_bounce        BOOLEAN NOT NULL DEFAULT false,
  alarm_complaint     BOOLEAN NOT NULL DEFAULT false,

  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (tenant_id, email_from_domain, as_of_date)
);

CREATE INDEX IF NOT EXISTS idx_domain_reputation_tenant_date
  ON domain_reputation(tenant_id, as_of_date DESC);

CREATE INDEX IF NOT EXISTS idx_domain_reputation_alarms
  ON domain_reputation(tenant_id)
  WHERE alarm_bounce OR alarm_complaint;

-- ---------------------------------------------------------------
-- 3) RLS: tenants see their own reputation, service role sees all
-- ---------------------------------------------------------------

ALTER TABLE domain_reputation ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS domain_reputation_tenant_select ON domain_reputation;
CREATE POLICY domain_reputation_tenant_select ON domain_reputation
  FOR SELECT
  USING (tenant_id = auth_tenant_id());

-- No INSERT/UPDATE/DELETE policy => writes restricted to service role
-- (the nightly cron job). Keeps the table append-only from the
-- dashboard's perspective.
