"""Caching wrapper for `google_solar_service` (Phase B, Task 5).

Why a separate module
---------------------
The bare `fetch_building_insight` and `fetch_data_layers` already
juggle:
  * mock-mode short-circuit
  * HIGH → MEDIUM → LOW quality tier-down
  * 404 / 429 / 5xx error mapping
  * httpx client lifecycle
Inlining cache logic would make those functions hard to follow. Instead
this module exposes drop-in replacements that:
  1. quantise the (lat, lng) coordinate
  2. read from `solar_insights_cache` (migration 0059)
  3. fall through to the bare function on miss
  4. persist the result for next time

The legacy v1 pipeline keeps calling the bare functions; the v2
orchestrator imports from this module. Tenants migrate by flipping
`tenants.pipeline_version` to 2.

Cache invariants
----------------
* The same (lat_q, lng_q, payload_kind) row is shared across tenants.
  Solar API responses are tied to physical coordinates — there's no
  tenant-specific data.
* Mock mode bypasses the cache entirely (mock data is already
  deterministic on (lat, lng); caching it would just bloat the table).
* `not_found` and `error` outcomes ARE cached. Re-asking Google about
  an empty pixel ten times costs €0.50 and gives the same answer.
* TTL is enforced server-side (partial index in SQL) AND client-side
  (defence-in-depth in `_read_cache`).
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from ..core.config import settings
from ..core.supabase_client import get_service_client
from .google_solar_service import (
    DataLayers,
    RoofInsight,
    SolarApiError,
    SolarApiNotFound,
    SolarApiRateLimited,
    SolarPanel,
    fetch_building_insight,
    fetch_data_layers,
)

log = structlog.get_logger(__name__)

# Quantisation precision: 5 decimal places ≈ 1.1 m at Italian latitudes.
# Tighter than this catches geocoder jitter; looser would conflate
# adjacent buildings on dense urban streets.
COORD_PRECISION = 5

PAYLOAD_KIND_BUILDING = "building_insight"
PAYLOAD_KIND_DATA_LAYERS = "data_layers"


def _quantise(value: float) -> float:
    """Round a coordinate to `COORD_PRECISION` decimals.

    Float rounding is good enough — we don't need decimal-precision
    here, the SQL column is `numeric(8,5)` which does the final clamp.
    """
    return round(value, COORD_PRECISION)


# ---------------------------------------------------------------------------
# Public: building_insight cache
# ---------------------------------------------------------------------------


async def fetch_building_insight_cached(
    lat: float,
    lng: float,
    *,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> RoofInsight:
    """Cache-aware wrapper for `fetch_building_insight`.

    Behaviour identical to the bare function: returns `RoofInsight` on
    success, raises `SolarApiNotFound` on no-building, `SolarApiError`
    or `SolarApiRateLimited` otherwise.

    Cached statuses:
      * 'ok'        → re-hydrate the RoofInsight from JSONB
      * 'not_found' → raise SolarApiNotFound (cached negative)
      * 'error'     → IGNORE the cache row, retry live (errors are usually
                      transient — a cached error would block legitimate
                      retries indefinitely)
    """

    # Mock mode: bypass cache entirely. The bare function returns
    # deterministic synthetic data on every call.
    if settings.google_solar_mock_mode and not (api_key or settings.google_solar_api_key):
        return await fetch_building_insight(lat, lng, client=client, api_key=api_key)

    lat_q = _quantise(lat)
    lng_q = _quantise(lng)

    cached = await _read_cache(lat_q, lng_q, PAYLOAD_KIND_BUILDING)
    if cached is not None:
        if cached["status"] == "ok" and cached.get("parsed_payload"):
            try:
                return _rehydrate_roof_insight(cached["parsed_payload"])
            except Exception as exc:  # noqa: BLE001
                # Cache row is malformed (schema drift). Treat as miss.
                log.warning(
                    "solar_cache.rehydrate_failed",
                    err=str(exc),
                    lat_q=lat_q,
                    lng_q=lng_q,
                )
        elif cached["status"] == "not_found":
            raise SolarApiNotFound(f"no building at ({lat}, {lng}) [cached]")
        # 'error' → fall through to live retry

    # Live call.
    try:
        result = await fetch_building_insight(
            lat, lng, client=client, api_key=api_key
        )
    except SolarApiNotFound:
        await _write_cache(
            lat_q=lat_q,
            lng_q=lng_q,
            payload_kind=PAYLOAD_KIND_BUILDING,
            status="not_found",
            parsed_payload=None,
            raw_response=None,
            quality_used=None,
        )
        raise
    except SolarApiRateLimited:
        # Don't cache rate-limits — they're a server signal to back off,
        # not a permanent answer.
        raise
    except SolarApiError:
        # Don't cache transient errors. The next attempt should retry.
        raise

    await _write_cache(
        lat_q=lat_q,
        lng_q=lng_q,
        payload_kind=PAYLOAD_KIND_BUILDING,
        status="ok",
        parsed_payload=_serialise_roof_insight(result),
        raw_response=result.raw,
        # The tier the bare function landed on is buried in result.raw;
        # extracting it would require parser awareness — leave NULL for now,
        # populate when the bare function is refactored to return it.
        quality_used=None,
    )
    return result


# ---------------------------------------------------------------------------
# Public: data_layers cache
# ---------------------------------------------------------------------------


async def fetch_data_layers_cached(
    lat: float,
    lng: float,
    *,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
    radius_m: int = 50,
) -> DataLayers:
    """Cache-aware wrapper for `fetch_data_layers`.

    DataLayers is smaller than building_insight (just three URL/string
    fields) so the cache row is tiny. Same TTL, same statuses.
    """

    if settings.google_solar_mock_mode and not (api_key or settings.google_solar_api_key):
        return await fetch_data_layers(
            lat, lng, client=client, api_key=api_key, radius_m=radius_m
        )

    lat_q = _quantise(lat)
    lng_q = _quantise(lng)

    cached = await _read_cache(lat_q, lng_q, PAYLOAD_KIND_DATA_LAYERS)
    if cached is not None and cached["status"] == "ok" and cached.get("parsed_payload"):
        p = cached["parsed_payload"]
        try:
            return DataLayers(
                rgb_url=p["rgb_url"],
                imagery_quality=p["imagery_quality"],
                imagery_date=p["imagery_date"],
            )
        except KeyError:
            log.warning(
                "solar_cache.data_layers_rehydrate_failed",
                lat_q=lat_q,
                lng_q=lng_q,
            )
    elif cached is not None and cached["status"] == "not_found":
        raise SolarApiNotFound(f"no data layers at ({lat}, {lng}) [cached]")

    try:
        result = await fetch_data_layers(
            lat, lng, client=client, api_key=api_key, radius_m=radius_m
        )
    except SolarApiNotFound:
        await _write_cache(
            lat_q=lat_q,
            lng_q=lng_q,
            payload_kind=PAYLOAD_KIND_DATA_LAYERS,
            status="not_found",
            parsed_payload=None,
            raw_response=None,
            quality_used=None,
        )
        raise
    except (SolarApiError, SolarApiRateLimited):
        raise

    await _write_cache(
        lat_q=lat_q,
        lng_q=lng_q,
        payload_kind=PAYLOAD_KIND_DATA_LAYERS,
        status="ok",
        parsed_payload={
            "rgb_url": result.rgb_url,
            "imagery_quality": result.imagery_quality,
            "imagery_date": result.imagery_date,
        },
        raw_response=None,
        quality_used=result.imagery_quality,
    )
    return result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _read_cache(
    lat_q: float, lng_q: float, payload_kind: str
) -> dict[str, Any] | None:
    sb = get_service_client()
    try:
        res = await asyncio.to_thread(
            lambda: sb.table("solar_insights_cache")
            .select(
                "status, parsed_payload, raw_response, quality_used, expires_at"
            )
            .eq("lat_q", lat_q)
            .eq("lng_q", lng_q)
            .eq("payload_kind", payload_kind)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("solar_cache.read_failed", err=str(exc))
        return None

    rows = getattr(res, "data", None) or []
    if not rows:
        return None
    row = rows[0]

    expires_raw = row.get("expires_at")
    if expires_raw:
        try:
            expires_at = datetime.fromisoformat(
                str(expires_raw).replace("Z", "+00:00")
            )
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(tz=timezone.utc):
                return None
        except ValueError:
            pass

    return row


async def _write_cache(
    *,
    lat_q: float,
    lng_q: float,
    payload_kind: str,
    status: str,
    parsed_payload: dict[str, Any] | None,
    raw_response: dict[str, Any] | None,
    quality_used: str | None,
) -> None:
    sb = get_service_client()
    payload: dict[str, Any] = {
        "lat_q": lat_q,
        "lng_q": lng_q,
        "payload_kind": payload_kind,
        "status": status,
        "parsed_payload": parsed_payload,
        "raw_response": raw_response,
        "quality_used": quality_used,
    }
    try:
        await asyncio.to_thread(
            lambda: sb.table("solar_insights_cache")
            .upsert(payload, on_conflict="lat_q,lng_q,payload_kind")
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        # Cache write failures must not break the orchestrator. Log + continue.
        log.warning(
            "solar_cache.write_failed",
            err=str(exc),
            lat_q=lat_q,
            lng_q=lng_q,
            payload_kind=payload_kind,
        )


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _serialise_roof_insight(insight: RoofInsight) -> dict[str, Any]:
    """Convert a `RoofInsight` (slots dataclass with nested `SolarPanel`s)
    to a JSON-serialisable dict for storage in JSONB.

    `asdict` handles the nested panels list; we don't store `raw` here
    — it goes in `raw_response` on the cache row to keep the parsed
    payload readable.
    """

    panels = [
        {
            "lat": p.lat,
            "lng": p.lng,
            "orientation": p.orientation,
            "segment_azimuth_deg": p.segment_azimuth_deg,
            "yearly_energy_kwh": p.yearly_energy_kwh,
            "segment_index": p.segment_index,
        }
        for p in insight.panels
    ]
    return {
        "lat": insight.lat,
        "lng": insight.lng,
        "area_sqm": insight.area_sqm,
        "estimated_kwp": insight.estimated_kwp,
        "estimated_yearly_kwh": insight.estimated_yearly_kwh,
        "max_panel_count": insight.max_panel_count,
        "panel_capacity_w": insight.panel_capacity_w,
        "dominant_exposure": insight.dominant_exposure,
        "pitch_degrees": insight.pitch_degrees,
        "shading_score": insight.shading_score,
        "postal_code": insight.postal_code,
        "region_code": insight.region_code,
        "administrative_area": insight.administrative_area,
        "locality": insight.locality,
        "panels": panels,
        "panel_width_m": insight.panel_width_m,
        "panel_height_m": insight.panel_height_m,
    }


def _rehydrate_roof_insight(data: dict[str, Any]) -> RoofInsight:
    """Inverse of `_serialise_roof_insight`. Raises KeyError on schema drift."""

    panels = [
        SolarPanel(
            lat=p["lat"],
            lng=p["lng"],
            orientation=p["orientation"],
            segment_azimuth_deg=p["segment_azimuth_deg"],
            yearly_energy_kwh=p["yearly_energy_kwh"],
            segment_index=p["segment_index"],
        )
        for p in (data.get("panels") or [])
    ]
    return RoofInsight(
        lat=data["lat"],
        lng=data["lng"],
        area_sqm=data["area_sqm"],
        estimated_kwp=data["estimated_kwp"],
        estimated_yearly_kwh=data["estimated_yearly_kwh"],
        max_panel_count=data["max_panel_count"],
        panel_capacity_w=data["panel_capacity_w"],
        dominant_exposure=data["dominant_exposure"],
        pitch_degrees=data["pitch_degrees"],
        shading_score=data["shading_score"],
        postal_code=data.get("postal_code"),
        region_code=data.get("region_code"),
        administrative_area=data.get("administrative_area"),
        locality=data.get("locality"),
        raw={},  # not preserved — the parsed payload is sufficient for v2
        panels=panels,
        panel_width_m=data.get("panel_width_m", 1.045),
        panel_height_m=data.get("panel_height_m", 1.879),
    )
