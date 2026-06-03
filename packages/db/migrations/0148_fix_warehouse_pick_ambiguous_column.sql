-- 0148 — Fix warehouse_pick: ambiguous column reference.
--
-- The function's RETURNS TABLE declares out-parameters
-- `enqueued_to_warehouse_at` / `expires_at`; the inner UPDATE ... RETURNING
-- and the final SELECT referenced those same names UNQUALIFIED, so Postgres
-- couldn't tell the out-parameter from the table column:
--
--   ERROR: 42702 column reference "enqueued_to_warehouse_at" is ambiguous
--
-- The RPC therefore raised on EVERY call. Because the daily send pick goes
-- through warehouse_pick, this meant no lead was ever dequeued and no
-- outreach was ever shipped (the cron's per-tenant try/except swallowed the
-- failure). Qualify the references (table name in RETURNING, CTE alias in
-- the final SELECT) to disambiguate. Behaviour is otherwise unchanged.
CREATE OR REPLACE FUNCTION public.warehouse_pick(p_tenant_id uuid, p_count integer)
  RETURNS TABLE(lead_id uuid, enqueued_to_warehouse_at timestamptz, expires_at timestamptz)
  LANGUAGE plpgsql
AS $function$
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
    RETURNING leads.id, leads.enqueued_to_warehouse_at, leads.expires_at
  )
  SELECT upd.id, upd.enqueued_to_warehouse_at, upd.expires_at FROM upd;
END;
$function$;
