-- ============================================================
-- 0006 — leads
-- ============================================================
-- Central entity: roof + subject + score + outreach + status.

CREATE TABLE IF NOT EXISTS leads (
  id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id               UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  roof_id                 UUID NOT NULL REFERENCES roofs(id) ON DELETE CASCADE,
  subject_id              UUID NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,

  -- Public URL token (base64, unique)
  public_slug             TEXT NOT NULL UNIQUE,

  -- Scoring
  score                   SMALLINT NOT NULL DEFAULT 0 CHECK (score BETWEEN 0 AND 100),
  score_breakdown         JSONB NOT NULL DEFAULT '{}'::jsonb,
  score_tier              lead_score_tier NOT NULL DEFAULT 'cold',

  -- Assets
  rendering_image_url     TEXT,
  rendering_video_url     TEXT,
  rendering_gif_url       TEXT,
  roi_data                JSONB NOT NULL DEFAULT '{}'::jsonb,

  -- Outreach
  outreach_channel        outreach_channel,
  outreach_sent_at        TIMESTAMPTZ,
  outreach_delivered_at   TIMESTAMPTZ,
  outreach_opened_at      TIMESTAMPTZ,
  outreach_clicked_at     TIMESTAMPTZ,

  -- Engagement
  dashboard_visited_at    TIMESTAMPTZ,
  whatsapp_initiated_at   TIMESTAMPTZ,

  -- Pipeline status
  pipeline_status         lead_status NOT NULL DEFAULT 'new',

  -- Installer feedback
  feedback                installer_feedback,
  feedback_notes          TEXT,
  feedback_at             TIMESTAMPTZ,

  -- Commercial
  contract_value_cents    BIGINT,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (tenant_id, roof_id, subject_id)
);

CREATE INDEX idx_leads_tenant_status ON leads(tenant_id, pipeline_status);
CREATE INDEX idx_leads_public_slug ON leads(public_slug);
CREATE INDEX idx_leads_score ON leads(tenant_id, score DESC);
CREATE INDEX idx_leads_tier ON leads(tenant_id, score_tier);
CREATE INDEX idx_leads_roof ON leads(roof_id);

CREATE TRIGGER trg_leads_updated_at
  BEFORE UPDATE ON leads
  FOR EACH ROW
  EXECUTE FUNCTION set_updated_at();
