-- ============================================================
-- 0004 — roofs
-- ============================================================
-- Discovered buildings with photovoltaic potential.

CREATE TABLE IF NOT EXISTS roofs (
  id                     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id              UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  territory_id           UUID REFERENCES territories(id) ON DELETE SET NULL,

  -- Geo
  lat                    DOUBLE PRECISION NOT NULL,
  lng                    DOUBLE PRECISION NOT NULL,
  geohash                TEXT NOT NULL,
  address                TEXT,
  cap                    TEXT,
  comune                 TEXT,
  provincia              TEXT,

  -- Technical
  area_sqm               NUMERIC(10, 2),
  estimated_kwp          NUMERIC(10, 2),
  estimated_yearly_kwh   NUMERIC(12, 2),
  exposure               TEXT,                -- N/NE/E/SE/S/SW/W/NW
  pitch_degrees          NUMERIC(5, 2),
  shading_score          NUMERIC(3, 2) CHECK (shading_score BETWEEN 0 AND 1),
  has_existing_pv        BOOLEAN NOT NULL DEFAULT false,

  -- Source
  data_source            roof_data_source NOT NULL,
  classification         subject_type NOT NULL DEFAULT 'unknown',

  -- Pipeline
  status                 roof_status NOT NULL DEFAULT 'discovered',

  -- Economics
  scan_cost_cents        INTEGER NOT NULL DEFAULT 0,

  -- Raw API payload (auditable)
  raw_data               JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (tenant_id, geohash)
);

CREATE INDEX idx_roofs_tenant_status ON roofs(tenant_id, status);
CREATE INDEX idx_roofs_geohash ON roofs(geohash);
CREATE INDEX idx_roofs_tenant_geohash ON roofs(tenant_id, geohash);
CREATE INDEX idx_roofs_classification ON roofs(classification);
CREATE INDEX idx_roofs_territory ON roofs(territory_id);

CREATE TRIGGER trg_roofs_updated_at
  BEFORE UPDATE ON roofs
  FOR EACH ROW
  EXECUTE FUNCTION set_updated_at();
