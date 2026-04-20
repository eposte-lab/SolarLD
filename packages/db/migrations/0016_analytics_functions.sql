-- ============================================================
-- 0016 — Analytics aggregation functions
-- ============================================================
-- SQL-side rollups so the API doesn't have to pull raw rows and
-- aggregate in Python. All functions are SECURITY DEFINER (run as
-- the owner, bypassing RLS) and take an explicit p_tenant_id — the
-- caller (FastAPI) is responsible for enforcing tenant scoping.
--
-- We expose them as JSON-returning functions to keep the Supabase
-- client boundary trivial: one RPC call → one JSONB payload.
-- ============================================================

-- ------------------------------------------------------------
-- analytics_funnel — counts at each pipeline stage for a tenant
-- ------------------------------------------------------------
-- Returns: {"leads_total": N, "sent": N, "delivered": N, "opened": N,
--           "clicked": N, "engaged": N, "contract_signed": N,
--           "hot": N, "warm": N, "cold": N, "rejected": N}
CREATE OR REPLACE FUNCTION analytics_funnel(
  p_tenant_id UUID,
  p_from      TIMESTAMPTZ DEFAULT (now() - interval '30 days'),
  p_to        TIMESTAMPTZ DEFAULT now()
)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT jsonb_build_object(
    'leads_total',      COUNT(*),
    'sent',             COUNT(*) FILTER (WHERE outreach_sent_at IS NOT NULL),
    'delivered',        COUNT(*) FILTER (WHERE outreach_delivered_at IS NOT NULL),
    'opened',           COUNT(*) FILTER (WHERE outreach_opened_at IS NOT NULL),
    'clicked',          COUNT(*) FILTER (WHERE outreach_clicked_at IS NOT NULL),
    'engaged',          COUNT(*) FILTER (WHERE dashboard_visited_at IS NOT NULL
                                         OR whatsapp_initiated_at IS NOT NULL),
    'contract_signed',  COUNT(*) FILTER (WHERE feedback = 'contract_signed'),
    'hot',              COUNT(*) FILTER (WHERE score_tier = 'hot'),
    'warm',             COUNT(*) FILTER (WHERE score_tier = 'warm'),
    'cold',             COUNT(*) FILTER (WHERE score_tier = 'cold'),
    'rejected',         COUNT(*) FILTER (WHERE score_tier = 'rejected')
  )
  FROM leads
  WHERE tenant_id = p_tenant_id
    AND created_at >= p_from
    AND created_at <  p_to;
$$;

REVOKE ALL ON FUNCTION analytics_funnel(UUID, TIMESTAMPTZ, TIMESTAMPTZ) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION analytics_funnel(UUID, TIMESTAMPTZ, TIMESTAMPTZ)
  TO authenticated, service_role;


-- ------------------------------------------------------------
-- analytics_spend_by_provider — MTD cost rollup per provider
-- ------------------------------------------------------------
-- Returns a JSON array:
-- [ {"provider": "google_solar", "calls": N, "cost_cents": N, "errors": N}, ... ]
CREATE OR REPLACE FUNCTION analytics_spend_by_provider(
  p_tenant_id UUID,
  p_from      TIMESTAMPTZ DEFAULT date_trunc('month', now()),
  p_to        TIMESTAMPTZ DEFAULT now()
)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT COALESCE(
    jsonb_agg(row_to_json(rows)::jsonb ORDER BY (rows).cost_cents DESC),
    '[]'::jsonb
  )
  FROM (
    SELECT
      provider,
      SUM(request_count)::bigint AS calls,
      SUM(cost_cents)::bigint    AS cost_cents,
      COUNT(*) FILTER (WHERE status = 'error')::bigint AS errors
    FROM api_usage_log
    WHERE tenant_id = p_tenant_id
      AND occurred_at >= p_from
      AND occurred_at <  p_to
    GROUP BY provider
  ) AS rows;
$$;

REVOKE ALL ON FUNCTION analytics_spend_by_provider(UUID, TIMESTAMPTZ, TIMESTAMPTZ) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION analytics_spend_by_provider(UUID, TIMESTAMPTZ, TIMESTAMPTZ)
  TO authenticated, service_role;


