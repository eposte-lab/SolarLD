-- 0161_qualification_kpi_report.sql
--
-- Two-cohort send report: sends whose contact went through the qualification
-- pipeline (NeverBounce validation + premium contact) vs the "legacy" un-validated
-- sends. Lets the dashboard (and the tenant owner) SEE the lift the qualification
-- system delivers — and the cost of letting NeverBounce run dry (2026-06-25).
--
-- A send is "qualified" iff NeverBounce actually validated that lead's address —
-- recorded as an api_usage_log row (provider='neverbounce', metadata.lead_id).
-- That captures BOTH the pre-system sends AND the days the account ran dry
-- (no validation row) as "legacy".
--
-- SECURITY INVOKER (the default for SQL functions): the function runs with the
-- CALLER's RLS, so leads + api_usage_log are already scoped to the caller's
-- tenant — no tenant arg, no SECURITY DEFINER, no data leak across tenants.
-- Aggregating server-side also sidesteps PostgREST's 1000-row cap (a tenant can
-- have thousands of validation rows).

CREATE OR REPLACE FUNCTION qualification_kpi_report()
RETURNS TABLE (
    cohort        text,
    sent          bigint,
    visited       bigint,
    appointments  bigint,
    engaged       bigint,
    visit_rate    numeric
)
LANGUAGE sql
STABLE
AS $$
    WITH s AS (
        SELECT
            l.id,
            l.dashboard_visited_at,
            l.appointment_requested_at,
            l.engagement_score,
            EXISTS (
                SELECT 1 FROM api_usage_log a
                WHERE a.tenant_id = l.tenant_id
                  AND a.provider = 'neverbounce'
                  AND a.metadata->>'lead_id' = l.id::text
            ) AS validated
        FROM leads l
        WHERE l.outreach_sent_at IS NOT NULL
    )
    SELECT
        CASE WHEN validated THEN 'qualified' ELSE 'legacy' END AS cohort,
        count(*)                                                  AS sent,
        count(*) FILTER (WHERE dashboard_visited_at IS NOT NULL)  AS visited,
        count(*) FILTER (WHERE appointment_requested_at IS NOT NULL) AS appointments,
        count(*) FILTER (WHERE engagement_score > 0)              AS engaged,
        round(
            100.0 * count(*) FILTER (WHERE dashboard_visited_at IS NOT NULL)
            / nullif(count(*), 0),
            1
        )                                                          AS visit_rate
    FROM s
    GROUP BY validated;
$$;

COMMENT ON FUNCTION qualification_kpi_report() IS
  'Per-cohort send KPIs (qualified = NeverBounce-validated vs legacy). SECURITY INVOKER → RLS-scoped to the caller''s tenant. Drives the /invii qualification report.';
