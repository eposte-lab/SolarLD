-- ============================================================
-- 0011 — Row Level Security (RLS) policies
-- ============================================================
-- Multi-tenant isolation: each record filtered by tenant_id via JWT.
-- Service role bypasses RLS for backend workers.

-- Helper: extract tenant_id from JWT via tenant_members lookup.
-- The JWT of a Supabase user holds `sub` = user_id; we look up
-- which tenant they belong to.
CREATE OR REPLACE FUNCTION auth_tenant_id()
RETURNS UUID
LANGUAGE sql STABLE
AS $$
  SELECT tenant_id
  FROM tenant_members
  WHERE user_id = auth.uid()
  LIMIT 1;
$$;

-- ============================================================
-- Enable RLS on all tenant-scoped tables
-- ============================================================

ALTER TABLE tenants              ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_members       ENABLE ROW LEVEL SECURITY;
ALTER TABLE territories          ENABLE ROW LEVEL SECURITY;
ALTER TABLE roofs                ENABLE ROW LEVEL SECURITY;
ALTER TABLE subjects             ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads                ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaigns            ENABLE ROW LEVEL SECURITY;
ALTER TABLE events               ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_warmup_status  ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_usage_log        ENABLE ROW LEVEL SECURITY;

-- Global / shared reference data (intentionally public-read)
ALTER TABLE ateco_consumption_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE regional_incentives        ENABLE ROW LEVEL SECURITY;
ALTER TABLE scoring_weights            ENABLE ROW LEVEL SECURITY;

-- global_blacklist is intentionally global; enable RLS but with open read policy
ALTER TABLE global_blacklist ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- Policies — tenants
-- ============================================================
CREATE POLICY tenants_select ON tenants
  FOR SELECT USING (id = auth_tenant_id());

CREATE POLICY tenants_update ON tenants
  FOR UPDATE USING (id = auth_tenant_id());

-- ============================================================
-- Policies — tenant_members
-- ============================================================
CREATE POLICY members_select ON tenant_members
  FOR SELECT USING (tenant_id = auth_tenant_id());

-- ============================================================
-- Policies — territories
-- ============================================================
CREATE POLICY territories_all ON territories
  FOR ALL
  USING (tenant_id = auth_tenant_id())
  WITH CHECK (tenant_id = auth_tenant_id());

-- ============================================================
-- Policies — roofs
-- ============================================================
CREATE POLICY roofs_all ON roofs
  FOR ALL
  USING (tenant_id = auth_tenant_id())
  WITH CHECK (tenant_id = auth_tenant_id());

-- ============================================================
-- Policies — subjects
-- ============================================================
CREATE POLICY subjects_all ON subjects
  FOR ALL
  USING (tenant_id = auth_tenant_id())
  WITH CHECK (tenant_id = auth_tenant_id());

-- ============================================================
-- Policies — leads
-- ============================================================
CREATE POLICY leads_all ON leads
  FOR ALL
  USING (tenant_id = auth_tenant_id())
  WITH CHECK (tenant_id = auth_tenant_id());

-- ============================================================
-- Policies — campaigns
-- ============================================================
CREATE POLICY campaigns_all ON campaigns
  FOR ALL
  USING (tenant_id = auth_tenant_id())
  WITH CHECK (tenant_id = auth_tenant_id());

-- ============================================================
-- Policies — events (read-only to tenants)
-- ============================================================
CREATE POLICY events_select ON events
  FOR SELECT USING (tenant_id = auth_tenant_id() OR tenant_id IS NULL);

-- ============================================================
-- Policies — email_warmup_status
-- ============================================================
CREATE POLICY warmup_all ON email_warmup_status
  FOR ALL
  USING (tenant_id = auth_tenant_id())
  WITH CHECK (tenant_id = auth_tenant_id());

-- ============================================================
-- Policies — api_usage_log (read-only to tenants)
-- ============================================================
CREATE POLICY api_usage_select ON api_usage_log
  FOR SELECT USING (tenant_id = auth_tenant_id());

-- ============================================================
-- Policies — shared reference tables (read-only global)
-- ============================================================
CREATE POLICY ateco_read_all ON ateco_consumption_profiles
  FOR SELECT USING (true);

CREATE POLICY incentives_read_all ON regional_incentives
  FOR SELECT USING (true);

CREATE POLICY scoring_weights_read_all ON scoring_weights
  FOR SELECT USING (true);

-- ============================================================
-- Policies — global_blacklist (read-only global)
-- ============================================================
CREATE POLICY blacklist_read_all ON global_blacklist
  FOR SELECT USING (true);
