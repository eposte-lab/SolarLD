"""Hunter Agent — scans a territory and populates the `roofs` table.

Sprint 9: three operating modes selected via `tenant_configs.scan_mode`.

  ┌────────────────┬──────────────────────────────────────────────────────┐
  │ scan_mode      │ Strategy                                             │
  ├────────────────┼──────────────────────────────────────────────────────┤
  │ b2b_precision  │ Google Places Nearby Search first → Solar only on    │
  │                │ known businesses. Cheap per-lead, high precision.    │
  │                │ Also writes a `subjects` row with display name +     │
  │                │ website + phone (Tier 0 enrichment).                 │
  ├────────────────┼──────────────────────────────────────────────────────┤
  │ opportunistic  │ Classic grid sampling (50m step). Solar on every     │
  │                │ point. Mixed B2B/B2C. Default for back-filled        │
  │                │ tenants from before Sprint 9.                        │
  ├────────────────┼──────────────────────────────────────────────────────┤
  │ volume         │ Grid sampling + very permissive technical filters.   │
  │                │ Maximises outreach volume, lower quality.            │
  └────────────────┴──────────────────────────────────────────────────────┘

Grid-sampling pipeline (opportunistic/volume):

    territory.bbox
        ↓
    generate_sampling_grid(bbox, step=config.scan_grid_density_m)
        ↓ for each point (bounded by max_roofs)
    geohash(8) dedupe against roofs(tenant_id, geohash)
        ↓
    Google Solar findClosest
        ↓ 404?
    Mapbox reverse-geocode + Claude Vision fallback
        ↓
    apply_technical_filters (tenant-config aware)
        ↓ accepted → upsert roofs (discovered) + emit roof.scanned

Places pipeline (b2b_precision):

    territory.bbox
        ↓
    generate_search_cells(bbox, radius_m=5000)
        ↓ for each cell
    google_places.nearby_search(types=config.place_type_whitelist)
        ↓
    dedupe + rank by config.place_type_priority
        ↓ for each place (bounded by max_roofs / budget)
    geohash dedupe against roofs(tenant_id, geohash)
        ↓
    Google Solar findClosest at place.lat/lng
        ↓
    apply_technical_filters (config.technical_b2b)
        ↓ accepted → upsert roofs + upsert subjects(business_name=place.name, …)

Idempotency: the `(tenant_id, geohash)` UNIQUE constraint on roofs +
`(tenant_id, vat_number)` or `(tenant_id, place_id)` on subjects lets
every mode re-run safely. Only *new* roofs count toward `roofs_discovered`.

Cost control:
  - `max_roofs` caps Google Solar calls per agent run.
  - Every API call is logged in `api_usage_log` (for monthly billing +
    budget alerts).
  - `start_index` pagination token lets subsequent runs continue where the
    last one stopped (arq retries or explicit re-trigger).
"""

from __future__ import annotations

import asyncio
from typing import Any

import geohash  # type: ignore[import-untyped]
import httpx
from pydantic import BaseModel, Field

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from ..models.enums import RoofDataSource, RoofStatus
from ..services.claude_vision_service import (
    VISION_COST_PER_CALL_CENTS,
    estimate_roof_from_image,
)
from ..services.google_solar_service import (
    COST_PER_CALL_CENTS,
    RoofInsight,
    SolarApiError,
    SolarApiNotFound,
    fetch_building_insight,
)
from ..services.google_places_service import (
    DETAILS_COST_PER_CALL_CENTS,
    NEARBY_COST_PER_CALL_CENTS,
    PlaceSummary,
    PlacesApiError,
    nearby_search,
    place_details,
)
from ..services.hunter import (
    apply_technical_filters,
    classify_roof,
    dedupe_places,
    filter_operational,
    generate_sampling_grid,
    generate_search_cells,
    rank_places,
)
from ..services.mapbox_service import (
    MapboxError,
    build_static_satellite_url,
    reverse_geocode,
)
from ..services.tenant_config_service import TechnicalFilters, TenantConfig, get_for_tenant
from .base import AgentBase

log = get_logger(__name__)

# Hard concurrency cap to stay under Google's 100 qps quota.
_SOLAR_CONCURRENCY = 8


class HunterInput(BaseModel):
    tenant_id: str
    territory_id: str
    max_roofs: int = Field(default=1000, ge=1, le=10000)
    start_index: int = Field(default=0, ge=0)
    step_meters: float = Field(default=50.0, gt=0.0, le=500.0)
    # Sprint 9: optional mode override — when absent we read
    # tenant_configs.scan_mode. Used by tests and admin triggers.
    scan_mode_override: str | None = Field(default=None)
    # Radius per Places Nearby cell (b2b_precision only).
    places_radius_m: float = Field(default=5000.0, gt=0.0, le=50000.0)


