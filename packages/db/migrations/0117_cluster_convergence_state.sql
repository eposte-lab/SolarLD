-- 0117_cluster_convergence_state.sql
--
-- Adds cluster_state — one row per (tenant, cluster_signature) tracking
-- whether an A/B test has converged. The cluster_ab_evaluator_service
-- writes here after every winner declaration; once `consecutive_wins`
-- reaches CONSECUTIVE_WINS_FOR_CONVERGENCE (=2) the evaluator stops
-- generating new variants and the OutreachAgent serves 100% of the
-- traffic to `champion_variant_id`.
--
-- This is what stops the "infinite testing" pathology where every
-- declared winner immediately spawns a new B challenger and the
-- operator never gets to enjoy a stable winner.
--
-- The drift detection cron unlocks converged clusters every 90 days
-- (or when an operator clicks "Sfida il vincitore" on the dashboard,
-- which sets `unlocked_at` and resets `converged_at`).

CREATE TABLE IF NOT EXISTS cluster_state (
  tenant_id              UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  cluster_signature      TEXT NOT NULL,
  consecutive_wins       INT  NOT NULL DEFAULT 0,
  last_winner_label      CHAR(1) NULL CHECK (last_winner_label IN ('A','B')),
  converged_at           TIMESTAMPTZ NULL,
  champion_variant_id    UUID NULL REFERENCES cluster_copy_variants(id) ON DELETE SET NULL,
  unlocked_at            TIMESTAMPTZ NULL,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (tenant_id, cluster_signature)
);

CREATE INDEX IF NOT EXISTS cluster_state_converged_idx
  ON cluster_state(tenant_id) WHERE converged_at IS NOT NULL;

COMMENT ON TABLE cluster_state IS
  'Per-cluster A/B convergence state. Set by cluster_ab_evaluator_service '
  'after winner declarations. When converged_at is set, OutreachAgent '
  'serves 100%% of traffic to champion_variant_id and the evaluator skips '
  'generate_variant_pair until weekly_cluster_refresh_cron unlocks it '
  '(90 days drift) or the operator clicks "Sfida il vincitore".';

COMMENT ON COLUMN cluster_state.consecutive_wins IS
  'Number of consecutive rounds where last_winner_label has won. '
  'When >= CONSECUTIVE_WINS_FOR_CONVERGENCE (=2) the cluster converges.';

COMMENT ON COLUMN cluster_state.champion_variant_id IS
  'The cluster_copy_variants.id that gets 100%% of traffic post-convergence. '
  'NULL while the cluster is still in active testing.';

COMMENT ON COLUMN cluster_state.unlocked_at IS
  'Set when an operator manually challenges the converged winner via the '
  'POST /v1/cluster-ab/{sig}/unlock endpoint. Distinguishes manual unlocks '
  'from automatic drift refreshes for analytics.';
