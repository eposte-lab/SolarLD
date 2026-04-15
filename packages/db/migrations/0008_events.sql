-- ============================================================
-- 0008 — events (append-only audit trail, monthly RANGE partition)
-- ============================================================

CREATE TABLE IF NOT EXISTS events (
  id             BIGSERIAL,
  tenant_id      UUID,                                       -- nullable for system-wide events
  lead_id        UUID,                                       -- nullable for tenant-level events

  event_type     TEXT NOT NULL,                              -- ex: roof.scanned, email.opened
  event_source   TEXT NOT NULL,                              -- ex: agent.hunter, webhook.resend

  payload        JSONB NOT NULL DEFAULT '{}'::jsonb,

  occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

  PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);

-- Create initial partitions (current + next 3 months)
-- NB: partition names must be created on-demand by a scheduled job,
-- bootstrap minimal so migrations don't fail.
CREATE TABLE IF NOT EXISTS events_default PARTITION OF events DEFAULT;

CREATE INDEX idx_events_lead         ON events(lead_id)           WHERE lead_id IS NOT NULL;
CREATE INDEX idx_events_tenant_type  ON events(tenant_id, event_type);
CREATE INDEX idx_events_occurred_at  ON events(occurred_at DESC);

-- Helper function to auto-create monthly partitions
CREATE OR REPLACE FUNCTION ensure_events_partition(p_month DATE)
RETURNS VOID AS $$
DECLARE
  start_date DATE := date_trunc('month', p_month)::DATE;
  end_date   DATE := (date_trunc('month', p_month) + INTERVAL '1 month')::DATE;
  part_name  TEXT := 'events_' || to_char(start_date, 'YYYY_MM');
BEGIN
  EXECUTE format(
    'CREATE TABLE IF NOT EXISTS %I PARTITION OF events
     FOR VALUES FROM (%L) TO (%L)',
    part_name, start_date, end_date
  );
END;
$$ LANGUAGE plpgsql;

-- Bootstrap: create current + next month partitions
SELECT ensure_events_partition(now()::DATE);
SELECT ensure_events_partition((now() + INTERVAL '1 month')::DATE);
SELECT ensure_events_partition((now() + INTERVAL '2 month')::DATE);
