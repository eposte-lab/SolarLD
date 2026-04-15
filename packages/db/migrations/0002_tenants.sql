-- ============================================================
-- 0002 — tenants
-- ============================================================
-- Installers (multi-tenant root entity).

CREATE TABLE IF NOT EXISTS tenants (
  id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

  -- Business info
  business_name           TEXT NOT NULL,
  vat_number              TEXT UNIQUE,
  contact_email           TEXT NOT NULL,
  contact_phone           TEXT,
  whatsapp_number         TEXT,

  -- Branding
  brand_logo_url          TEXT,
  brand_primary_color     TEXT DEFAULT '#0F766E',
  email_from_domain       TEXT,
  email_from_name         TEXT,

  -- Commercial tier
  tier                    tenant_tier NOT NULL DEFAULT 'founding',
  monthly_rate_cents      INTEGER NOT NULL DEFAULT 0,
  contract_start_date     DATE,
  contract_end_date       DATE,

  -- Lifecycle
  status                  tenant_status NOT NULL DEFAULT 'onboarding',

  -- Billing
  stripe_customer_id      TEXT,
  stripe_subscription_id  TEXT,

  -- Config
  settings                JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_tenants_status ON tenants(status);
CREATE INDEX idx_tenants_stripe_customer ON tenants(stripe_customer_id);

-- Supabase Auth join table: map auth.users → tenants (for multi-user tenants later)
CREATE TABLE IF NOT EXISTS tenant_members (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  user_id      UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  role         TEXT NOT NULL DEFAULT 'owner', -- owner|admin|member
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, user_id)
);

CREATE INDEX idx_tenant_members_user ON tenant_members(user_id);
CREATE INDEX idx_tenant_members_tenant ON tenant_members(tenant_id);

-- updated_at trigger helper (reused across tables)
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_tenants_updated_at
  BEFORE UPDATE ON tenants
  FOR EACH ROW
  EXECUTE FUNCTION set_updated_at();
