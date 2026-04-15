-- ============================================================
-- 0005 — subjects
-- ============================================================
-- Identified owners of roofs (companies B2B or private citizens B2C).

CREATE TABLE IF NOT EXISTS subjects (
  id                            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id                     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  roof_id                       UUID NOT NULL REFERENCES roofs(id) ON DELETE CASCADE,

  type                          subject_type NOT NULL,

  -- ============ B2B fields ============
  business_name                 TEXT,
  vat_number                    TEXT,
  ateco_code                    TEXT,
  ateco_description             TEXT,
  yearly_revenue_cents          BIGINT,
  employees                     INTEGER,
  decision_maker_name           TEXT,
  decision_maker_role           TEXT,
  decision_maker_email          TEXT,
  decision_maker_email_verified BOOLEAN NOT NULL DEFAULT false,
  linkedin_url                  TEXT,

  -- ============ B2C fields ============
  owner_first_name              TEXT,
  owner_last_name               TEXT,
  postal_address_line1          TEXT,
  postal_address_line2          TEXT,
  postal_cap                    TEXT,
  postal_city                   TEXT,
  postal_province               TEXT,

  -- ============ Audit / compliance ============
  data_sources                  JSONB NOT NULL DEFAULT '[]'::jsonb,
  enrichment_cost_cents         INTEGER NOT NULL DEFAULT 0,
  enrichment_completed_at       TIMESTAMPTZ,

  -- Blacklist matching hash: SHA256 of normalized (business_name|vat_number) or (full_name|full_address)
  pii_hash                      TEXT NOT NULL,

  created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (tenant_id, roof_id)
);

CREATE INDEX idx_subjects_tenant ON subjects(tenant_id);
CREATE INDEX idx_subjects_pii_hash ON subjects(pii_hash);
CREATE INDEX idx_subjects_roof ON subjects(roof_id);
CREATE INDEX idx_subjects_vat ON subjects(vat_number) WHERE vat_number IS NOT NULL;

CREATE TRIGGER trg_subjects_updated_at
  BEFORE UPDATE ON subjects
  FOR EACH ROW
  EXECUTE FUNCTION set_updated_at();
