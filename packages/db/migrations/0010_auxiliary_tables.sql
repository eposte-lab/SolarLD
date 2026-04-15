-- ============================================================
-- 0010 — auxiliary tables
-- ============================================================
-- Supporting tables for scoring, incentives, warmup, billing audit.

-- ATECO → average kWh/year per employee (scoring input)
CREATE TABLE IF NOT EXISTS ateco_consumption_profiles (
  ateco_code              TEXT PRIMARY KEY,
  description             TEXT NOT NULL,
  avg_yearly_kwh_per_employee  NUMERIC(10, 2),
  avg_yearly_kwh_per_sqm       NUMERIC(10, 2),
  energy_intensity_tier   TEXT,             -- low | medium | high
  notes                   TEXT,
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_ateco_intensity ON ateco_consumption_profiles(energy_intensity_tier);

-- Regional incentives (scraped weekly from GSE)
CREATE TABLE IF NOT EXISTS regional_incentives (
  id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  region         TEXT NOT NULL,             -- 'Campania', 'Lombardia', ecc.
  name           TEXT NOT NULL,
  description    TEXT,
  amount_type    TEXT,                      -- percentage | flat_eur | kwp_based
  amount_value   NUMERIC(12, 2),
  deadline       DATE,
  target         TEXT,                      -- b2b | b2c | both
  source_url     TEXT,
  active         BOOLEAN NOT NULL DEFAULT true,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_incentives_region_active ON regional_incentives(region, active);
CREATE INDEX idx_incentives_deadline ON regional_incentives(deadline) WHERE active = true;

CREATE TRIGGER trg_incentives_updated_at
  BEFORE UPDATE ON regional_incentives
  FOR EACH ROW
  EXECUTE FUNCTION set_updated_at();

-- Scoring weights (runtime-tunable without redeploy)
CREATE TABLE IF NOT EXISTS scoring_weights (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  version         INTEGER NOT NULL,
  active          BOOLEAN NOT NULL DEFAULT false,
  weights         JSONB NOT NULL,            -- {"technical":25,"consumption":25,...}
  notes           TEXT,
  created_by      UUID REFERENCES auth.users(id),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (version)
);

CREATE UNIQUE INDEX idx_scoring_weights_single_active
  ON scoring_weights(active)
  WHERE active = true;

-- Bootstrap V1 default weights
INSERT INTO scoring_weights (version, active, weights, notes)
VALUES (
  1,
  true,
  '{"technical":25,"consumption":25,"incentives":15,"solvency":20,"distance":15}'::jsonb,
  'V1 default weights from PRD'
)
ON CONFLICT (version) DO NOTHING;

-- Email warmup status per tenant (domain reputation building)
CREATE TABLE IF NOT EXISTS email_warmup_status (
  id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  domain                TEXT NOT NULL,
  provider              TEXT NOT NULL DEFAULT 'mailwarm',
  started_at            TIMESTAMPTZ,
  completed_at          TIMESTAMPTZ,
  daily_limit           INTEGER NOT NULL DEFAULT 50,
  current_sent_today    INTEGER NOT NULL DEFAULT 0,
  bounce_rate_pct       NUMERIC(5, 2) DEFAULT 0,
  complaint_rate_pct    NUMERIC(5, 2) DEFAULT 0,
  paused                BOOLEAN NOT NULL DEFAULT false,
  paused_reason         TEXT,
  last_checked_at       TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, domain)
);

CREATE INDEX idx_warmup_tenant ON email_warmup_status(tenant_id);

CREATE TRIGGER trg_warmup_updated_at
  BEFORE UPDATE ON email_warmup_status
  FOR EACH ROW
  EXECUTE FUNCTION set_updated_at();

-- External API usage log (for cost allocation + budget monitoring)
CREATE TABLE IF NOT EXISTS api_usage_log (
  id            BIGSERIAL PRIMARY KEY,
  tenant_id     UUID REFERENCES tenants(id) ON DELETE SET NULL,
  provider      TEXT NOT NULL,             -- google_solar | visura | atoka | hunter | replicate | ...
  endpoint      TEXT,
  request_count INTEGER NOT NULL DEFAULT 1,
  cost_cents    INTEGER NOT NULL DEFAULT 0,
  status        TEXT,                      -- success | error | rate_limited
  metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
  occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_api_usage_tenant_provider ON api_usage_log(tenant_id, provider);
CREATE INDEX idx_api_usage_occurred ON api_usage_log(occurred_at DESC);
