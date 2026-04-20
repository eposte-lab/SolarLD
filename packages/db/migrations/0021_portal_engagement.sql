-- ============================================================
-- 0021 — portal engagement tracking (Part B.1 deep-tracking)
-- ============================================================
--
-- Transforms the lead portal from a static page into a "heat sensor"
-- for sales:
--
--   * `portal_events` — partitioned, append-only, high-cardinality.
--     Stores every interaction the lead has with their personalised
--     page. Partitioned monthly like `events` so a lead-portal that
--     sees hundreds of interactions/day doesn't wreck query planners
--     on historical tables.
--
--   * `leads.{engagement_score, portal_sessions, portal_total_time_sec,
--     deepest_scroll_pct, engagement_score_updated_at}` — denormalised
--     rollup columns, refreshed by the nightly `engagement_rollup_cron`.
--     This keeps the dashboard lead list sortable by heat without a
--     JOIN to `portal_events` on every page load.
--
-- Intentionally separated from the existing `events` table: that one
-- is the audit trail (low volume, one per pipeline transition).
-- `portal_events` is telemetry (high volume, one per scroll/hover).
-- Mixing them would bloat the audit table and blur the semantics.
--
-- RLS policy gives the tenant SELECT on their own rows; writes happen
-- exclusively through the FastAPI beacon using the service role (the
-- beacon is public-faced and resolves `tenant_id` from `public_slug`).

-- ---------------------------------------------------------------
-- 1) portal_events (partitioned by month on occurred_at)
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS portal_events (
  id            BIGSERIAL,
  tenant_id     UUID NOT NULL,
  lead_id       UUID NOT NULL,
  session_id    UUID NOT NULL,

  -- One of:
  --   portal.view              — initial page load
  --   portal.scroll_50         — first time passing 50% of page
  --   portal.scroll_90         — first time passing 90% of page
  --   portal.roi_viewed        — ROI stats card entered viewport
  --   portal.cta_hover         — hovered a CTA button (any)
  --   portal.whatsapp_click    — tapped the WhatsApp CTA
  --   portal.appointment_click — clicked the "request site visit" CTA
  --   portal.video_play        — play() on the hero video
  --   portal.video_complete    — ended event (or >=95% watched)
  --   portal.heartbeat         — 15-second keepalive (time-on-page signal)
  --   portal.leave             — navigator.sendBeacon on unload
  event_kind    TEXT NOT NULL,

  -- Free-form context (scroll %, video position, ...). Small by design.
  metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,

  -- Milliseconds elapsed since the session started (view event).
  -- Nullable only for the inaugural `portal.view` (which defines t=0).
  elapsed_ms    INTEGER,

  occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

  PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);

COMMENT ON TABLE portal_events IS
  'High-cardinality telemetry from the public lead portal. See '
  'migration 0021. Not to be confused with events(): this one stores '
  'scroll / hover / heartbeat; events() stores pipeline transitions.';

-- Safety net for any event that slips outside the pre-created partitions.
CREATE TABLE IF NOT EXISTS portal_events_default
  PARTITION OF portal_events DEFAULT;

-- Hot-path indexes:
--   * lead_id → "show me this lead's recent activity" on the detail page.
--   * (tenant_id, occurred_at DESC) → "hot leads now" dashboard feed.
--   * session_id → rollup groups per-visit for time-on-page calc.
CREATE INDEX IF NOT EXISTS idx_portal_events_lead
  ON portal_events(lead_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_portal_events_tenant_recent
  ON portal_events(tenant_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_portal_events_session
  ON portal_events(session_id);

-- ---------------------------------------------------------------
-- 2) Partition helper (mirrors ensure_events_partition from 0008/0019)
-- ---------------------------------------------------------------

CREATE OR REPLACE FUNCTION ensure_portal_events_partition(p_month DATE)
RETURNS VOID AS $$
DECLARE
  start_date DATE := date_trunc('month', p_month)::DATE;
  end_date   DATE := (date_trunc('month', p_month) + INTERVAL '1 month')::DATE;
  part_name  TEXT := 'portal_events_' || to_char(start_date, 'YYYY_MM');
  already    BOOLEAN;
BEGIN
  EXECUTE format(
    'CREATE TABLE IF NOT EXISTS %I PARTITION OF portal_events
     FOR VALUES FROM (%L) TO (%L)',
    part_name, start_date, end_date
  );

  -- Register the partition with the realtime publication (same
  -- reasoning as migration 0019 for the events table: the parent is
  -- not in the publication with publish_via_partition_root=false, so
  -- each partition must be added individually or INSERTs silently
  -- stop broadcasting on the new month's rollover).
  SELECT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime'
      AND schemaname = 'public'
      AND tablename = part_name
  ) INTO already;

  IF NOT already THEN
    EXECUTE format(
      'ALTER PUBLICATION supabase_realtime ADD TABLE public.%I',
      part_name
    );
  END IF;
END;
$$ LANGUAGE plpgsql;

-- Bootstrap: create current + next 2 months.
SELECT ensure_portal_events_partition(now()::DATE);
SELECT ensure_portal_events_partition((now() + INTERVAL '1 month')::DATE);
SELECT ensure_portal_events_partition((now() + INTERVAL '2 month')::DATE);

-- ---------------------------------------------------------------
-- 3) RLS — tenant SELECT, service-role only writes
-- ---------------------------------------------------------------

ALTER TABLE portal_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS portal_events_tenant_select ON portal_events;
CREATE POLICY portal_events_tenant_select ON portal_events
  FOR SELECT
  USING (tenant_id = auth_tenant_id());

-- No INSERT/UPDATE/DELETE policies → writes restricted to the
-- service-role client used by the FastAPI beacon. This is critical:
-- the beacon runs without a JWT (portal is public) and resolves the
-- tenant from the lead's public_slug server-side. Writes MUST NOT
-- flow through anon/auth roles.

-- ---------------------------------------------------------------
-- 4) leads: denormalised rollup columns
-- ---------------------------------------------------------------

ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS engagement_score            INTEGER  NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS engagement_score_updated_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS portal_sessions             INTEGER  NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS portal_total_time_sec       INTEGER  NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS deepest_scroll_pct          SMALLINT NOT NULL DEFAULT 0;

COMMENT ON COLUMN leads.engagement_score IS
  'Heat score 0-100 computed nightly by engagement_rollup_cron. '
  'Input: last-30-day portal_events + existing email engagement '
  '(outreach_opened_at, outreach_clicked_at). See '
  'apps/api/src/services/engagement_service.py for the formula.';

-- Covering index for the "hot leads" sort — the dashboard lead list
-- defaults to engagement_score DESC for the current tenant.
CREATE INDEX IF NOT EXISTS idx_leads_engagement_hot
  ON leads(tenant_id, engagement_score DESC)
  WHERE engagement_score > 0;
