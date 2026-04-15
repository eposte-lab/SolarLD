"""Hunter Agent — scans a territory and populates the `roofs` table.

Pipeline (Sprint 1):

    territory_id
        ↓
    fetch bbox from territories
        ↓
    generate_sampling_grid(bbox, step=50m)
        ↓ for each point (bounded by max_roofs)
    geohash(8) dedupe against roofs(tenant_id, geohash)
        ↓
    Google Solar findClosest
        ↓ 404?
    Mapbox reverse-geocode + (TODO Sprint 2) Claude Vision fallback
        ↓
    apply_technical_filters()
        ↓ accepted → upsert roofs (discovered) + emit roof.scanned event
        ↓ rejected → upsert roofs (rejected) with rejection reason

Idempotency: the `(tenant_id, geohash)` UNIQUE constraint on roofs lets us
re-run the scan safely — re-runs re-sample the same grid cells but skip any
(tenant_id, geohash) already present. Only *new* roofs count toward
`roofs_discovered`.

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
from ..services.google_solar_service import (
    COST_PER_CALL_CENTS,
    RoofInsight,
    SolarApiError,
    SolarApiNotFound,
    fetch_building_insight,
)
from ..services.hunter import (
    apply_technical_filters,
    classify_roof,
    generate_sampling_grid,
)
from ..services.mapbox_service import MapboxError, reverse_geocode
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


class HunterOutput(BaseModel):
    roofs_discovered: int = 0
    roofs_filtered_out: int = 0
    roofs_already_known: int = 0
    api_calls: int = 0
    api_cost_cents: int = 0
    used_fallback_count: int = 0
    next_pagination_token: int | None = None
    territory_exhausted: bool = False


class HunterAgent(AgentBase[HunterInput, HunterOutput]):
    name = "agent.hunter"

    async def execute(self, payload: HunterInput) -> HunterOutput:
        sb = get_service_client()

        # 1) Load territory + bbox
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
                "max_roofs": payload.max_roofs,
                "start_index": payload.start_index,
            },
            tenant_id=payload.tenant_id,
        )

        # 2) Walk the grid with bounded concurrency
        out = HunterOutput()
        sem = asyncio.Semaphore(_SOLAR_CONCURRENCY)
        last_index = payload.start_index

        async with httpx.AsyncClient(timeout=15.0) as http_client:
            tasks: list[asyncio.Task[tuple[int, _PointResult]]] = []

            grid = generate_sampling_grid(
                bbox, step_meters=payload.step_meters, start_index=payload.start_index
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
                        )
                        return p.index, result

                tasks.append(asyncio.create_task(handle()))

                # Flush in batches of 32 so memory doesn't balloon on big bboxes
                if len(tasks) >= 32:
                    await _drain_tasks(tasks, out)
                    tasks.clear()

            if tasks:
                await _drain_tasks(tasks, out)

            # Did the grid finish while we were running?
            out.territory_exhausted = out.api_calls + out.roofs_already_known < payload.max_roofs
            if not out.territory_exhausted:
                out.next_pagination_token = last_index + 1

        # 3) Log usage for billing
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
                        "discovered": out.roofs_discovered,
                        "filtered_out": out.roofs_filtered_out,
                        "fallback": out.used_fallback_count,
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
        insight: RoofInsight | None = None
        try:
            insight = await fetch_building_insight(lat, lng, client=http_client)
        except SolarApiNotFound:
            # Mapbox fallback — Sprint 2 wires Claude Vision to estimate
            # geometry; for Sprint 1 we skip the point entirely unless we
            # get a reverse-geocode hit that looks like a building address.
            used_fallback = True
            try:
                _ = await reverse_geocode(lat, lng, client=http_client)
            except MapboxError as exc:
                log.debug("mapbox_fallback_unavailable", lat=lat, lng=lng, err=str(exc))
            return _PointResult(status="no_building", used_fallback=used_fallback, api_calls=1)
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

        # 3) Technical filters
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
            "data_source": RoofDataSource.GOOGLE_SOLAR.value,
            "classification": classification.value,
            "status": (RoofStatus.DISCOVERED if verdict.accepted else RoofStatus.REJECTED).value,
            "scan_cost_cents": COST_PER_CALL_CENTS,
            "raw_data": {
                "solar": insight.raw,
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
                },
                tenant_id=tenant_id,
            )
            return _PointResult(status="discovered", api_calls=1)

        return _PointResult(status="filtered", filter_reason=verdict.reason, api_calls=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _PointResult(BaseModel):
    status: str  # discovered | filtered | duplicate | no_building | api_error | db_error
    filter_reason: str | None = None
    used_fallback: bool = False
    api_calls: int = 0


async def _drain_tasks(
    tasks: list[asyncio.Task[tuple[int, _PointResult]]], out: HunterOutput
) -> None:
    """Await a batch of in-flight point-tasks and fold results into `out`."""
    for task in asyncio.as_completed(tasks):
        _, result = await task
        out.api_calls += result.api_calls
        out.api_cost_cents += result.api_calls * COST_PER_CALL_CENTS
        if result.used_fallback:
            out.used_fallback_count += 1
        if result.status == "discovered":
            out.roofs_discovered += 1
        elif result.status == "filtered":
            out.roofs_filtered_out += 1
        elif result.status == "duplicate":
            out.roofs_already_known += 1
