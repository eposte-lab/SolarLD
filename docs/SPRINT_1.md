# Sprint 1 — Hunter Agent (Delivered)

**Duration**: Weeks 3-4
**Goal**: Turn a tenant-selected territory into a populated `roofs` table using
Google Solar API, with Mapbox reverse-geocoding for addresses and a placeholder
for the Claude-Vision fallback (Sprint 2).

## What's in place

### Services (`apps/api/src/services/`)

- **`google_solar_service.py`** — async client for
  `buildingInsights:findClosest`:
  - `fetch_building_insight(lat, lng)` → `RoofInsight`
  - Typed dataclass `RoofInsight` with area, kWp, yearly kWh, dominant
    exposure (8-point cardinal), pitch, shading score, postal fields, and
    the full raw payload for audit.
  - Retries `429/503` with exponential backoff (tenacity).
  - `SolarApiNotFound` is raised (not retried) when a point has no
    building — the Hunter agent catches it and switches to the fallback
    path.
  - Cost constant `COST_PER_CALL_CENTS = 2` used for per-tenant billing.

- **`mapbox_service.py`**:
  - `reverse_geocode(lat, lng)` → Italian postal fields (address, CAP,
    comune, provincia) used to populate `roofs` columns when Google Solar
    doesn't return locality data.
  - `build_static_satellite_url(lat, lng, zoom=19)` — emits a Mapbox
    Static Images URL; consumed by the (Sprint 2) Claude-Vision fallback
    when Google has no coverage.

- **`hunter/` subpackage**:
  - `grid.py`:
    - `generate_sampling_grid(bbox, step_meters=50, max_points, start_index)`
      yields `GridPoint(index, lat, lng)` covering the bbox. Supports
      both bbox shapes (`{ne,sw}` and `{north,south,east,west}`) and
      respects `start_index` for pagination resumption.
    - `estimate_grid_cost(bbox)` — pre-flight budget check called by the
      `/scan-estimate` endpoint.
    - `haversine_km()` helper.
  - `filters.py`:
    - `apply_technical_filters(insight)` → `FilterVerdict(accepted,
      reason)`. Rejects area<20m², kWp<2, shading<0.45, exposure=N,
      pitch∉[5°, 60°].
  - `classification.py`:
    - `classify_roof(insight)` — provisional B2B/B2C split based on area
      + kWp thresholds. Replaced by the Identity agent's real classifier
      in Sprint 2.

### Agent (`apps/api/src/agents/hunter.py`)

Real `HunterAgent.execute()` pipeline:

1. Loads the territory + bbox (tenant-scoped).
2. Walks `generate_sampling_grid(bbox, step_meters, start_index)`.
3. For each point, with bounded concurrency (`asyncio.Semaphore(8)` to
   stay under Google's 100qps quota):
   - Geohash(8) dedupe against `roofs(tenant_id, geohash)`.
   - Google Solar `findClosest`; on 404 → Mapbox reverse-geocode (fallback
     geometry estimation is Sprint 2).
   - `apply_technical_filters()` + `classify_roof()`.
   - Upsert into `roofs` with full raw Solar payload in `raw_data`.
   - Emit `roof.scanned` audit event on accept.
4. Summarizes discovered/filtered/dup counts into `HunterOutput`.
5. Inserts a single row into `api_usage_log` for billing.
6. Emits `hunter.scan_started` / `hunter.scan_completed` events.

**Idempotency**: `UNIQUE(tenant_id, geohash)` on `roofs` means reruns skip
previously scanned cells; `start_index` lets pagination resume at the
next grid cell.

### Queue (`apps/api/src/core/queue.py`)

Thin arq helper (`enqueue`, `close_pool`) so `routes/` can enqueue jobs
without importing `workers/main.py` (avoids circular imports). `main.py`
closes the pool on lifespan shutdown.

### API (`apps/api/src/routes/territories.py`)

- `GET /v1/territories/:id/scan-estimate?step_meters=50` — returns
  `{grid_points, estimated_cost_cents}` without touching Google.
- `POST /v1/territories/:id/scan?max_roofs=500&start_index=0&step_meters=50`
  — enqueues `hunter_task` with a deterministic `job_id` of
  `hunter:<tenant>:<territory>:<start_index>` (in-flight dedupe).

### Tests (`apps/api/tests/`)

26 new unit tests, all pure-functions (no DB, no HTTP):

- **`test_hunter_grid.py`** (9) — grid walking, step spacing,
  truncation, `start_index` resume, both bbox shapes, invalid bbox,
  estimate↔iterator parity, haversine sanity.
- **`test_hunter_filters.py`** (11) — accept/reject paths for area,
  kWp, shading, exposure, pitch + classification into B2B/B2C/UNKNOWN.
- **`test_hunter_solar_parser.py`** (6) — Google Solar JSON parser:
  dominant-segment picking, kWp compute, yearly-kWh fallback,
  whole-roof area, azimuth→cardinal sector mapping.

## What's NOT in place (by design — Sprint 2)

- **Claude Vision fallback** — when Solar 404s we currently skip the
  point. Sprint 2 will feed the Mapbox static tile to
  `claude-sonnet-4-5` with a vision prompt that returns estimated area,
  exposure, pitch.
- **Identity Agent** — Visura + Atoka + Hunter.io enrichment.
- **PII hashing on subjects** upgraded from the Compliance helper to a
  full pre-insert trigger.

## Handoff checklist for Sprint 2

- [ ] `GOOGLE_SOLAR_API_KEY` set in Railway env
- [ ] `MAPBOX_ACCESS_TOKEN` set (also used for dashboard map UI)
- [ ] At least one territory row seeded with a real bbox (e.g. via
      dashboard territory picker or manual SQL insert)
- [ ] Run `POST /v1/territories/:id/scan-estimate` first to sanity-check
      the cost before triggering the real scan.
- [ ] Watch `api_usage_log` during the first production scan to catch
      quota surprises.
