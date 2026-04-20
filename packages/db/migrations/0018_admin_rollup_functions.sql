-- ============================================================
-- 0018 — Cross-tenant rollup helpers for the super-admin console
-- ============================================================
-- SECURITY DEFINER so the service role key is enough; we gate the
-- REST surface in FastAPI with the `ctx.role == 'super_admin'`
-- check. Not granted to `authenticated` on purpose — only the
-- super-admin UI should reach these.

CREATE OR REPLACE FUNCTION admin_tenant_overview()
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT COALESCE(
    jsonb_agg(
      jsonb_build_object(
        'tenant_id',       t.id,
        'business_name',   t.business_name,
        'status',          t.status,
        'tier',            t.tier,
        'created_at',      t.created_at,
        'leads_total',     COALESCE(lead_agg.n, 0),
        'leads_mtd',       COALESCE(lead_agg.n_mtd, 0),
        'cost_mtd_cents',  COALESCE(cost_agg.cents, 0),
        'members',         COALESCE(member_agg.n, 0)
      )
      ORDER BY t.created_at DESC
    ),
    '[]'::jsonb
  )
  FROM tenants t
  LEFT JOIN LATERAL (
    SELECT
      COUNT(*)::bigint AS n,
      COUNT(*) FILTER (WHERE created_at >= date_trunc('month', now()))::bigint AS n_mtd
    FROM leads
    WHERE tenant_id = t.id
  ) AS lead_agg ON TRUE
  LEFT JOIN LATERAL (
    SELECT SUM(cost_cents)::bigint AS cents
    FROM api_usage_log
    WHERE tenant_id = t.id
      AND occurred_at >= date_trunc('month', now())
  ) AS cost_agg ON TRUE
  LEFT JOIN LATERAL (
    SELECT COUNT(*)::bigint AS n
    FROM tenant_members
    WHERE tenant_id = t.id
  ) AS member_agg ON TRUE;
$$;

REVOKE ALL ON FUNCTION admin_tenant_overview() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin_tenant_overview() TO service_role;


CREATE OR REPLACE FUNCTION admin_platform_cost(p_days INTEGER DEFAULT 30)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  WITH by_tenant AS (
    SELECT
      t.id            AS tenant_id,
      t.business_name,
      COALESCE(SUM(u.cost_cents), 0)::bigint AS cost_cents,
      COALESCE(SUM(u.request_count), 0)::bigint AS calls
    FROM tenants t
    LEFT JOIN api_usage_log u
      ON u.tenant_id = t.id
     AND u.occurred_at >= now() - make_interval(days => p_days)
    GROUP BY t.id, t.business_name
  ),
  by_provider AS (
    SELECT
      provider,
      SUM(cost_cents)::bigint AS cost_cents,
      SUM(request_count)::bigint AS calls,
      COUNT(*) FILTER (WHERE status = 'error')::bigint AS errors
    FROM api_usage_log
    WHERE occurred_at >= now() - make_interval(days => p_days)
    GROUP BY provider
  )
  SELECT jsonb_build_object(
    'window_days', p_days,
    'by_tenant',   COALESCE((
      SELECT jsonb_agg(row_to_json(b)::jsonb ORDER BY cost_cents DESC)
      FROM by_tenant b
      WHERE cost_cents > 0
    ), '[]'::jsonb),
    'by_provider', COALESCE((
      SELECT jsonb_agg(row_to_json(p)::jsonb ORDER BY cost_cents DESC)
      FROM by_provider p
    ), '[]'::jsonb),
    'total_cost_cents', (
      SELECT COALESCE(SUM(cost_cents), 0)::bigint FROM by_tenant
    )
  );
$$;

REVOKE ALL ON FUNCTION admin_platform_cost(INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin_platform_cost(INTEGER) TO service_role;
