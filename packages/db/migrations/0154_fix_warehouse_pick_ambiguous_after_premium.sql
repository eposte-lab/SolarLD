-- 0154 — Re-fix warehouse_pick ambiguity reintroduced by 0151.
--
-- 0148 disambiguated the RETURNING / final SELECT by qualifying the column
-- references (leads.* / upd.*). 0151 then CREATE OR REPLACE'd the function to
-- add premium-first ordering but copied an UNQUALIFIED body, regressing the
-- 0148 fix:
--
--   ERROR 42702: column reference "enqueued_to_warehouse_at" is ambiguous
--
-- The RETURNS TABLE out-parameter `enqueued_to_warehouse_at` collides with the
-- `leads` column of the same name in the inner UPDATE ... RETURNING and the
-- final SELECT. Postgres raised on EVERY call, so warehouse_pick never dequeued
-- a lead and NO outreach shipped — both the daily cron and the manual
-- "Avvia invii ora" button silently picked 0 (the API caught the RPC error and
-- logged it). Observed in prod 2026-06-16/17: 150+ ready_to_send, 0 ever moved
-- to `picked`.
--
-- Combine BOTH fixes: 0151's premium-first ORDER BY (LEFT JOIN subjects, so a
-- missing subject never drops a lead) AND 0148's qualified column references.
-- Idempotent (CREATE OR REPLACE); behaviour otherwise unchanged.
--
-- Applied to prod via Supabase MCP on 2026-06-17 ahead of this merge (see
-- the `migrations-applied-manually` runbook).
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
    LEFT JOIN subjects s ON s.id = l.subject_id
    WHERE l.tenant_id        = p_tenant_id
      AND l.pipeline_status  = 'ready_to_send'
      AND (l.expires_at IS NULL OR l.expires_at > v_now)
    ORDER BY
      COALESCE(s.decision_maker_email_source = 'premium_finder', FALSE) DESC,
      l.enqueued_to_warehouse_at NULLS LAST,
      l.created_at
    LIMIT p_count
    FOR UPDATE OF l SKIP LOCKED
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

COMMENT ON FUNCTION public.warehouse_pick(uuid, integer) IS
  'Atomic pick: dequeues up to N ready_to_send leads (premium decision-maker contacts first, then FIFO) and transitions them to picked. Columns qualified to avoid the 42702 ambiguity (0148 + 0151).';
