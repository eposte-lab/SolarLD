-- 0155_total_trade_open_dashboard.sql
-- "Graduate" Total Trade from trial moderation WITHOUT exposing the old
-- un-promoted queue.
--
-- Desired end state (operator decision):
--   * Everything present + future is PUBLIC in the tenant dashboard — no
--     more promotion/approval step.
--   * The leads the operator deliberately left un-promoted (reacted but
--     never released) stay HIDDEN from the tenant and visible only in the
--     super-admin queue.
--
-- Mechanism: keep the ``trial_moderation`` gate ON (it's what still hides
-- the un-released leads), and:
--   1. add a per-tenant ``moderation_auto_release`` flag + a BEFORE INSERT
--      trigger that auto-releases NEW leads for such tenants (so future
--      leads are public without code changes / global default changes);
--   2. release every CURRENT un-released lead EXCEPT the reacted-but-
--      un-promoted ones (the super-admin queue), which stay hidden.
--
-- The appointment endpoint (routes/public.py) now holds only when the lead
-- is BOTH moderated AND not released — so released leads' requests flow
-- straight through.
BEGIN;

-- 1) Auto-release trigger for opt-in tenants -------------------------------
CREATE OR REPLACE FUNCTION auto_release_moderated_lead()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.operator_released_at IS NULL AND EXISTS (
    SELECT 1 FROM tenants t
    WHERE t.id = NEW.tenant_id
      AND (t.settings -> 'feature_flags' ->> 'moderation_auto_release') = 'true'
  ) THEN
    NEW.operator_released_at := now();
    NEW.operator_review_status := 'released';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_auto_release_lead ON leads;
CREATE TRIGGER trg_auto_release_lead
  BEFORE INSERT ON leads
  FOR EACH ROW
  EXECUTE FUNCTION auto_release_moderated_lead();

-- 2) Enable auto-release for Total Trade (keep trial_moderation ON so the
--    9 un-promoted leads below stay gated). --------------------------------
UPDATE tenants
SET settings = jsonb_set(
      COALESCE(settings, '{}'::jsonb),
      '{feature_flags,moderation_auto_release}',
      '"true"'::jsonb,
      true
    )
WHERE id = 'df08df04-4c90-4613-b21e-80879fc958d1';

-- 3) Release every CURRENT un-released Total Trade lead EXCEPT the reacted-
--    but-un-promoted ones (those stay hidden + in the super-admin queue).
--    The "reacted" predicate mirrors the super-admin pending-queue filter
--    (routes/admin.py::trial_pending_leads).
UPDATE leads
SET operator_released_at = now(),
    operator_review_status = 'released'
WHERE tenant_id = 'df08df04-4c90-4613-b21e-80879fc958d1'
  AND operator_released_at IS NULL
  AND NOT (
        outreach_clicked_at   IS NOT NULL
     OR dashboard_visited_at  IS NOT NULL
     OR whatsapp_initiated_at IS NOT NULL
     OR outreach_replied_at   IS NOT NULL
     OR last_portal_event_at  IS NOT NULL
     OR engagement_score > 0
     OR pipeline_status IN ('clicked','engaged','whatsapp','appointment','closed_won','closed_lost')
  );

COMMIT;
