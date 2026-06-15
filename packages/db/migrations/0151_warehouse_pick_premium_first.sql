-- 0151 — Daily send picks PREMIUM decision-maker leads first.
--
-- The operator wants tomorrow's outreach to prioritise leads whose contact was
-- upgraded to a named decision-maker (subjects.decision_maker_email_source =
-- 'premium_finder') over leads still on a generic role inbox (info@…) — WITHOUT
-- excluding the generics (they still fill the daily cap once the premium supply
-- is exhausted). "Send premium while it lasts, generic as fill."
--
-- warehouse_pick (0072) dequeues ready_to_send leads FIFO. We add a single
-- leading ORDER BY term so premium leads sort first, keeping the existing
-- FIFO order as the tiebreaker. CREATE OR REPLACE with the IDENTICAL signature
-- and RETURNS clause — a pure ordering change, atomicity unchanged.
--
--   * LEFT JOIN subjects so a missing/late subject never drops a lead (falls
--     back to FIFO).
--   * COALESCE(... = 'premium_finder', FALSE) maps NULL/website_scrape → FALSE
--     so only true premium leads jump the queue.
--   * FOR UPDATE OF l — lock only the leads rows (not subjects), preserving the
--     concurrency behaviour of the original.
--
-- Idempotent (CREATE OR REPLACE).

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
    RETURNING id, enqueued_to_warehouse_at, expires_at
  )
  SELECT id, enqueued_to_warehouse_at, expires_at FROM upd;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION warehouse_pick(UUID, INT) IS
  'Atomic pick: dequeues up to N ready_to_send leads (premium decision-maker contacts first, then FIFO) and transitions them to picked.';
