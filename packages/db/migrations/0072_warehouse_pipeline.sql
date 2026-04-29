-- ============================================================
-- 0072 — Sprint 11 — Warehouse pipeline + variable per-tenant cap
-- ============================================================
-- Refactor the discovery → outreach pipeline around a per-tenant
-- "magazzino" of leads that are vetted and pre-staged but NOT yet
-- rendered. Pick-time generates the heavy assets (Solar + Kling)
-- only on the leads we actually intend to send today, and the daily
-- discovery cycle is triggered conditionally based on warehouse runway.
--
-- New ENUM values (additive — existing rows untouched):
--   discovered      raw Atoka discovery, not enriched yet
--   enriched        Places / Visura attached
--   scored          Haiku scoring done
--   qualified       Solar gate passed (roof viable)
--   ready_to_send   in warehouse, awaiting pick
--   picked          dequeued, asset generation kicked off
--   rendering       Solar+Kling in progress
--   rendered        assets ready, about to be sent
--   expired         removed from warehouse after lead_expiration_days
--
-- Existing values kept as-is: new, sent, delivered, opened, clicked,
-- engaged, whatsapp, appointment, closed_won, closed_lost, blacklisted.
-- (`new` is now considered a transient pre-pipeline state and may be
-- backfilled to `discovered` by a separate one-shot script.)
--
-- Lead lifecycle in the new world:
--   discovered → enriched → scored → qualified → ready_to_send
--      → (pick) picked → rendering → rendered → sent → delivered
--      → opened → clicked → engaged → appointment → closed_*
--   side branches: blacklisted (any time), expired (from ready_to_send).

BEGIN;

-- ------------------------------------------------------------
-- 1) Extend lead_status enum (additive)
-- ------------------------------------------------------------
-- ALTER TYPE … ADD VALUE cannot run inside a transaction block in
-- some Postgres versions; we issue them outside the BEGIN/COMMIT
-- frame at the end if needed. Supabase migrations runner handles
-- this, but we use IF NOT EXISTS for idempotency.
COMMIT;

ALTER TYPE lead_status ADD VALUE IF NOT EXISTS 'discovered'    BEFORE 'sent';
ALTER TYPE lead_status ADD VALUE IF NOT EXISTS 'enriched'      BEFORE 'sent';
ALTER TYPE lead_status ADD VALUE IF NOT EXISTS 'scored'        BEFORE 'sent';
ALTER TYPE lead_status ADD VALUE IF NOT EXISTS 'qualified'     BEFORE 'sent';
ALTER TYPE lead_status ADD VALUE IF NOT EXISTS 'ready_to_send' BEFORE 'sent';
ALTER TYPE lead_status ADD VALUE IF NOT EXISTS 'picked'        BEFORE 'sent';
ALTER TYPE lead_status ADD VALUE IF NOT EXISTS 'rendering'     BEFORE 'sent';
ALTER TYPE lead_status ADD VALUE IF NOT EXISTS 'rendered'      BEFORE 'sent';
ALTER TYPE lead_status ADD VALUE IF NOT EXISTS 'expired';

BEGIN;

-- ------------------------------------------------------------
-- 2) Tenants: variable cap + warehouse policy
-- ------------------------------------------------------------
-- daily_target_send_cap already exists from 0055 — keep as the
-- effective daily cap. Min/max bound the admin slider; warehouse
-- buffer drives "when to trigger a fresh discovery cycle"; the
-- expiration window controls auto-cleanup.
ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS daily_send_cap_min       INTEGER NOT NULL DEFAULT 50,
  ADD COLUMN IF NOT EXISTS daily_send_cap_max       INTEGER NOT NULL DEFAULT 250,
  ADD COLUMN IF NOT EXISTS warehouse_buffer_days    INTEGER NOT NULL DEFAULT 7,
  ADD COLUMN IF NOT EXISTS lead_expiration_days     INTEGER NOT NULL DEFAULT 21,
  ADD COLUMN IF NOT EXISTS atoka_survival_target    NUMERIC(4,3) NOT NULL DEFAULT 0.800;

ALTER TABLE tenants
  DROP CONSTRAINT IF EXISTS tenants_send_cap_bounds;
ALTER TABLE tenants
  ADD  CONSTRAINT tenants_send_cap_bounds
       CHECK (
         daily_send_cap_min BETWEEN 1 AND 5000
         AND daily_send_cap_max BETWEEN daily_send_cap_min AND 5000
         AND daily_target_send_cap BETWEEN daily_send_cap_min AND daily_send_cap_max
       );

ALTER TABLE tenants
  DROP CONSTRAINT IF EXISTS tenants_warehouse_window_sane;
