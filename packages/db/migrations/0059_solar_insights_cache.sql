-- ============================================================
-- 0059 — solar_insights_cache
-- ============================================================
-- Cache the most expensive single call in the pipeline:
-- Google Solar API `buildingInsights:findClosest` (~€0.05/lookup).
--
-- Why
-- ---
-- The Italian B2B funnel re-scans the same buildings repeatedly:
--   * the same address may appear in multiple Atoka discoveries
--     (different ATECO codes, headquarters vs operational site)
--   * a tenant re-runs a campaign on the same CAP/segment, hitting
--     known buildings again
--   * forensic re-runs after a render fix re-call Solar API for
--     coordinates we already analysed
-- Each repeat costs another €0.05 + introduces non-determinism if the
-- model behind Solar API ticks forward between calls.
--
-- Keying
-- ------
-- Latitude/longitude are quantised to 5 decimal places (~1.1 m at
-- Italian latitudes) BEFORE hashing. That collapses the inevitable
-- micro-jitter from forward geocoding (Mapbox returns 7-8 decimals
-- but consecutive geocodes of the same address can wobble by 1-2 m).
--
-- TTL
-- ---
-- 180 days. Italian aerial imagery in Google's dataset refreshes
-- once every 12-18 months on average, so we revalidate twice in
-- one refresh cycle. Cache rows older than this are treated as
-- misses and silently overwritten on next fetch.
--
-- Schema
-- ------
-- We store the parsed `building_insight` payload as JSONB so the
-- Python side can rebuild a `RoofInsight` dataclass without re-running
-- the parser. We also store the raw API response so an operator can
-- reproduce parser bugs offline.
--
-- The same table holds dataLayers cache rows under `payload_kind='data_layers'`
-- so both Solar API endpoints share the cache infrastructure (different
-- TTL would be a future ALTER if needed).

BEGIN;

CREATE TABLE IF NOT EXISTS solar_insights_cache (
    id              BIGSERIAL PRIMARY KEY,
    -- Quantised coordinates — also stored as the cache key.
    lat_q           NUMERIC(8, 5) NOT NULL,
    lng_q           NUMERIC(8, 5) NOT NULL,
    -- 'building_insight' = fetch_building_insight() output
    -- 'data_layers'      = fetch_data_layers() output
    payload_kind    TEXT NOT NULL CHECK (payload_kind IN (
        'building_insight', 'data_layers'
    )),
    -- Status — even a "no_building_at_this_point" answer is worth caching:
    -- it costs Google nothing and saves us €0.05 the next time someone
    -- re-scans the same empty pixel.
    status          TEXT NOT NULL CHECK (status IN (
        'ok', 'not_found', 'error'
    )),
    parsed_payload  JSONB,         -- RoofInsight / DataLayers serialisation
    raw_response    JSONB,         -- pristine Google response for debug
    quality_used    TEXT,          -- 'HIGH' | 'MEDIUM' | 'LOW' — which tier hit
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '180 days'),
    UNIQUE (lat_q, lng_q, payload_kind)
);

CREATE INDEX IF NOT EXISTS idx_solar_cache_active
    ON solar_insights_cache (lat_q, lng_q, payload_kind)
    WHERE expires_at > now();

-- Cross-tenant: Solar API responses are tied to physical coordinates,
-- not to a tenant. Sharing the cache is the correct economic model
-- and there's no PII in the response. Service-role writes only.
ALTER TABLE solar_insights_cache ENABLE ROW LEVEL SECURITY;

CREATE POLICY solar_cache_select_all
    ON solar_insights_cache
    FOR SELECT
    USING (true);

GRANT SELECT ON solar_insights_cache TO authenticated;

COMMIT;