class HunterOutput(BaseModel):
    # Mode selector (one of b2b_precision | opportunistic | volume)
    scan_mode: str = "opportunistic"
    roofs_discovered: int = 0
    roofs_filtered_out: int = 0
    roofs_already_known: int = 0
    api_calls: int = 0
    api_cost_cents: int = 0
    used_fallback_count: int = 0
    next_pagination_token: int | None = None
    territory_exhausted: bool = False
    # b2b_precision-specific counters
    places_found: int = 0
    places_deduped: int = 0
    places_details_fetched: int = 0
    subjects_created: int = 0


class HunterAgent(AgentBase[HunterInput, HunterOutput]):
    name = "agent.hunter"

    async def execute(self, payload: HunterInput) -> HunterOutput:
        """Dispatch to the mode-specific runner based on tenant config."""
        sb = get_service_client()

        # 1) Load tenant config (Sprint 9)
        config = await get_for_tenant(payload.tenant_id)
        mode = payload.scan_mode_override or config.scan_mode

        # 2) Load territory + bbox
        tres = (
            sb.table("territories")
            .select("id, tenant_id, bbox, name, type, code")
            .eq("id", payload.territory_id)
            .eq("tenant_id", payload.tenant_id)
            .single()
            .execute()
        )
        territory = tres.data
        if not territory:
            raise SolarApiError(f"territory {payload.territory_id} not found")
        bbox = territory.get("bbox")
        if not bbox:
            raise SolarApiError(f"territory {payload.territory_id} has no bbox set")

        await self._emit_event(
            event_type="hunter.scan_started",
            payload={
                "territory_id": payload.territory_id,
                "scan_mode": mode,
                "max_roofs": payload.max_roofs,
                "start_index": payload.start_index,
            },
            tenant_id=payload.tenant_id,
        )

        # 3) Run the mode-specific pipeline
        if mode == "b2b_precision":
            out = await self._run_b2b_precision(
                bbox=bbox, payload=payload, config=config
            )
        elif mode == "volume":
            out = await self._run_grid_sampling(
                bbox=bbox, payload=payload, config=config, mode="volume"
            )
        else:
            # 'opportunistic' + unknown modes fall through to grid sampling.
            out = await self._run_grid_sampling(
                bbox=bbox, payload=payload, config=config, mode="opportunistic"
            )

        out.scan_mode = mode

        # 4) Log usage for billing
        try:
            sb.table("api_usage_log").insert(
                {
                    "tenant_id": payload.tenant_id,
                    "provider": "google_solar",
                    "endpoint": "buildingInsights:findClosest",
                    "request_count": out.api_calls,
                    "cost_cents": out.api_cost_cents,
                    "status": "success" if out.api_calls > 0 else "noop",
                    "metadata": {
                        "territory_id": payload.territory_id,
                        "scan_mode": mode,
                        "discovered": out.roofs_discovered,
                        "filtered_out": out.roofs_filtered_out,
                        "fallback": out.used_fallback_count,
                        "places_found": out.places_found,
                        "places_details_fetched": out.places_details_fetched,
                        "subjects_created": out.subjects_created,
                    },
                }
            ).execute()
        except Exception as exc:  # noqa: BLE001
            log.warning("api_usage_log_write_failed", error=str(exc))

        await self._emit_event(
            event_type="hunter.scan_completed",
            payload=out.model_dump(),
            tenant_id=payload.tenant_id,
        )
        return out

    # ------------------------------------------------------------------
    # MODE: opportunistic / volume — grid sampling (current behavior)
    # ------------------------------------------------------------------

    async def _run_grid_sampling(
        self,
        *,
        bbox: dict[str, Any],
        payload: HunterInput,
        config: TenantConfig,
        mode: str,
    ) -> HunterOutput:
        """Grid sampling pipeline shared by 'opportunistic' and 'volume'.

        The only difference between the two modes is which technical
        filter threshold is applied (volume uses B2C's permissive set,
        opportunistic uses a best-fit per-point). Both ignore the Places
        whitelist and walk every building in the bbox.
        """
        out = HunterOutput()
        sem = asyncio.Semaphore(_SOLAR_CONCURRENCY)
        last_index = payload.start_index
        # Volume mode takes the more permissive of the two segment
        # filters; opportunistic still relies on the legacy global
        # constants inside `apply_technical_filters`.
        override_filters: TechnicalFilters | None = None
        if mode == "volume":
            override_filters = config.technical_b2c

        step = payload.step_meters
        if mode in ("opportunistic", "volume") and config.scan_grid_density_m:
            step = float(config.scan_grid_density_m)

        async with httpx.AsyncClient(timeout=15.0) as http_client:
            tasks: list[asyncio.Task[tuple[int, _PointResult]]] = []

            grid = generate_sampling_grid(
                bbox, step_meters=step, start_index=payload.start_index
            )
            for point in grid:
                if out.roofs_discovered + out.roofs_filtered_out >= payload.max_roofs:
                    break
                last_index = point.index

                async def handle(p: Any = point) -> tuple[int, _PointResult]:  # capture
                    async with sem:
                        result = await self._process_point(
                            tenant_id=payload.tenant_id,
                            territory_id=payload.territory_id,
                            lat=p.lat,
                            lng=p.lng,
                            http_client=http_client,
                            override_filters=override_filters,
                        )
                        return p.index, result

                tasks.append(asyncio.create_task(handle()))

                if len(tasks) >= 32:
                    await _drain_tasks(tasks, out)
                    tasks.clear()

            if tasks:
                await _drain_tasks(tasks, out)

            out.territory_exhausted = out.api_calls + out.roofs_already_known < payload.max_roofs
            if not out.territory_exhausted:
                out.next_pagination_token = last_index + 1

        return out

    # ------------------------------------------------------------------
    # MODE: b2b_precision — Places-first, Solar-only-on-candidates
    # ------------------------------------------------------------------

    async def _run_b2b_precision(
        self,
        *,
        bbox: dict[str, Any],
        payload: HunterInput,
        config: TenantConfig,
    ) -> HunterOutput:
        """Google Places discovery → Solar scan on the returned coords.

        Places calls are the expensive line here, so we:
          1. Tile the bbox with overlapping circles (`generate_search_cells`).
          2. One Nearby Search per cell, including `config.place_type_whitelist`.
          3. Dedupe on place_id, rank by `config.place_type_priority`.
          4. For each place (until max_roofs hit), run Solar + filter.
          5. For each accepted place, fetch Place Details (website/phone)
             and upsert `roofs` + `subjects` rows.
        """
        out = HunterOutput()

        async with httpx.AsyncClient(timeout=20.0) as http_client:
            # --- Step 1-3: discovery ---
            cells = generate_search_cells(bbox, radius_m=payload.places_radius_m)
            batches: list[list[PlaceSummary]] = []
            try:
                for cell in cells:
                    places = await nearby_search(
                        cell.center_lat,
                        cell.center_lng,
                        radius_m=cell.radius_m,
                        included_types=list(config.place_type_whitelist),
                        client=http_client,
                    )
                    out.api_calls += 1
                    out.api_cost_cents += NEARBY_COST_PER_CALL_CENTS
                    batches.append(places)
            except PlacesApiError as exc:
                log.error("places_discovery_failed", extra={"err": str(exc)})
                return out

            out.places_found = sum(len(b) for b in batches)
            merged = dedupe_places(batches)
            merged = filter_operational(merged)
            merged = rank_places(merged, config.place_type_priority)
            out.places_deduped = len(merged)

            # --- Step 4-5: Solar + subject upsert ---
            sem = asyncio.Semaphore(_SOLAR_CONCURRENCY)
            tasks: list[asyncio.Task[_PointResult]] = []
            processed = 0
            for place in merged:
                if (out.roofs_discovered + out.roofs_filtered_out) >= payload.max_roofs:
                    break
                processed += 1

                async def handle(pl: PlaceSummary = place) -> _PointResult:
                    async with sem:
                        return await _process_place_impl(
                            tenant_id=payload.tenant_id,
                            territory_id=payload.territory_id,
                            place=pl,
                            http_client=http_client,
                            filters=config.technical_b2b,
                        )

                tasks.append(asyncio.create_task(handle()))
                if len(tasks) >= 16:
                    await _drain_place_tasks(tasks, out)
                    tasks.clear()

            if tasks:
                await _drain_place_tasks(tasks, out)

            out.territory_exhausted = processed >= len(merged)

        return out

    # ------------------------------------------------------------------
    # Per-point logic
    # ------------------------------------------------------------------

    async def _process_point(
        self,
        *,
        tenant_id: str,
        territory_id: str,
        lat: float,
        lng: float,
        http_client: httpx.AsyncClient,
        override_filters: TechnicalFilters | None = None,
    ) -> "_PointResult":
        """Run the Solar→Mapbox→upsert pipeline for a single lat/lng."""
        gh = geohash.encode(lat, lng, precision=8)

        # Dedupe against existing roofs for this tenant
        sb = get_service_client()
        dup = (
            sb.table("roofs")
            .select("id")
            .eq("tenant_id", tenant_id)
            .eq("geohash", gh)
            .limit(1)
            .execute()
        )
        if dup.data:
            return _PointResult(status="duplicate")

        # 1) Google Solar
        used_fallback = False
        vision_calls = 0
        data_source = RoofDataSource.GOOGLE_SOLAR
        insight: RoofInsight | None = None
        try:
            insight = await fetch_building_insight(lat, lng, client=http_client)
        except SolarApiNotFound:
            # Fallback path: ask Claude to estimate geometry from a Mapbox
            # satellite tile. Returns None if no building is confidently
            # visible, in which case we skip the point entirely.
            used_fallback = True
            try:
                image_url = build_static_satellite_url(lat, lng, zoom=19)
                insight = await estimate_roof_from_image(image_url, lat, lng)
                vision_calls = 1
                if insight is None:
                    return _PointResult(
                        status="no_building",
                        used_fallback=True,
                        api_calls=1,
                        vision_calls=vision_calls,
                    )
                data_source = RoofDataSource.MAPBOX_AI_FALLBACK
            except (MapboxError, RuntimeError) as exc:
                log.debug("vision_fallback_unavailable", lat=lat, lng=lng, err=str(exc))
                return _PointResult(
                    status="no_building", used_fallback=True, api_calls=1
                )
        except SolarApiError as exc:
            log.warning("solar_point_error", lat=lat, lng=lng, err=str(exc))
            return _PointResult(status="api_error", api_calls=1)

        # 2) Reverse-geocode for postal fields (non-fatal if missing)
        address = comune = provincia = cap = None
        try:
            geo = await reverse_geocode(lat, lng, client=http_client)
            address = geo.address
            comune = geo.comune
            provincia = geo.provincia
            cap = geo.cap
        except MapboxError as exc:
            log.debug("geocode_failed", lat=lat, lng=lng, err=str(exc))

        # 3) Technical filters — use tenant-config override when provided
        if override_filters is not None:
            verdict = _apply_config_filters(insight, override_filters)
        else:
            verdict = apply_technical_filters(insight)
        classification = classify_roof(insight)

        row = {
            "tenant_id": tenant_id,
            "territory_id": territory_id,
            "lat": insight.lat or lat,
            "lng": insight.lng or lng,
            "geohash": gh,
            "address": address,
            "cap": cap,
            "comune": comune,
            "provincia": provincia,
            "area_sqm": insight.area_sqm,
            "estimated_kwp": insight.estimated_kwp,
            "estimated_yearly_kwh": insight.estimated_yearly_kwh,
            "exposure": insight.dominant_exposure,
            "pitch_degrees": insight.pitch_degrees,
            "shading_score": insight.shading_score,
            "data_source": data_source.value,
            "classification": classification.value,
            "status": (RoofStatus.DISCOVERED if verdict.accepted else RoofStatus.REJECTED).value,
            "scan_cost_cents": COST_PER_CALL_CENTS
            + (VISION_COST_PER_CALL_CENTS * vision_calls),
            "raw_data": {
                "solar": insight.raw if data_source == RoofDataSource.GOOGLE_SOLAR else None,
                "vision": insight.raw if data_source == RoofDataSource.MAPBOX_AI_FALLBACK else None,
                "filter_reason": verdict.reason,
            },
        }

        try:
            sb.table("roofs").upsert(row, on_conflict="tenant_id,geohash").execute()
        except Exception as exc:  # noqa: BLE001
            log.warning("roof_upsert_failed", err=str(exc), geohash=gh)
            return _PointResult(status="db_error", api_calls=1)

        if verdict.accepted:
            await self._emit_event(
                event_type="roof.scanned",
                payload={
                    "geohash": gh,
                    "area_sqm": insight.area_sqm,
                    "estimated_kwp": insight.estimated_kwp,
                    "classification": classification.value,
                    "data_source": data_source.value,
                },
                tenant_id=tenant_id,
            )
            return _PointResult(
                status="discovered",
                api_calls=1,
                vision_calls=vision_calls,
                used_fallback=used_fallback,
            )

        return _PointResult(
            status="filtered",
            filter_reason=verdict.reason,
            api_calls=1,
            vision_calls=vision_calls,
            used_fallback=used_fallback,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _PointResult(BaseModel):
    status: str  # discovered | filtered | duplicate | no_building | api_error | db_error
    filter_reason: str | None = None
    used_fallback: bool = False
    api_calls: int = 0
    vision_calls: int = 0
    # b2b_precision extensions
    details_calls: int = 0
    subject_created: bool = False


async def _drain_tasks(
    tasks: list[asyncio.Task[tuple[int, _PointResult]]], out: HunterOutput
) -> None:
    """Await a batch of in-flight point-tasks and fold results into `out`."""
    for task in asyncio.as_completed(tasks):
        _, result = await task
        out.api_calls += result.api_calls
        out.api_cost_cents += (
            result.api_calls * COST_PER_CALL_CENTS
            + result.vision_calls * VISION_COST_PER_CALL_CENTS
        )
        if result.used_fallback:
            out.used_fallback_count += 1
        if result.status == "discovered":
            out.roofs_discovered += 1
        elif result.status == "filtered":
            out.roofs_filtered_out += 1
        elif result.status == "duplicate":
            out.roofs_already_known += 1


async def _drain_place_tasks(
    tasks: list[asyncio.Task["_PointResult"]], out: HunterOutput
) -> None:
    """Fold place-processing task results into `out`."""
    for task in asyncio.as_completed(tasks):
        result = await task
        out.api_calls += result.api_calls
        out.api_cost_cents += (
            result.api_calls * COST_PER_CALL_CENTS
            + result.vision_calls * VISION_COST_PER_CALL_CENTS
            + result.details_calls * DETAILS_COST_PER_CALL_CENTS
        )
        out.places_details_fetched += result.details_calls
        if result.subject_created:
            out.subjects_created += 1
        if result.used_fallback:
            out.used_fallback_count += 1
        if result.status == "discovered":
            out.roofs_discovered += 1
        elif result.status == "filtered":
            out.roofs_filtered_out += 1
        elif result.status == "duplicate":
            out.roofs_already_known += 1


def _apply_config_filters(insight: RoofInsight, filters: TechnicalFilters) -> Any:
    """Tenant-config-driven filter verdict. Mirrors `apply_technical_filters`
    but reads thresholds from a `TechnicalFilters` dataclass.

    Returns a `FilterVerdict`-compatible object (duck-typed as `.accepted`,
    `.reason` are the only attributes consumed by callers).
    """
    from ..services.hunter.filters import FilterVerdict

    if insight.area_sqm < filters.min_area_sqm:
        return FilterVerdict(False, f"area<{filters.min_area_sqm}m²")
    if insight.estimated_kwp < filters.min_kwp:
        return FilterVerdict(False, f"kwp<{filters.min_kwp}")
    # shading_score: higher = better → reject when < (1 - max_shading)
    if insight.shading_score < (1.0 - filters.max_shading):
        return FilterVerdict(False, f"shading={insight.shading_score:.2f}")
    if insight.dominant_exposure == "N":
        return FilterVerdict(False, f"exposure={insight.dominant_exposure}")
    return FilterVerdict(True, None)


# ---------------------------------------------------------------------------
# Place-processing pipeline (b2b_precision)
# ---------------------------------------------------------------------------


# Forward ref so the method above is textual-valid; the method is defined
# as a free function because HunterAgent already has many method attrs
# and binding through `self` here adds nothing.


async def _process_place_impl(
    *,
    tenant_id: str,
    territory_id: str,
    place: PlaceSummary,
    http_client: httpx.AsyncClient,
    filters: TechnicalFilters,
) -> "_PointResult":
    """Discover-or-update a roof + subject from a Google Place.

    Pipeline:
      1. Geohash dedupe against `roofs`.
      2. Solar findClosest at place.lat/lng.
      3. Apply B2B technical filters (from tenant config).
      4. Fetch Place Details (website, phone) — one extra call,
         only on accepted places.
      5. Upsert `roofs` (unique on tenant_id+geohash).
      6. Upsert `subjects` with business_name from place.name.
    """
    sb = get_service_client()
    gh = geohash.encode(place.lat, place.lng, precision=8)

    # 1) Roof dedupe
    dup = (
        sb.table("roofs")
        .select("id")
        .eq("tenant_id", tenant_id)
        .eq("geohash", gh)
        .limit(1)
        .execute()
    )
    if dup.data:
        return _PointResult(status="duplicate")

    # 2) Solar
    insight: RoofInsight | None = None
    try:
        insight = await fetch_building_insight(place.lat, place.lng, client=http_client)
    except SolarApiNotFound:
        # b2b_precision intentionally skips the Mapbox/Vision fallback:
        # Places already told us there's a business here, so a Solar 404
        # means the building footprint isn't modelled by Google — not
        # worth the Vision spend on every miss. Ship as api_error
        # (not filtered, not discovered) and move on.
        return _PointResult(status="api_error", api_calls=1)
    except SolarApiError as exc:
        log.warning("solar_place_error", extra={"place": place.place_id, "err": str(exc)})
        return _PointResult(status="api_error", api_calls=1)

    # 3) Technical filters (tenant-config)
    verdict = _apply_config_filters(insight, filters)
    classification = classify_roof(insight)

    # 4) Fetch Details only for accepted roofs
    website = phone = None
    details_calls = 0
    if verdict.accepted:
        try:
            details = await place_details(place.place_id, client=http_client)
            website = details.website
            phone = details.phone_international or details.phone_national
            details_calls = 1
        except PlacesApiError as exc:
            log.debug("place_details_failed", extra={"place": place.place_id, "err": str(exc)})

    # 5) Reverse-geocode for postal fields — best-effort
    address = comune = provincia = cap = None
    try:
        geo = await reverse_geocode(place.lat, place.lng, client=http_client)
        address = geo.address or place.address
        comune = geo.comune
        provincia = geo.provincia
        cap = geo.cap
    except MapboxError:
        address = place.address

    row = {
        "tenant_id": tenant_id,
        "territory_id": territory_id,
        "lat": insight.lat or place.lat,
        "lng": insight.lng or place.lng,
        "geohash": gh,
        "address": address,
        "cap": cap,
        "comune": comune,
        "provincia": provincia,
        "area_sqm": insight.area_sqm,
        "estimated_kwp": insight.estimated_kwp,
        "estimated_yearly_kwh": insight.estimated_yearly_kwh,
        "exposure": insight.dominant_exposure,
        "pitch_degrees": insight.pitch_degrees,
        "shading_score": insight.shading_score,
        "data_source": RoofDataSource.GOOGLE_SOLAR.value,
        "classification": "b2b",  # b2b_precision mode asserts B2B
        "status": (RoofStatus.DISCOVERED if verdict.accepted else RoofStatus.REJECTED).value,
        "scan_cost_cents": COST_PER_CALL_CENTS,
        "raw_data": {
            "solar": insight.raw,
            "place": {
                "place_id": place.place_id,
                "name": place.name,
                "types": list(place.types),
                "primary_type": place.primary_type,
                "website": website,
                "phone": phone,
            },
            "filter_reason": verdict.reason,
        },
    }

    try:
        up = sb.table("roofs").upsert(row, on_conflict="tenant_id,geohash").execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("roof_upsert_failed", extra={"err": str(exc), "geohash": gh})
        return _PointResult(status="db_error", api_calls=1, details_calls=details_calls)

    subject_created = False
    if verdict.accepted:
        roof_id = (up.data[0]["id"] if up.data else None)
        # Subject upsert — use (tenant_id, place_id) semantics via
        # metadata.place_id in raw_data; real UNIQUE would require a
        # schema change. For now: skip if an existing subject has the
        # same place_id raw_data.
        if roof_id:
            existing = (
                sb.table("subjects")
                .select("id")
                .eq("tenant_id", tenant_id)
                .eq("roof_id", roof_id)
                .limit(1)
                .execute()
            )
            if not existing.data:
                try:
                    sb.table("subjects").insert(
                        {
                            "tenant_id": tenant_id,
                            "roof_id": roof_id,
                            "type": "b2b",
                            "business_name": place.name,
                            "business_website": website,
                            "business_phone": phone,
                            # VAT / ATECO / employees filled later by
                            # Tier-2 Atoka enrichment.
                            "raw_data": {
                                "place_id": place.place_id,
                                "google_types": list(place.types),
                            },
                        }
                    ).execute()
                    subject_created = True
                except Exception as exc:  # noqa: BLE001
                    log.warning("subject_insert_failed", extra={"err": str(exc)})

    return _PointResult(
        status=("discovered" if verdict.accepted else "filtered"),
        filter_reason=verdict.reason,
        api_calls=1,
        details_calls=details_calls,
        subject_created=subject_created,
    )


