-- ============================================================
-- 0017 — Outbound CRM webhooks + notifications
-- ============================================================
-- Two concerns bundled in one migration because they share a
-- dispatch pattern (deterministic payload + retry metadata) and
-- because the dashboard wires them up together.
--
--   1. crm_webhook_subscriptions — per-tenant CRM endpoints.
--   2. crm_webhook_deliveries    — per-delivery audit trail.
--   3. notifications             — in-app bell items.
--
-- All tables are tenant-scoped with RLS "same tenant" policies.
-- ============================================================

-- ------------------------------------------------------------
-- 1) CRM webhook subscriptions — where to POST events
-- ------------------------------------------------------------
-- Each tenant can register one or more endpoints (Salesforce Flow,
-- HubSpot workflow, Zapier catch hook, custom n8n, ...). We sign
-- every outbound request with `secret` via HMAC-SHA256 so the
-- receiver can verify authenticity.
CREATE TABLE IF NOT EXISTS crm_webhook_subscriptions (
  id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  label          TEXT NOT NULL,              -- 'Salesforce Prod', 'HubSpot staging'
  url            TEXT NOT NULL,
  secret         TEXT NOT NULL,              -- signing key for HMAC-SHA256
  events         TEXT[] NOT NULL DEFAULT ARRAY[
                     'lead.created',
                     'lead.scored',
                     'lead.outreach_sent',
                     'lead.engaged',
                     'lead.contract_signed'
                   ],
  active         BOOLEAN NOT NULL DEFAULT true,
  last_status    TEXT,                       -- last HTTP status or 'error:<msg>'
  last_delivered_at TIMESTAMPTZ,
  failure_count  INTEGER NOT NULL DEFAULT 0,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_crm_sub_tenant_active
  ON crm_webhook_subscriptions(tenant_id, active);

CREATE TRIGGER trg_crm_sub_updated_at
  BEFORE UPDATE ON crm_webhook_subscriptions
  FOR EACH ROW
  EXECUTE FUNCTION set_updated_at();

ALTER TABLE crm_webhook_subscriptions ENABLE ROW LEVEL SECURITY;

CREATE POLICY crm_sub_select ON crm_webhook_subscriptions
  FOR SELECT USING (tenant_id = auth_tenant_id());
CREATE POLICY crm_sub_insert ON crm_webhook_subscriptions
  FOR INSERT WITH CHECK (tenant_id = auth_tenant_id());
CREATE POLICY crm_sub_update ON crm_webhook_subscriptions
  FOR UPDATE USING (tenant_id = auth_tenant_id());
CREATE POLICY crm_sub_delete ON crm_webhook_subscriptions
  FOR DELETE USING (tenant_id = auth_tenant_id());


-- ------------------------------------------------------------
-- 2) CRM webhook delivery log — one row per attempt
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS crm_webhook_deliveries (
  id              BIGSERIAL PRIMARY KEY,
  subscription_id UUID NOT NULL REFERENCES crm_webhook_subscriptions(id) ON DELETE CASCADE,
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  event_type      TEXT NOT NULL,
  payload         JSONB NOT NULL,
  attempt         INTEGER NOT NULL DEFAULT 1,
  status_code     INTEGER,                   -- HTTP status from receiver
  response_body   TEXT,                      -- first 2kb for debugging
  error           TEXT,                      -- set if the dispatch raised
  occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_crm_deliveries_tenant_occurred
  ON crm_webhook_deliveries(tenant_id, occurred_at DESC);
CREATE INDEX idx_crm_deliveries_sub
  ON crm_webhook_deliveries(subscription_id, occurred_at DESC);

ALTER TABLE crm_webhook_deliveries ENABLE ROW LEVEL SECURITY;

CREATE POLICY crm_del_select ON crm_webhook_deliveries
  FOR SELECT USING (tenant_id = auth_tenant_id());


-- ------------------------------------------------------------
-- 3) In-app notifications — bell icon + unread counter
-- ------------------------------------------------------------
-- Rendered by the dashboard topbar. Agents insert rows on
-- interesting events (lead.contract_signed, outreach.bounced,
-- billing.overdue, ...). Supabase Realtime broadcasts INSERTs to
-- authenticated clients that subscribe to their tenant channel.
CREATE TABLE IF NOT EXISTS notifications (
  id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id  UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  user_id    UUID REFERENCES auth.users(id) ON DELETE CASCADE,  -- NULL = all members
  severity   TEXT NOT NULL DEFAULT 'info',    -- info | success | warning | error
  title      TEXT NOT NULL,
  body       TEXT,
  href       TEXT,                            -- deep link inside the dashboard
  metadata   JSONB NOT NULL DEFAULT '{}'::jsonb,
  read_at    TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_notif_tenant_unread
  ON notifications(tenant_id, created_at DESC)
  WHERE read_at IS NULL;

CREATE INDEX idx_notif_user_unread
  ON notifications(user_id, created_at DESC)
  WHERE read_at IS NULL;

ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;

-- A notification is visible to a member if:
--   - it targets them specifically (user_id = auth.uid()), OR
--   - it's a tenant-wide broadcast (user_id IS NULL) and the
--     caller belongs to the tenant.
CREATE POLICY notif_select ON notifications
  FOR SELECT USING (
    tenant_id = auth_tenant_id()
    AND (user_id IS NULL OR user_id = auth.uid())
  );

-- Only the recipient can mark a notification as read.
CREATE POLICY notif_update ON notifications
  FOR UPDATE USING (
    tenant_id = auth_tenant_id()
    AND (user_id IS NULL OR user_id = auth.uid())
  );