ALTER TABLE tenants
  ADD  CONSTRAINT tenants_warehouse_window_sane
       CHECK (
         warehouse_buffer_days BETWEEN 1 AND 30
         AND lead_expiration_days BETWEEN warehouse_buffer_days AND 90
       );

ALTER TABLE tenants
  DROP CONSTRAINT IF EXISTS tenants_atoka_survival_target_pct;
ALTER TABLE tenants
  ADD  CONSTRAINT tenants_atoka_survival_target_pct
       CHECK (atoka_survival_target BETWEEN 0.10 AND 1.00);

COMMENT ON COLUMN tenants.daily_send_cap_min     IS 'Lower bound of the admin slider for daily cap (default 50).';
COMMENT ON COLUMN tenants.daily_send_cap_max     IS 'Upper bound of the admin slider for daily cap (default 250).';
COMMENT ON COLUMN tenants.warehouse_buffer_days  IS 'Trigger a new discovery cycle when warehouse runway < this many days.';
COMMENT ON COLUMN tenants.lead_expiration_days   IS 'A lead in ready_to_send for more than this many days is auto-expired.';
COMMENT ON COLUMN tenants.atoka_survival_target  IS 'Target ratio of post-filter survivors. Below this we alert admins.';

-- ------------------------------------------------------------
-- 3) Leads: warehouse timestamps
-- ------------------------------------------------------------
ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS enqueued_to_warehouse_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS picked_at                TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS rendered_at              TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS expires_at               TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS expired_at               TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_status_transition_at TIMESTAMPTZ NOT NULL DEFAULT now();

COMMENT ON COLUMN leads.enqueued_to_warehouse_at IS 'When the lead entered ready_to_send. Drives FIFO pick.';
COMMENT ON COLUMN leads.expires_at               IS 'enqueued_to_warehouse_at + tenant.lead_expiration_days. NULL until staged.';
COMMENT ON COLUMN leads.picked_at                IS 'When the daily orchestrator dequeued this lead for asset generation.';
COMMENT ON COLUMN leads.rendered_at              IS 'When pick-time asset generation (Solar+Kling) completed.';

-- FIFO pick (only the partial index — small + hot)
CREATE INDEX IF NOT EXISTS idx_leads_warehouse_fifo
  ON leads (tenant_id, enqueued_to_warehouse_at)
  WHERE pipeline_status = 'ready_to_send';

-- Expiration sweep
CREATE INDEX IF NOT EXISTS idx_leads_warehouse_expiry
  ON leads (expires_at)
  WHERE pipeline_status = 'ready_to_send';

-- ------------------------------------------------------------
-- 4) reverification_queue
-- ------------------------------------------------------------
-- Leads that hit `expired` but the underlying subject + roof are
-- still potentially valuable. The cleanup worker enqueues them; an
-- admin tool (or a future weekly job) can re-pull Atoka data and
-- re-promote them into the warehouse.
CREATE TABLE IF NOT EXISTS reverification_queue (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  lead_id         UUID NOT NULL REFERENCES leads(id)   ON DELETE CASCADE,
  reason          TEXT NOT NULL DEFAULT 'expired_in_warehouse',
  enqueued_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  attempted_at    TIMESTAMPTZ,
  resolved_at     TIMESTAMPTZ,
  outcome         TEXT,            -- 'requeued' | 'discarded' | 'blacklisted'
  notes           TEXT,
  UNIQUE (tenant_id, lead_id)
);

CREATE INDEX IF NOT EXISTS idx_reverification_pending
  ON reverification_queue (tenant_id, enqueued_at)
  WHERE resolved_at IS NULL;

ALTER TABLE reverification_queue ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rvq_tenant_iso ON reverification_queue;
CREATE POLICY rvq_tenant_iso ON reverification_queue
  FOR ALL TO authenticated
  USING (tenant_id = auth_tenant_id())
  WITH CHECK (tenant_id = auth_tenant_id());

COMMENT ON TABLE reverification_queue IS
  'Leads expired from the warehouse that may still be valuable. Worked off-line by admin tools or a weekly job.';

-- ------------------------------------------------------------
-- 5) warehouse_health view (per tenant snapshot)
-- ------------------------------------------------------------
-- Powers the dashboard "magazzino" widget. Cheap because of the
-- partial index on ready_to_send.
CREATE OR REPLACE VIEW warehouse_health AS
SELECT
  t.id                                               AS tenant_id,
  t.daily_target_send_cap                            AS daily_cap,
  t.warehouse_buffer_days                            AS buffer_days,
  t.lead_expiration_days                             AS expiration_days,
  COALESCE(w.ready_count, 0)                         AS ready_to_send_count,
  COALESCE(w.expiring_within_3d, 0)                  AS expiring_within_3d,
  COALESCE(w.oldest_age_days, 0)                     AS oldest_age_days,
  CASE
    WHEN t.daily_target_send_cap = 0 THEN NULL
    ELSE ROUND(
      COALESCE(w.ready_count, 0)::numeric / t.daily_target_send_cap,
      1
    )
  END                                                AS runway_days,
  (
    COALESCE(w.ready_count, 0)
      < (t.daily_target_send_cap * t.warehouse_buffer_days)
  )                                                  AS needs_refill
