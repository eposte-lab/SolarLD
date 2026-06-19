-- 0157 — warehouse_pick: prefer ALREADY-RENDERED leads.
--
-- Why (prod 2026-06-19): the daily send stalled at 34/60 with ~30 rendered
-- ready_to_send leads sitting UNSENT. Root cause: warehouse_pick ordered only
-- by premium-contact then FIFO (0151/0154), so each scheduled pass grabbed a
-- batch that was mostly leads WITHOUT a render. Those can't ship — the outreach
-- render-readiness gate skips them — and #384 now correctly un-picks them back
-- to ready_to_send instead of stranding them in `picked`. Net effect: the pass
-- churns through unrenderable leads and ships only the few rendered ones that
-- happened to fall in its batch, while genuinely-sendable rendered leads never
-- get picked (there is no re-pick until the next scheduled pass). With the
-- Google Solar billing 403 throttling new renders, the rendered pool is finite,
-- so wasting pick slots on unrenderable leads directly caps the day's sends.
--
-- Fix: add a top-priority ORDER BY key so leads that ALREADY have the static
-- send image (`rendering_image_url`, the exact field the outreach gate requires)
-- are picked FIRST. Sendable leads ship before the pass's budget is spent on
-- leads that can't. On a normal day (billing up) the ready_to_send pool is
-- mostly freshly-ready/unrendered leads rendered post-pick, so this key is a
-- near-noop; it only matters when rendered leads accumulate unsent. Unrendered
-- leads still get picked once the rendered ones are exhausted (and are rendered
-- post-pick as before), so nothing is starved — only reordered.
--
-- Columns stay qualified (l.*/upd.*) so we do NOT reintroduce the 42702
-- ambiguity that silently zeroed ALL sends in 0151 (see 0148/0154). Idempotent
-- (CREATE OR REPLACE). Apply to prod via Supabase MCP ahead of merge (see the
-- `migrations-applied-manually` runbook).
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
      (l.rendering_image_url IS NOT NULL) DESC,
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
  'Atomic pick: dequeues up to N ready_to_send leads (already-rendered first, then premium decision-maker contacts, then FIFO) and transitions them to picked. Columns qualified to avoid the 42702 ambiguity (0148 + 0151 + 0154).';
