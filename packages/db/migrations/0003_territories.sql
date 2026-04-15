-- ============================================================
-- 0003 — territories
-- ============================================================
-- Territorial coverage per tenant (exclusive geographic lock).

CREATE TABLE IF NOT EXISTS territories (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

  type        territory_type NOT NULL,
  code        TEXT NOT NULL,  -- es. '80100' (CAP), 'NA' (prov), 'Napoli' (comune)
  name        TEXT NOT NULL,

  bbox        JSONB,          -- {ne:{lat,lng},sw:{lat,lng}}
  excluded    BOOLEAN NOT NULL DEFAULT false,
  priority    SMALLINT NOT NULL DEFAULT 5 CHECK (priority BETWEEN 1 AND 10),

  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (tenant_id, type, code)
);

CREATE INDEX idx_territories_tenant ON territories(tenant_id);
CREATE INDEX idx_territories_type_code ON territories(type, code);

CREATE TRIGGER trg_territories_updated_at
  BEFORE UPDATE ON territories
  FOR EACH ROW
  EXECUTE FUNCTION set_updated_at();
