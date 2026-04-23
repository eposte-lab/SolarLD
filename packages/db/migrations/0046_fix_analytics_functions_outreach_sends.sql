-- ============================================================
-- 0046 — Fix analytics functions after campaigns → outreach_sends rename
-- ============================================================
--
-- Migration 0043 renamed `campaigns` to `outreach_sends`.
-- The analytics SQL functions in 0016 still reference the old table name
-- (Postgres doesn't automatically update function bodies on table rename).
-- This migration recreates those functions against `outreach_sends`.

BEGIN;

CREATE OR REPLACE FUNCTION analytics_usage_mtd(
  p_tenant_id UUID
)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  WITH
    roofs_mtd AS (
      SELECT COUNT(*)::bigint AS n
      FROM roofs
      WHERE tenant_id = p_tenant_id
        AND created_at >= date_trunc('month', now())
    ),
    leads_mtd AS (
      SELECT COUNT(*)::bigint AS n
      FROM leads
      WHERE tenant_id = p_tenant_id
        AND created_at >= date_trunc('month', now())
    ),
    emails_mtd AS (
      SELECT COUNT(*)::bigint AS n
      FROM outreach_sends
      WHERE tenant_id = p_tenant_id
        AND channel = 'email'
        AND status = 'sent'
        AND sent_at >= date_trunc('month', now())
    ),
    postcards_mtd AS (
      SELECT COUNT(*)::bigint AS n
      FROM outreach_sends
      WHERE tenant_id = p_tenant_id
        AND channel = 'postal'
        AND status = 'sent'
        AND sent_at >= date_trunc('month', now())
    ),
    cost_mtd AS (
      SELECT COALESCE(SUM(cost_cents), 0)::bigint AS cents
      FROM api_usage_log
      WHERE tenant_id = p_tenant_id
        AND occurred_at >= date_trunc('month', now())
    )
  SELECT jsonb_build_object(
    'roofs_scanned_mtd',  (SELECT n FROM roofs_mtd),
    'leads_generated_mtd',(SELECT n FROM leads_mtd),
    'emails_sent_mtd',    (SELECT n FROM emails_mtd),
    'postcards_sent_mtd', (SELECT n FROM postcards_mtd),
    'total_cost_eur',     ROUND((SELECT cents FROM cost_mtd)::numeric / 100, 2)
  );
$$;

REVOKE ALL ON FUNCTION analytics_usage_mtd(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION analytics_usage_mtd(UUID)
  TO authenticated, service_role;

COMMIT;
