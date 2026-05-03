-- ============================================================
-- 0101 — tenant_target_areas (FLUSSO 1 v3 — geocentric)
-- ============================================================
-- L0 step output: poligoni OSM mappati per ogni tenant in onboarding.
-- Una sola volta per tenant (rimappabile via /v1/territory/map).
--
-- L1 Places discovery itera su queste righe per scoprire candidati
-- con coords precise del capannone (sostituisce la sede legale Atoka).
--
-- Le geometrie sono GEOGRAPHY(POLYGON, 4326): per range query / overlap
-- abbastanza precise. Per il prototipo MVP basta il centroide; la
-- geometria completa è preservata per future feature (mappa Leaflet,
-- "rimappa solo la zona X").
--
-- Backward-compat: la tabella è additiva. Tenant senza zone non vengono
-- processati dal cron v3 (no-op safe). Il flusso v2 (Atoka-first) resta
-- funzionante in parallelo finché non viene sostituito.

CREATE TABLE IF NOT EXISTS tenant_target_areas (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  -- OSM source identification
  osm_id          BIGINT NOT NULL,
  osm_type        TEXT NOT NULL CHECK (osm_type IN ('way', 'relation')),
  -- Geometry (PostGIS geography for accurate distance / overlap)
  geometry        GEOGRAPHY(POLYGON, 4326),
  centroid_lat    NUMERIC(10,7) NOT NULL,
  centroid_lng    NUMERIC(10,7) NOT NULL,
  area_m2         NUMERIC(12,2),
  -- Sector classification
  matched_sectors TEXT[] NOT NULL DEFAULT '{}',
  primary_sector  TEXT,
  matching_score  NUMERIC(5,2),
  -- Geographic scope
  province_code   TEXT,
  -- Lifecycle
  status          TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'archived', 'review')),
  raw_tags        JSONB DEFAULT '{}'::jsonb,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (tenant_id, osm_type, osm_id)
);

CREATE INDEX IF NOT EXISTS idx_target_areas_tenant_status
  ON tenant_target_areas(tenant_id, status);

CREATE INDEX IF NOT EXISTS idx_target_areas_primary_sector
  ON tenant_target_areas(tenant_id, primary_sector)
  WHERE primary_sector IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_target_areas_province
  ON tenant_target_areas(tenant_id, province_code)
  WHERE province_code IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_target_areas_geometry
  ON tenant_target_areas USING GIST (geometry);

-- ============================================================
-- RLS — tenant isolation (same pattern as territories / leads)
-- ============================================================
ALTER TABLE tenant_target_areas ENABLE ROW LEVEL SECURITY;

CREATE POLICY tta_tenant_all ON tenant_target_areas
  FOR ALL
  USING (tenant_id = auth_tenant_id())
  WITH CHECK (tenant_id = auth_tenant_id());

-- Service role bypasses RLS (FastAPI worker uses service key)
-- — no explicit policy needed, the service_role JWT skips RLS.

COMMENT ON TABLE tenant_target_areas IS
  'L0 output: poligoni OSM (industrial / retail / farmyard / etc.) mappati per il tenant in onboarding. L1 Places discovery itera su queste zone per trovare candidati. Sostituisce il discovery Atoka-first del flusso v2.';

COMMENT ON COLUMN tenant_target_areas.matched_sectors IS
  'wizard_groups (da ateco_google_types) compatibili con questa zona. Una zona può matchare più settori: es. landuse=industrial matcha sia industry_heavy che logistics. Ordinati per matching_score DESC.';

COMMENT ON COLUMN tenant_target_areas.primary_sector IS
  'Settore con il matching_score più alto. Usato come tag default per L1 Places discovery (radius + keywords).';

COMMENT ON COLUMN tenant_target_areas.geometry IS
  'GEOGRAPHY(POLYGON, 4326). Centroide ridondante in centroid_lat/lng per query veloci senza ST_Centroid.';

COMMENT ON COLUMN tenant_target_areas.raw_tags IS
  'Tag OSM grezzi (landuse, building, tourism, ecc.) preservati per debug e per future feature di re-classification senza re-fetch Overpass.';
