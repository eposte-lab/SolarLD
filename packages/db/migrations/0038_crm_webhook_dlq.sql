-- Migration 0038: CRM webhook dead-letter queue
--
-- When all tenacity retries on a CRM webhook delivery are exhausted the
-- service writes a row here so operators can inspect the failure and
-- trigger a manual replay later.  Rows are never automatically deleted;
-- operators prune them after successful replay or explicit discard.
--
-- Relationship:
--   crm_webhook_dlq.subscription_id → crm_webhook_subscriptions.id
--   ON DELETE CASCADE keeps the DLQ tidy when a subscription is removed.

CREATE TABLE IF NOT EXISTS crm_webhook_dlq (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    subscription_id     uuid        NOT NULL
                            REFERENCES crm_webhook_subscriptions(id)
                            ON DELETE CASCADE,
    tenant_id           uuid        NOT NULL,
    event_type          text        NOT NULL,
    -- Full canonical payload envelope (JSON) — identical to what was
    -- attempted so replay can re-sign + re-POST without reconstruction.
    payload             jsonb       NOT NULL,
    -- Last transport or HTTP error string (up to 2000 chars).
    error               text,
    failed_at           timestamptz NOT NULL DEFAULT now(),
    -- Populated when an operator triggers replay.
    replayed_at         timestamptz,
    replay_status       text,           -- 'ok', 'failed', null=not replayed
    created_at          timestamptz NOT NULL DEFAULT now()
);

-- Support efficient per-tenant DLQ views in the dashboard.
CREATE INDEX IF NOT EXISTS crm_webhook_dlq_tenant_idx
    ON crm_webhook_dlq (tenant_id, failed_at DESC);

-- Support cascading joins from subscription drilldown.
CREATE INDEX IF NOT EXISTS crm_webhook_dlq_subscription_idx
    ON crm_webhook_dlq (subscription_id, failed_at DESC);

-- RLS: tenants see only their own DLQ rows.
ALTER TABLE crm_webhook_dlq ENABLE ROW LEVEL SECURITY;

CREATE POLICY crm_webhook_dlq_tenant_isolation
    ON crm_webhook_dlq
    FOR ALL
    USING (tenant_id = (
        SELECT tenant_id
          FROM tenant_members
         WHERE user_id = auth.uid()
         LIMIT 1
    ));
