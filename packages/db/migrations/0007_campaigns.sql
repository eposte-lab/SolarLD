-- ============================================================
-- 0007 — campaigns
-- ============================================================
-- Outreach send records (email steps + postal orders).

CREATE TABLE IF NOT EXISTS campaigns (
  id                        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id                 UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  lead_id                   UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,

  channel                   outreach_channel NOT NULL,
  template_id               TEXT NOT NULL,
  sequence_step             SMALLINT NOT NULL DEFAULT 1,

  -- Email-specific
  email_message_id          TEXT,
  email_subject             TEXT,
  email_html_url            TEXT,

  -- Postal-specific
  postal_provider_order_id  TEXT,
  postal_tracking_number    TEXT,
  postal_pdf_url            TEXT,

  -- Scheduling
  scheduled_for             TIMESTAMPTZ NOT NULL,
  sent_at                   TIMESTAMPTZ,

  -- Cost
  cost_cents                INTEGER NOT NULL DEFAULT 0,

  -- Status
  status                    campaign_status NOT NULL DEFAULT 'pending',
  failure_reason            TEXT,

  created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_campaigns_lead ON campaigns(lead_id);
CREATE INDEX idx_campaigns_scheduled ON campaigns(scheduled_for) WHERE status = 'pending';
CREATE INDEX idx_campaigns_tenant_status ON campaigns(tenant_id, status);
CREATE INDEX idx_campaigns_email_msg ON campaigns(email_message_id) WHERE email_message_id IS NOT NULL;
CREATE INDEX idx_campaigns_postal_order ON campaigns(postal_provider_order_id) WHERE postal_provider_order_id IS NOT NULL;

CREATE TRIGGER trg_campaigns_updated_at
  BEFORE UPDATE ON campaigns
  FOR EACH ROW
  EXECUTE FUNCTION set_updated_at();
