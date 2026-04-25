-- ============================================================
-- 0058 — immobiliare_listings_cache
-- ============================================================
-- Cache layer for the immobiliare.it building-data matcher
-- (apps/api/src/services/immobiliare_matcher.py).
--
-- Why caching is mandatory (not just nice-to-have):
--   1. **Legal/ToS pressure.** immobiliare.it's terms forbid bulk
--      automated retrieval. Caching matches per-address means we hit
--      the source AT MOST ONCE per (address, fetch_window). If we
--      ever switch backend (official partner feed, manual import,
--      different aggregator) the cache layer is identical.
--   2. **Determinism for filter tuning.** Offline filters that read
--      from this cache must produce the same rejection given the
--      same input — re-running an A/B test shouldn't produce
--      different numbers because the listing went off-market overnight.
--   3. **Cost.** Even with a partner feed, lookups are billable; we
--      do not want to re-pay for "Via Roma 1, Milano" eight times
--      in one day because eight candidates share the address.
--
-- Cache key is the normalised address (lowercase, no punctuation,
-- no trailing CAP/city). The matcher computes the key + a SHA-256
-- digest of the same string and writes/reads here.
--
-- TTL: 90 days. After that the orchestrator pretends there's no
-- match and may attempt a refresh (subject to backend availability).

BEGIN;

CREATE TABLE IF NOT EXISTS immobiliare_listings_cache (
    id                 BIGSERIAL PRIMARY KEY,
    -- Normalised key for deduplication.
    address_normalised TEXT NOT NULL,
    address_hash       TEXT NOT NULL,  -- sha256(address_normalised), hex
    -- Best-effort geo for nearest-listing queries.
    lat                NUMERIC(9, 6),
    lng                NUMERIC(9, 6),
    -- Match status — what the backend told us.
    match_status       TEXT NOT NULL CHECK (match_status IN (
        'matched',          -- one or more listings found
        'no_match',         -- address not present in source
        'ambiguous',        -- multiple equally-likely candidates
        'backend_error',    -- transient — counts as no_match for filter logic
        'backend_disabled'  -- backend toggled off (default in Phase B)
    )),
    -- Aggregated building characteristics inferred from the listing(s).
    -- Optional — only populated when match_status='matched'.
    building_type      TEXT,           -- 'edificio_indipendente' | 'condominio' | 'capannone' | ...
    is_multi_tenant    BOOLEAN,
    is_for_sale        BOOLEAN,
    is_for_rent        BOOLEAN,
    listing_count      INTEGER,        -- how many listings on the same address
    last_listing_seen  TIMESTAMPTZ,
    -- Raw payload for forensic debugging. Kept small (<= 4 KB).
    raw                JSONB NOT NULL DEFAULT '{}'::JSONB,
    -- Provenance.
    backend_name       TEXT NOT NULL,  -- 'null' (stub), 'partner_feed_v1', 'scraper_v1', ...
    fetched_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- TTL anchor — orchestrator treats anything older than 90 days
    -- as cache-miss.
    expires_at         TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '90 days'),
    UNIQUE (address_hash)
);

CREATE INDEX IF NOT EXISTS idx_immobiliare_cache_active
    ON immobiliare_listings_cache (address_hash)
    WHERE expires_at > now();

CREATE INDEX IF NOT EXISTS idx_immobiliare_cache_geo
    ON immobiliare_listings_cache (lat, lng)
    WHERE lat IS NOT NULL AND lng IS NOT NULL;

-- Cache is shared across all tenants — ToS dictates one fetch per
-- address regardless of who's asking, and there's no PII (only a
-- street address + listing metadata). RLS therefore opens SELECT to
-- everyone authenticated, but writes are restricted to service-role
-- (the matcher worker uses the service-role client).
ALTER TABLE immobiliare_listings_cache ENABLE ROW LEVEL SECURITY;

CREATE POLICY immobiliare_cache_select_all
    ON immobiliare_listings_cache
    FOR SELECT
    USING (true);

-- No INSERT/UPDATE policy → only service-role bypass can write,
-- which is exactly what we want.

GRANT SELECT ON immobiliare_listings_cache TO authenticated;

COMMIT;
