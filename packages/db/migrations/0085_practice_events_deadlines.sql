-- GSE Practices Module — Livello 2 Sprint 1: event sourcing + deadlines.
--
-- Layered on top of 0083_practices.sql / 0084_practice_extras.sql.
-- Adds two tables and zero columns to existing tables, so it's safe
-- to apply alongside live traffic on Livello 1.
--
--   1. `practice_events` — append-only audit log of everything that
--      happens to a practice or one of its documents (created, rendered,
--      sent, accepted, rejected, deadline_breached, deadline_satisfied,
--      data_collected, …).  Source of truth for the dashboard's
--      timeline panel and the input to the daily deadline cron.
--      Append-only → no UPDATE/DELETE policy; we only ever insert.
--
--   2. `practice_deadlines` — projection on top of practice_events:
--      one row per (practice, deadline_kind).  Created when a triggering
--      event fires (e.g. comunicazione_comune.sent → +30 days deadline
--      for "comune_acceptance"), satisfied when a closing event fires
--      (comunicazione_comune.accepted), cancelled when the practice is
--      cancelled.  Read by the daily cron to surface alerts.
--
-- Notifications themselves reuse the existing `notifications` table
-- (defined in 0017_crm_webhooks.sql) with metadata pointers
--   { practice_id, deadline_id, practice_number, deadline_kind }.
-- That avoids a parallel notifications subsystem and lets the existing
-- bell/inbox UI surface practice alerts for free.

-- ---------------------------------------------------------------------
-- practice_events — append-only event log
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS practice_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  practice_id uuid NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
  -- Optional pointer to a specific document (NULL when the event is
  -- about the practice as a whole, e.g. 'practice_created').
  document_id uuid REFERENCES practice_documents(id) ON DELETE SET NULL,
  -- Free-form event type.  We don't constrain via CHECK because we
  -- expect to add new types every sprint and an enum migration is
  -- friction we don't need.  Common values:
  --   practice_created, practice_status_changed, practice_cancelled
  --   document_generated, document_regenerated
  --   document_reviewed, document_sent, document_accepted, document_rejected
  --   deadline_created, deadline_satisfied, deadline_breached, deadline_cancelled
  --   data_collected (Sprint 3+: missing-data fill-in)
  event_type text NOT NULL,
  -- Arbitrary payload.  Keep it small (< 4 KB) — large blobs go to
  -- storage, not here.  Example for document_sent: {"channel": "pec",
  -- "message_id": "abc@example.it"}.  Example for deadline_breached:
  -- {"deadline_id": "...", "days_overdue": 5}.
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- Best-effort actor.  Filled when the event is triggered by a user
  -- action (form submit, status change in dashboard).  NULL for
  -- system-generated events (cron, worker, webhook).
  actor_user_id uuid,
  -- When did the underlying business event happen?  May differ from
  -- created_at if we backfill historical events.
  occurred_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_practice_events_practice_occurred
  ON practice_events (practice_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_practice_events_tenant_occurred
  ON practice_events (tenant_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_practice_events_type
  ON practice_events (tenant_id, event_type, occurred_at DESC);

COMMENT ON TABLE practice_events IS
  'Append-only event log for GSE practices and their documents. Powers the dashboard timeline and the deadline cron.';

COMMENT ON COLUMN practice_events.event_type IS
  'Free-form event type (practice_created | document_sent | deadline_breached | …). Documented in services/practice_events_service.py.';


-- ---------------------------------------------------------------------
-- practice_deadlines — open deadlines by kind, per practice
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS practice_deadlines (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  practice_id uuid NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
  -- Optional document this deadline is attached to.  NULL when the
  -- deadline is about the practice as a whole (e.g. transizione_50
  -- ex-post window, regardless of which sub-doc).
  document_id uuid REFERENCES practice_documents(id) ON DELETE SET NULL,
  -- Stable identifier for the deadline kind.  Used to look up the SLA
  -- in services/practice_deadlines_service.DEADLINE_RULES.  Common:
  --   tica_response_60d  (60 calendar days after TICA submission)
  --   comune_acceptance_30d (30 days after comunicazione_comune sent)
  --   modello_unico_p2_due (after data_fine_lavori)
  --   transizione_50_ex_post_60d (60 days after entrata in esercizio)
  deadline_kind text NOT NULL,
  -- Calendar due date.  Computed at creation time from a triggering
  -- event + a fixed offset (see DEADLINE_RULES).  We store the absolute
  -- timestamp so the cron can do a simple `WHERE due_at < now()` scan
  -- without re-applying business-day math.
  due_at timestamptz NOT NULL,
  status text NOT NULL DEFAULT 'open' CHECK (
    status IN (
      'open',         -- waiting on the closing event
      'satisfied',    -- closing event fired before/at due_at
      'overdue',      -- still open after due_at (set by cron)
      'cancelled'     -- practice cancelled or rule no longer applies
    )
  ),
  -- When the closing event fired (or NULL if still open/overdue).
  satisfied_at timestamptz,
  -- The practice_event that satisfied this deadline (audit trail).
  satisfied_by_event_id uuid REFERENCES practice_events(id) ON DELETE SET NULL,
  -- The triggering event (so we can render "30 days from when X
  -- happened" in the UI without re-deriving).
  triggered_by_event_id uuid REFERENCES practice_events(id) ON DELETE SET NULL,
  -- Free-form metadata: the SLA copy ("Risposta TICA entro 60 gg
  -- ARERA 109/2021"), attachments URLs, …
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  -- One open deadline per (practice, kind).  Re-triggering the same
  -- kind (e.g. resending after a rejection) UPSERTs the row rather
  -- than creating a duplicate.
  UNIQUE (practice_id, deadline_kind)
);

CREATE INDEX IF NOT EXISTS idx_practice_deadlines_due_open
  ON practice_deadlines (due_at)
  WHERE status = 'open';

CREATE INDEX IF NOT EXISTS idx_practice_deadlines_tenant_status
  ON practice_deadlines (tenant_id, status, due_at);

CREATE INDEX IF NOT EXISTS idx_practice_deadlines_practice
  ON practice_deadlines (practice_id, status);

CREATE OR REPLACE FUNCTION practice_deadlines_touch_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS practice_deadlines_touch_updated_at_trg
  ON practice_deadlines;
CREATE TRIGGER practice_deadlines_touch_updated_at_trg
  BEFORE UPDATE ON practice_deadlines
  FOR EACH ROW EXECUTE FUNCTION practice_deadlines_touch_updated_at();

COMMENT ON TABLE practice_deadlines IS
  'Open/closed deadlines computed from practice_events. One row per (practice, deadline_kind). Read by the daily cron and the dashboard scadenze panel.';


-- ---------------------------------------------------------------------
-- RLS — same pattern as practices/practice_documents
-- ---------------------------------------------------------------------

ALTER TABLE practice_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY practice_events_tenant_select
  ON practice_events FOR SELECT
  USING (
    tenant_id IN (
      SELECT tenant_id FROM tenant_members WHERE user_id = auth.uid()
    )
  );

ALTER TABLE practice_deadlines ENABLE ROW LEVEL SECURITY;

CREATE POLICY practice_deadlines_tenant_select
  ON practice_deadlines FOR SELECT
  USING (
    tenant_id IN (
      SELECT tenant_id FROM tenant_members WHERE user_id = auth.uid()
    )
  );