FROM tenants t
LEFT JOIN LATERAL (
  SELECT
    COUNT(*)                                         AS ready_count,
    SUM(
      CASE
        WHEN l.expires_at IS NOT NULL
         AND l.expires_at <= now() + INTERVAL '3 days'
        THEN 1 ELSE 0
      END
    )                                                AS expiring_within_3d,
    EXTRACT(
      DAY FROM
      now() - MIN(l.enqueued_to_warehouse_at)
    )::int                                           AS oldest_age_days
  FROM leads l
  WHERE l.tenant_id = t.id
    AND l.pipeline_status = 'ready_to_send'
) w ON TRUE;

COMMENT ON VIEW warehouse_health IS
  'Per-tenant snapshot of the lead warehouse: depth, runway, pressure points. Read by dashboard /api/tenant/warehouse.';

-- ------------------------------------------------------------
-- 6) warehouse_pick — atomic FIFO pick + status transition
-- ------------------------------------------------------------
-- The daily orchestrator calls this RPC to dequeue up to N leads in
-- one transaction. FOR UPDATE SKIP LOCKED makes concurrent workers
-- safe (we shouldn't have more than one orchestrator running per
-- tenant, but if two cron clocks ever overlap we still want
-- correctness, not duplicates).
--
-- Output rows are the lead ids in pick order; the caller is expected
-- to enqueue an asset-render job per id. The status transition to
-- `picked` is committed atomically with the SELECT, so a crash mid-way
-- through enqueue won't leave a lead "stolen but not enqueued" — at
-- worst the lead is `picked` with no follow-up job, and the next
-- orchestrator run treats that as a stale-pick and re-queues it (see
-- `warehouse_unstick_picked` below).
CREATE OR REPLACE FUNCTION warehouse_pick(
  p_tenant_id UUID,
  p_count     INT
) RETURNS TABLE (
  lead_id                  UUID,
  enqueued_to_warehouse_at TIMESTAMPTZ,
  expires_at               TIMESTAMPTZ
) AS $$
DECLARE
  v_now TIMESTAMPTZ := now();
BEGIN
  IF p_count <= 0 THEN
    RETURN;
  END IF;

  RETURN QUERY
  WITH picked AS (
    SELECT l.id
    FROM leads l
    WHERE l.tenant_id        = p_tenant_id
      AND l.pipeline_status  = 'ready_to_send'
      AND (l.expires_at IS NULL OR l.expires_at > v_now)
    ORDER BY l.enqueued_to_warehouse_at NULLS LAST, l.created_at
    LIMIT p_count
    FOR UPDATE SKIP LOCKED
  ),
  upd AS (
    UPDATE leads
       SET pipeline_status            = 'picked',
           picked_at                  = v_now,
           last_status_transition_at  = v_now
     WHERE id IN (SELECT id FROM picked)
    RETURNING id, enqueued_to_warehouse_at, expires_at
  )
  SELECT id, enqueued_to_warehouse_at, expires_at FROM upd;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION warehouse_pick(UUID, INT) IS
  'Atomic FIFO pick: dequeues up to N leads from the ready_to_send queue and transitions them to picked.';

-- ------------------------------------------------------------
-- 7) warehouse_unstick_picked — recover stalled picks
-- ------------------------------------------------------------
-- Defensive: if a lead has been in `picked` for more than 6 hours
-- without progressing, the asset-render or send job crashed.
-- Reset it back to `ready_to_send` so the next run picks it again.
CREATE OR REPLACE FUNCTION warehouse_unstick_picked(
  p_max_age_hours INT DEFAULT 6
) RETURNS INT AS $$
DECLARE
  v_count INT;
BEGIN
  WITH stuck AS (
    UPDATE leads
       SET pipeline_status           = 'ready_to_send',
           picked_at                 = NULL,
           last_status_transition_at = now()
     WHERE pipeline_status = 'picked'
       AND picked_at < now() - (p_max_age_hours || ' hours')::INTERVAL
    RETURNING id
  )
  SELECT COUNT(*) INTO v_count FROM stuck;
  RETURN v_count;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION warehouse_unstick_picked(INT) IS
  'Reset leads stuck in picked beyond the timeout, returning the count of recovered leads.';

COMMIT;
