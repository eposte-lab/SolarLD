"""L4 — Direct Solar API qualification (FLUSSO 1 v3).

Replaces the v2 ``level4_solar_gate.py`` (which used the 7-stage BIC
cascade to find a building first, then called Solar). v3 doesn't need
BIC: the Places coords from L1 are already on the capannone, so we
skip straight to ``solar_service.fetch_building_insight``.

Filters applied (from PRD §L4):

  MIN_AREA_M2          = 200
  MIN_KW_INSTALLABILE  = 60
  MIN_SUNSHINE_HOURS   = 1200

Cache: ``known_company_buildings`` (place_id-keyed after migration
0103) avoids re-paying ~$0.02 every cycle for the same business.

Cost: ~$0.02 per building_insights call. For 600 candidates surviving
L3, ~€10-12/cycle (vs €50-100 for the v2 BIC + Solar combo).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import geohash  # type: ignore[import-untyped]

from ...core.logging import get_logger
from ...core.supabase_client import get_service_client
from ...services.google_solar_service import (
    SolarApiError,
    SolarApiNotFound,
    SolarApiRateLimited,
    fetch_building_insight,
)
from .types_v3 import (
    FunnelV3Context,
    QualifiedCandidate,
    SolarQualified,
)

log = get_logger(__name__)


# Minimum thresholds — copied verbatim from PRD §L4. Identical for all
# tenants, demo or production: the funnel must reflect the customer's
# onboarding criteria and not be silently relaxed for any sub-population.
MIN_AREA_M2 = 200.0
MIN_KW_INSTALLABILE = 60.0
MIN_SUNSHINE_HOURS = 1200.0

# Cost in cents per Solar API call. ~$0.02 → 2 cents.
SOLAR_COST_CENTS = 2


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


async def _check_solar_cache(
    sb: Any, *, google_place_id: str
) -> dict[str, Any] | None:
    """Return cached Solar data for this place_id, or None.

    Migration 0103 reformulates ``known_company_buildings`` with a
    ``google_place_id UNIQUE`` column + ``solar_building_insights JSONB``.
    Until that ships, the lookup will fail silently and we'll always
    miss-cache.
    """
    try:
        res = (
            sb.table("known_company_buildings")
            .select("solar_building_insights, lat, lng")
            .eq("google_place_id", google_place_id)
            .maybe_single()
            .execute()
        )
        return res.data
    except Exception:  # noqa: BLE001 — column may not exist yet
        return None


async def _store_solar_cache(
    sb: Any,
    *,
    google_place_id: str,
    insights: dict[str, Any],
    lat: float,
    lng: float,
) -> None:
    try:
        sb.table("known_company_buildings").upsert(
            {
                "google_place_id": google_place_id,
                "solar_building_insights": insights,
                "lat": lat,
                "lng": lng,
            },
            on_conflict="google_place_id",
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.debug("level4_solar.cache_skipped", err=type(exc).__name__)


async def _persist_roof_and_link(
    sb: Any,
    *,
    tenant_id: str,
    scan_id: str,
    candidate_id: UUID,
    insight: Any,  # google_solar_service.RoofInsight
    google_place_id: str,
    sunshine_hours: float | None = None,
) -> UUID | None:
    """Insert a row into ``roofs`` and link from scan_candidates.roof_id.

    Also writes the solar metric columns (solar_kw_installable, solar_area_m2,
    solar_sunshine_hours, solar_panels_count) to scan_candidates so the
    /contatti KPI can aggregate them without joining roofs.

    Returns the new roof UUID, or None if the insert failed.
    """
    row: dict[str, Any] = {
        "tenant_id": tenant_id,
        "lat": insight.lat,
        "lng": insight.lng,
        "geohash": geohash.encode(insight.lat, insight.lng, precision=8),
        "data_source": "google_solar",
        "area_sqm": insight.area_sqm,
        "estimated_kwp": insight.estimated_kwp,
        "estimated_yearly_kwh": insight.estimated_yearly_kwh,
        "exposure": insight.dominant_exposure,
        "pitch_degrees": insight.pitch_degrees,
        "shading_score": insight.shading_score,
        "raw_data": insight.raw,
        "address": (
            (insight.locality or "") + " " + (insight.postal_code or "")
        ).strip()
        or None,
        "comune": insight.locality,
        "cap": insight.postal_code,
        "status": "identified",
    }

    # Snapshot ROI (kWp, savings, payback, capex, monthly curves) so the
    # dashboard, Creative Agent and preventivo PDF read from one source of
    # truth. v2 already does this in level4_solar_gate.py:_upsert_roof_and_subject;
    # v3 used to skip it, leaving roofs.derivations NULL → leads.roi_data {}
    # → all ROI KPIs blank in the lead detail UI. Best-effort: errors are
    # logged but do not block the qualify step.
    try:
        from ...services.roi_service import compute_full_derivations

        derivations = compute_full_derivations(
            estimated_kwp=insight.estimated_kwp,
            estimated_yearly_kwh=insight.estimated_yearly_kwh,
            roof_area_sqm=insight.area_sqm,
            panel_count=(
                len(insight.panels) if getattr(insight, "panels", None)
                else getattr(insight, "max_panel_count", None)
            ),
            panel_capacity_w=getattr(insight, "panel_capacity_w", None),
            panel_width_m=getattr(insight, "panel_width_m", None),
            panel_height_m=getattr(insight, "panel_height_m", None),
            subject_type="b2b",
            tenant_cost_assumptions=None,
            roi_target_years=None,
        )
        if derivations is not None:
            row["derivations"] = derivations
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "level4_solar.derivations_compute_failed",
            err=type(exc).__name__,
            msg=str(exc)[:200],
        )
    try:
        # Upsert by (tenant_id, geohash) — same building rediscovered in
        # subsequent scans returns the existing roof_id rather than 23505.
        res = (
            sb.table("roofs")
            .upsert(row, on_conflict="tenant_id,geohash")
            .execute()
        )
        roof_id = res.data[0]["id"] if res.data else None
        if not roof_id:
            # PostgREST upsert sometimes returns empty data on UPDATE path;
            # fall back to a SELECT by geohash to recover the existing id.
            existing = (
                sb.table("roofs")
                .select("id")
                .eq("tenant_id", tenant_id)
                .eq("geohash", row["geohash"])
                .limit(1)
                .execute()
            )
            roof_id = (existing.data or [{}])[0].get("id")
        if roof_id:
            sc_update: dict[str, Any] = {
                "id": str(candidate_id),
                "tenant_id": tenant_id,
                "scan_id": scan_id,
                "roof_id": roof_id,
                "solar_verdict": "accepted",
                "stage": 4,
                # Denormalise solar metrics so /contatti KPI avoids a join.
                "solar_kw_installable": insight.estimated_kwp,
                "solar_area_m2": insight.area_sqm,
                "solar_panels_count": insight.max_panel_count,
            }
            if sunshine_hours is not None:
                sc_update["solar_sunshine_hours"] = round(sunshine_hours, 1)
            sb.table("scan_candidates").upsert(
                sc_update,
                on_conflict="id",
            ).execute()
        return UUID(roof_id) if roof_id else None
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "level4_solar.persist_failed",
            err=type(exc).__name__,
            msg=str(exc)[:300],
        )
        return None


# ---------------------------------------------------------------------------
# Public agent
# ---------------------------------------------------------------------------


async def run_level4_solar_qualify(
    ctx: FunnelV3Context,
    candidates: list[QualifiedCandidate],
) -> list[SolarQualified]:
    """Per-candidate Solar API call + threshold filter.

    Survivors get a roof_id linked into scan_candidates and become
    SolarQualified objects with `solar_verdict='accepted'`. Rejected
    candidates are still returned with their verdict so the L5 prompt
    has the context.
    """
    if not candidates:
        return []

    sb = get_service_client()
    out: list[SolarQualified] = []
    api_calls = 0

    for qc in candidates:
        rec = qc.record
        place_id = rec.google_place_id

        # 1) Cache lookup
        cached = await _check_solar_cache(sb, google_place_id=place_id)
        insight = None
        if cached and cached.get("solar_building_insights"):
            try:
                from ...services.google_solar_service import (
                    _parse_building_insight_payload,
                )
                insight = _parse_building_insight_payload(
                    cached["solar_building_insights"]
                )
            except Exception:  # noqa: BLE001
                insight = None

        # 2) API call on cache miss
        if insight is None:
            try:
                insight = await fetch_building_insight(rec.lat, rec.lng)
                api_calls += 1
            except SolarApiNotFound:
                # solar_verdict CHECK only allows
                # {accepted, rejected_tech, no_building, api_error, skipped_below_gate}.
                out.append(_rejected(qc, "no_building"))
                _mark_verdict(
                    sb,
                    rec.candidate_id,
                    "no_building",
                    tenant_id=ctx.tenant_id,
                    scan_id=ctx.scan_id,
                )
                continue
            except (SolarApiRateLimited, SolarApiError) as exc:
                log.warning(
                    "level4_solar.api_error",
                    place_id=place_id,
                    err=type(exc).__name__,
                )
                out.append(_rejected(qc, "api_error"))
                _mark_verdict(
                    sb,
                    rec.candidate_id,
                    "api_error",
                    tenant_id=ctx.tenant_id,
                    scan_id=ctx.scan_id,
                )
                continue
            else:
                # Cache for next cycle
                await _store_solar_cache(
                    sb,
                    google_place_id=place_id,
                    insights=insight.raw,
                    lat=insight.lat,
                    lng=insight.lng,
                )

        # 3) Threshold filter — same production gate for every tenant.
        sunshine = insight.estimated_yearly_kwh / max(insight.estimated_kwp, 1.0)
        if (
            insight.area_sqm < MIN_AREA_M2
            or insight.estimated_kwp < MIN_KW_INSTALLABILE
            or sunshine < MIN_SUNSHINE_HOURS
        ):
            out.append(
                SolarQualified(
                    record=qc.record,
                    scraped=qc.scraped,
                    contact=qc.contact,
                    building_quality_score=qc.building_quality_score,
                    roof_id=None,
                    solar_verdict="rejected_tech",
                    solar_area_m2=insight.area_sqm,
                    solar_kw_installable=insight.estimated_kwp,
                    solar_panels_count=insight.max_panel_count,
                    solar_sunshine_hours=sunshine,
                )
            )
            # Persist the solar metrics on the candidate row even on rejection
            # so that the /contatti KPI and the requalify endpoint can read
            # them back without re-calling the Solar API.
            _mark_verdict(
                sb,
                rec.candidate_id,
                "rejected_tech",
                tenant_id=ctx.tenant_id,
                scan_id=ctx.scan_id,
                solar_area_m2=insight.area_sqm,
                solar_kw_installable=insight.estimated_kwp,
                solar_panels_count=insight.max_panel_count,
                solar_sunshine_hours=sunshine,
            )
            continue

        # 4) Accept — persist roof + link + solar columns on scan_candidates
        roof_id = await _persist_roof_and_link(
            sb,
            tenant_id=ctx.tenant_id,
            scan_id=ctx.scan_id,
            candidate_id=rec.candidate_id,
            insight=insight,
            google_place_id=place_id,
            sunshine_hours=sunshine,
        )
        out.append(
            SolarQualified(
                record=qc.record,
                scraped=qc.scraped,
                contact=qc.contact,
                building_quality_score=qc.building_quality_score,
                roof_id=roof_id,
                solar_verdict="accepted",
                solar_area_m2=insight.area_sqm,
                solar_kw_installable=insight.estimated_kwp,
                solar_panels_count=insight.max_panel_count,
                solar_sunshine_hours=sunshine,
            )
        )

    # 5) Cost accounting
    ctx.costs.add_solar(calls=api_calls, cost_cents=api_calls * SOLAR_COST_CENTS)

    accepted = sum(1 for r in out if r.solar_verdict == "accepted")
    log.info(
        "level4_solar.done",
        tenant_id=ctx.tenant_id,
        scanned=len(candidates),
        accepted=accepted,
        api_calls=api_calls,
        cost_cents=api_calls * SOLAR_COST_CENTS,
    )
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rejected(qc: QualifiedCandidate, verdict: str) -> SolarQualified:
    return SolarQualified(
        record=qc.record,
        scraped=qc.scraped,
        contact=qc.contact,
        building_quality_score=qc.building_quality_score,
        roof_id=None,
        solar_verdict=verdict,
    )


def _mark_verdict(
    sb: Any,
    candidate_id: UUID,
    verdict: str,
    *,
    tenant_id: str,
    scan_id: str,
    solar_kw_installable: float | None = None,
    solar_area_m2: float | None = None,
    solar_panels_count: int | None = None,
    solar_sunshine_hours: float | None = None,
) -> None:
    """Write the L4 verdict (and optionally solar metrics) to scan_candidates.

    Solar metrics are persisted even for rejected candidates so that:
      * The requalify endpoint can re-evaluate without re-calling the API.
      * Future "why rejected?" UI can show the actual values.
    """
    update: dict[str, Any] = {
        "id": str(candidate_id),
        "tenant_id": tenant_id,
        "scan_id": scan_id,
        "solar_verdict": verdict,
        "stage": 4,
    }
    if solar_kw_installable is not None:
        update["solar_kw_installable"] = round(solar_kw_installable, 2)
    if solar_area_m2 is not None:
        update["solar_area_m2"] = round(solar_area_m2, 2)
    if solar_panels_count is not None:
        update["solar_panels_count"] = solar_panels_count
    if solar_sunshine_hours is not None:
        update["solar_sunshine_hours"] = round(solar_sunshine_hours, 1)
    try:
        sb.table("scan_candidates").upsert(update, on_conflict="id").execute()
    except Exception as exc:  # noqa: BLE001
        log.debug("level4_solar.mark_verdict_failed", err=type(exc).__name__)