-- ------------------------------------------------------------
-- analytics_spend_daily — daily spend over a window (for sparkline)
-- ------------------------------------------------------------
-- Returns: [ {"day":"2026-04-01","cost_cents":N,"calls":N}, ...]
CREATE OR REPLACE FUNCTION analytics_spend_daily(
  p_tenant_id UUID,
  p_days      INTEGER DEFAULT 30
)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  WITH series AS (
    SELECT generate_series(
      date_trunc('day', now() - make_interval(days => p_days - 1)),
      date_trunc('day', now()),
      interval '1 day'
    )::date AS day
  ),
  rollup AS (
    SELECT
      date_trunc('day', occurred_at)::date AS day,
      SUM(cost_cents)::bigint     AS cost_cents,
      SUM(request_count)::bigint  AS calls
    FROM api_usage_log
    WHERE tenant_id = p_tenant_id
      AND occurred_at >= now() - make_interval(days => p_days)
    GROUP BY 1
  )
  SELECT COALESCE(
    jsonb_agg(
      jsonb_build_object(
        'day',        to_char(s.day, 'YYYY-MM-DD'),
        'cost_cents', COALESCE(r.cost_cents, 0),
        'calls',      COALESCE(r.calls, 0)
      )
      ORDER BY s.day
    ),
    '[]'::jsonb
  )
  FROM series s
  LEFT JOIN rollup r ON r.day = s.day;
$$;

REVOKE ALL ON FUNCTION analytics_spend_daily(UUID, INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION analytics_spend_daily(UUID, INTEGER)
  TO authenticated, service_role;


-- ------------------------------------------------------------
-- analytics_territory_roi — leads + avg score + signed count per territory
-- ------------------------------------------------------------
-- Returns: [ {"territory_id":..., "territory_name":..., "leads_total":N,
--            "leads_hot":N, "avg_score":N, "signed":N,
--            "contract_value_eur":N}, ... ]
CREATE OR REPLACE FUNCTION analytics_territory_roi(
  p_tenant_id UUID
)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT COALESCE(
    jsonb_agg(
      jsonb_build_object(
        'territory_id',       t.id,
        'territory_name',     t.name,
        'leads_total',        COALESCE(agg.leads_total, 0),
        'leads_hot',          COALESCE(agg.leads_hot, 0),
        'avg_score',          ROUND(COALESCE(agg.avg_score, 0)::numeric, 1),
        'signed',             COALESCE(agg.signed, 0),
        'contract_value_eur', ROUND(COALESCE(agg.contract_value_cents, 0)::numeric / 100, 2)
      )
      ORDER BY COALESCE(agg.leads_total, 0) DESC
    ),
    '[]'::jsonb
  )
  FROM territories t
  LEFT JOIN LATERAL (
    SELECT
      COUNT(l.*)                                       AS leads_total,
      COUNT(l.*) FILTER (WHERE l.score_tier = 'hot')   AS leads_hot,
      AVG(l.score)                                     AS avg_score,
      COUNT(l.*) FILTER (WHERE l.feedback = 'contract_signed') AS signed,
      SUM(l.contract_value_cents) FILTER (WHERE l.feedback = 'contract_signed') AS contract_value_cents
    FROM leads l
    JOIN roofs r ON r.id = l.roof_id
    WHERE l.tenant_id = p_tenant_id
      AND r.territory_id = t.id
  ) agg ON true
  WHERE t.tenant_id = p_tenant_id;
$$;

REVOKE ALL ON FUNCTION analytics_territory_roi(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION analytics_territory_roi(UUID)
  TO authenticated, service_role;


-- ------------------------------------------------------------
-- analytics_usage_mtd — month-to-date operational stats (replaces
-- the stub in GET /v1/tenants/me/usage)
-- ------------------------------------------------------------
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
      FROM campaigns
      WHERE tenant_id = p_tenant_id
        AND channel = 'email'
        AND status = 'sent'
        AND sent_at >= date_trunc('month', now())
    ),
    postcards_mtd AS (
      SELECT COUNT(*)::bigint AS n
      FROM campaigns
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
