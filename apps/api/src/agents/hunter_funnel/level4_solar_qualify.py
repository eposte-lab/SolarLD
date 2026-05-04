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


# Minimum thresholds — copied verbatim from PRD §L4.
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
    candidate_id: UUID,
    insight: Any,  # google_solar_service.RoofInsight
    google_place_id: str,
) -> UUID | None:
    """Insert a row into ``roofs`` and link from scan_candidates.roof_id.

    Returns the new roof UUID, or None if the insert failed.
    """
    row = {
        "tenant_id": tenant_id,
        "lat": insight.lat,
        "lng": insight.lng,
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
        "status": "active",
    }
    try:
        res = sb.table("roofs").insert(row).execute()
        roof_id = res.data[0]["id"] if res.data else None
        if roof_id:
            sb.table("scan_candidates").upsert(
                {
                    "id": str(candidate_id),
                    "roof_id": roof_id,
                    "solar_verdict": "accepted",
                    "stage": 4,
                },
                on_conflict="id",
            ).execute()
        return UUID(roof_id) if roof_id else None
    except Exception as exc:  # noqa: BLE001
        log.warning("level4_solar.persist_failed", err=type(exc).__name__)
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
                out.append(_rejected(qc, "no_solar_data"))
                _mark_verdict(sb, rec.candidate_id, "no_solar_data")
                continue
            except (SolarApiRateLimited, SolarApiError) as exc:
                log.warning(
                    "level4_solar.api_error",
                    place_id=place_id,
                    err=type(exc).__name__,
                )
                out.append(_rejected(qc, "api_error"))
                _mark_verdict(sb, rec.candidate_id, "api_error")
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

        # 3) Threshold filter
        sunshine = insight.estimated_yearly_kwh / max(insight.estimated_kwp, 1.0)
        if (
            insight.area_sqm < MIN_AREA_M2
            or insight.estimated_kwp < MIN_KW_INSTALLABILE
            or sunshine < MIN_SUNSHINE_HOURS
        ):
            verdict = (
                "rejected_tech"
                if insight.area_sqm < MIN_AREA_M2 or insight.estimated_kwp < MIN_KW_INSTALLABILE
                else "rejected_tech"
            )
            out.append(
                SolarQualified(
                    record=qc.record,
                    scraped=qc.scraped,
                    contact=qc.contact,
                    building_quality_score=qc.building_quality_score,
                    roof_id=None,
                    solar_verdict=verdict,
                    solar_area_m2=insight.area_sqm,
                    solar_kw_installable=insight.estimated_kwp,
                    solar_panels_count=insight.max_panel_count,
                    solar_sunshine_hours=sunshine,
                )
            )
            _mark_verdict(sb, rec.candidate_id, verdict)
            continue

        # 4) Accept — persist roof + link
        roof_id = await _persist_roof_and_link(
            sb,
            tenant_id=ctx.tenant_id,
            candidate_id=rec.candidate_id,
            insight=insight,
            google_place_id=place_id,
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


def _mark_verdict(sb: Any, candidate_id: UUID, verdict: str) -> None:
    try:
        sb.table("scan_candidates").upsert(
            {
                "id": str(candidate_id),
                "solar_verdict": verdict,
                "stage": 4,
            },
            on_conflict="id",
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.debug("level4_solar.mark_verdict_failed", err=type(exc).__name__)
