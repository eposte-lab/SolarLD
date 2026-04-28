-- ============================================================
-- 0068 — Cluster-level A/B engine (50/50 with auto-promotion)
-- ============================================================
-- Sprint 9 Fase B.1.
--
-- The legacy `template_experiments` table (migration 0026) runs a
-- single tenant-wide A/B on the email subject line. That conflates
-- segments: an installer with 5 different lead clusters (ATECO codes,
-- decision-maker roles, B2C provinces) ends up optimising for whatever
-- the largest cluster is, and starves the smaller ones of their own
-- winning copy.
--
-- We replace the random-per-send selector with a per-cluster
-- persistent assignment:
--
--   1. Each lead has a `cluster_signature` computed at insert (B2B:
--      "ateco41_m_ceo", B2C: "b2c_na").
--   2. Per (tenant, cluster) we keep exactly TWO `active` variants
--      (round_number, A and B). Each variant carries the 4 dynamic
--      copy fields used by the premium template:
--        copy_subject, copy_opening_line,
--        copy_proposition_line, cta_primary_label
--   3. Variant assignment is deterministic:
--        variant = 'A' if hash(uuid bytes) % 2 == 0 else 'B'
--      Same lead always gets the same variant, which is critical for
--      retries and follow-up steps.
--   4. A daily worker tallies sent/replied per variant; once we have
--      ≥100 send/variant we run a 2x2 chi-square. p<0.05 → promote
--      the higher reply-rate variant to `winner`, demote the other to
--      `loser`, generate a new round_number+1 pair via Claude Haiku
--      using the winner as baseline.
--
-- ``ab_test_metrics_daily`` keeps daily snapshots so the dashboard can
-- chart reply-rate convergence per cluster across time.

BEGIN;

-- -------------------------------------------------------------
-- 1. cluster_copy_variants — per-cluster A/B variant pairs
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cluster_copy_variants (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  cluster_signature TEXT NOT NULL,
  round_number      INT  NOT NULL DEFAULT 1,
  variant_label     CHAR(1) NOT NULL CHECK (variant_label IN ('A', 'B')),

  -- 4 dynamic copy fields consumed by the premium template
  copy_subject          TEXT NOT NULL,
  copy_opening_line     TEXT NOT NULL,
  copy_proposition_line TEXT NOT NULL,
  cta_primary_label     TEXT NOT NULL,

  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'winner', 'loser', 'no_difference', 'archived')),
  generated_by  TEXT NOT NULL DEFAULT 'haiku'
    CHECK (generated_by IN ('haiku', 'manual', 'seed')),
  generated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  promoted_at   TIMESTAMPTZ,

  -- Denormalised counters (refreshed by the daily worker; cheap reads
  -- for the dashboard A/B page).
  sent_count       INT NOT NULL DEFAULT 0,
  delivered_count  INT NOT NULL DEFAULT 0,
  opened_count     INT NOT NULL DEFAULT 0,
  clicked_count    INT NOT NULL DEFAULT 0,
  replied_count    INT NOT NULL DEFAULT 0,

  CONSTRAINT cluster_copy_variants_unique
    UNIQUE (tenant_id, cluster_signature, round_number, variant_label)
);

-- Hot path: "give me the active A+B for this (tenant, cluster)".
CREATE INDEX IF NOT EXISTS idx_ccv_active
  ON cluster_copy_variants (tenant_id, cluster_signature, round_number DESC)
  WHERE status = 'active';

-- Lookup by tenant for the dashboard list.
CREATE INDEX IF NOT EXISTS idx_ccv_tenant_status
  ON cluster_copy_variants (tenant_id, status, generated_at DESC);

ALTER TABLE cluster_copy_variants ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS ccv_tenant_iso ON cluster_copy_variants;
CREATE POLICY ccv_tenant_iso ON cluster_copy_variants
  FOR ALL TO authenticated
  USING (tenant_id = auth_tenant_id())
  WITH CHECK (tenant_id = auth_tenant_id());

COMMENT ON TABLE cluster_copy_variants IS
  'Sprint 9 Fase B — per-cluster A/B variant pairs. Each round has '
  'exactly one A and one B for a given (tenant, cluster_signature). '
  'When the daily evaluator picks a winner, the round is closed and '
  'a new round is generated with the winner as baseline.';

-- -------------------------------------------------------------
-- 2. leads — persistent variant assignment + cluster signature
-- -------------------------------------------------------------
ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS cluster_signature TEXT,
  ADD COLUMN IF NOT EXISTS assigned_variant  CHAR(1)
    CHECK (assigned_variant IS NULL OR assigned_variant IN ('A', 'B')),
  ADD COLUMN IF NOT EXISTS assigned_round    INT;

CREATE INDEX IF NOT EXISTS idx_leads_cluster_signature
  ON leads (tenant_id, cluster_signature)
  WHERE cluster_signature IS NOT NULL;

COMMENT ON COLUMN leads.cluster_signature IS
  'Sprint 9 Fase B — short string identifying which copy cluster this '
  'lead belongs to (B2B: "ateco41_m_ceo", B2C: "b2c_na"). Computed at '
  'lead insertion by cluster_service.compute_cluster_signature().';

COMMENT ON COLUMN leads.assigned_variant IS
  'Sprint 9 Fase B — persistent A/B assignment from hash(lead.id) % 2. '
  'Set on first outreach send and never reassigned, so retries and '
  'follow-up steps reuse the same variant.';

-- -------------------------------------------------------------
-- 3. ab_test_metrics_daily — chart history
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ab_test_metrics_daily (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  cluster_signature TEXT NOT NULL,
  round_number      INT  NOT NULL,
  variant_label     CHAR(1) NOT NULL CHECK (variant_label IN ('A', 'B')),
  date              DATE NOT NULL,

  sent_count    INT NOT NULL DEFAULT 0,
  replied_count INT NOT NULL DEFAULT 0,
  reply_rate    NUMERIC(5, 4),

  CONSTRAINT abmd_unique
    UNIQUE (tenant_id, cluster_signature, round_number, variant_label, date)
);

CREATE INDEX IF NOT EXISTS idx_abmd_lookup
  ON ab_test_metrics_daily
    (tenant_id, cluster_signature, round_number, date DESC);

ALTER TABLE ab_test_metrics_daily ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS abmd_tenant_iso ON ab_test_metrics_daily;
CREATE POLICY abmd_tenant_iso ON ab_test_metrics_daily
  FOR ALL TO authenticated
  USING (tenant_id = auth_tenant_id())
  WITH CHECK (tenant_id = auth_tenant_id());

COMMENT ON TABLE ab_test_metrics_daily IS
  'Sprint 9 Fase B — daily snapshot of (sent_count, replied_count) per '
  '(tenant, cluster, round, variant) used to chart reply-rate '
  'convergence in the dashboard A/B page.';

-- -------------------------------------------------------------
-- 4. outreach_sends → variant link
-- -------------------------------------------------------------
ALTER TABLE outreach_sends
  ADD COLUMN IF NOT EXISTS cluster_variant_id UUID
    REFERENCES cluster_copy_variants(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_outreach_sends_cluster_variant
  ON outreach_sends (cluster_variant_id)
  WHERE cluster_variant_id IS NOT NULL;

COMMENT ON COLUMN outreach_sends.cluster_variant_id IS
  'Sprint 9 Fase B — links the send to the cluster_copy_variants row '
  'whose copy was rendered. Used by the daily evaluator to aggregate '
  'sent/replied counters per variant.';

COMMIT;
