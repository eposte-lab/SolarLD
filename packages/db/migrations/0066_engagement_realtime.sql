-- ============================================================
-- 0066 — Real-time engagement bump
-- ============================================================
-- Sprint 8 Fase C.1.
--
-- Today engagement_score is filled only by the nightly rollup
-- (engagement_service.run_engagement_rollup). That means a lead who
-- watches the video at 11:50 AM only shows as "hot" on the operator
-- dashboard the morning after — useless for the "richiama i caldi
-- adesso" promise.
--
-- We add a thin SECURITY DEFINER function ``bump_engagement_score``
-- that the public portal track endpoint calls inline after each
-- accepted batch of events: it adds the requested delta to
-- ``leads.engagement_score`` (clamped 0-100), stamps a recency column
-- ``last_portal_event_at`` (used by the dashboard "Caldi adesso"
-- filter to ignore stale leads), and refreshes
-- ``engagement_score_updated_at``.
--
-- The nightly rollup keeps existing — it remains the ground truth
-- and applies a decay if the lead has gone cold for ≥30 days. The
-- realtime bump is monotonic upward; reconciliation downward stays
-- in the cron.
--
-- SECURITY DEFINER + ``GRANT EXECUTE TO authenticated, anon`` because
-- the public portal calls /v1/public/portal/track without a JWT —
-- our service-role client invokes the RPC, but we keep the explicit
-- grants tight so a future change to the public route can't
-- accidentally elevate beyond what's whitelisted.

BEGIN;

ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS last_portal_event_at TIMESTAMPTZ;

-- Index used by the GET /v1/leads/hot endpoint (Fase C.2). We filter
-- WHERE last_portal_event_at >= now() - INTERVAL 'X hours' so a
-- partial index keyed on the column NOT NULL is the lean shape.
CREATE INDEX IF NOT EXISTS idx_leads_last_portal_event_at
  ON leads (tenant_id, last_portal_event_at DESC NULLS LAST)
  WHERE last_portal_event_at IS NOT NULL;

CREATE OR REPLACE FUNCTION bump_engagement_score(
  p_lead_id UUID,
  p_delta   INTEGER
)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_new_score INTEGER;
BEGIN
  -- Clamp [0, 100]: a delta that pushes negative or above 100 is a
  -- bug somewhere upstream, but we still don't want to corrupt the
  -- score. The nightly rollup will overwrite this value if the
  -- formula produces something different.
  UPDATE leads
     SET engagement_score = LEAST(
           100,
           GREATEST(0, COALESCE(engagement_score, 0) + p_delta)
         ),
         engagement_score_updated_at = now(),
         last_portal_event_at        = now()
   WHERE id = p_lead_id
   RETURNING engagement_score INTO v_new_score;

  RETURN v_new_score;
END;
$$;

COMMENT ON FUNCTION bump_engagement_score(UUID, INTEGER) IS
  'Sprint 8 Fase C.1 — applies a real-time delta to leads.engagement_score, '
  'clamped 0-100, and stamps last_portal_event_at. Called by the public '
  'portal track endpoint after each accepted event batch.';

REVOKE ALL ON FUNCTION bump_engagement_score(UUID, INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bump_engagement_score(UUID, INTEGER)
  TO authenticated, anon, service_role;

COMMIT;
