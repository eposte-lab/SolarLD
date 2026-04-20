-- 0024: audit_log — immutable append-only trail of operator actions.
--
-- One row per meaningful mutation (lead deleted, follow-up sent,
-- feedback updated, config changed …). The table is intentionally
-- NOT partitioned: audit rows are small and infrequent compared with
-- `events` / `portal_events`. A single btree index on (tenant_id, at)
-- is all we need for the dashboard viewer.
--
-- Security model:
--   - Writes are service-role only (no INSERT RLS policy).
--   - Dashboard reads via RLS: tenant_id = auth_tenant_id().
--   - No DELETE policy — the table is append-only by design.
--   - No FK on tenant_id: we want audit rows to survive even if a
--     tenant record is hard-deleted (forensics).

CREATE TABLE IF NOT EXISTS public.audit_log (
    id             BIGSERIAL    PRIMARY KEY,
    tenant_id      UUID         NOT NULL,
    actor_user_id  UUID,                      -- null for system/cron actions
    action         TEXT         NOT NULL,
    target_table   TEXT,
    target_id      TEXT,
    diff           JSONB,
    at             TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Main access pattern: "show last N actions for this tenant"
CREATE INDEX IF NOT EXISTS audit_log_tenant_at_idx
    ON public.audit_log (tenant_id, at DESC);

-- Secondary: per-object history ("what happened to lead X?")
CREATE INDEX IF NOT EXISTS audit_log_target_idx
    ON public.audit_log (tenant_id, target_table, target_id, at DESC);

-- -------------------------------------------------------------------------
-- Row-level security — reads only, no write policy
-- -------------------------------------------------------------------------

ALTER TABLE public.audit_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY "audit_log_tenant_select"
    ON public.audit_log FOR SELECT
    USING (tenant_id = auth_tenant_id());
