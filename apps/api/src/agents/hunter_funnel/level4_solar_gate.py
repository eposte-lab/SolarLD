"""Level 4 — Solar gate on the top N% by proxy score.

Takes the `ScoredCandidate`s from L3 (sorted desc) and calls Google Solar
on only the top `solar_gate_pct` fraction (default 20%, min `solar_gate_min_candidates`).
Survivors of the technical filter graduate into `roofs` + `subjects` rows
— the first time the funnel produces anything the outreach pipeline sees.

This is the expensive rung (~€0.02 per findClosest + ~€0.005 per Mapbox
forward-geocode when Atoka didn't give us coords). Gating here is the
key to the v2 cost improvement: on a 5000-candidate scan we run Solar on
~1000 instead of all 5000, saving ~€80 per tenant per scan.

Upsert keys mirror the legacy b2b_precision path so the downstream agents
(Scoring, Creative, Outreach) see the same `subjects` shape regardless of
which mode produced them.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import geohash  # type: ignore[import-untyped]
import httpx

from ...core.logging import get_logger
from ...core.queue import enqueue
from ...core.supabase_client import get_service_client
from ...models.enums import RoofDataSource, RoofStatus
from ...services.google_solar_service import (
    COST_PER_CALL_CENTS as SOLAR_COST_PER_CALL_CENTS,
    RoofInsight,
    SolarApiError,
    SolarApiNotFound,
    fetch_building_insight,
)
from ...services.hunter import classify_roof
from ...services.mapbox_service import (
    ForwardGeocodeResult,
    MapboxError,
    forward_geocode,
)
from ...services.operating_site_resolver import (
    OperatingSite,
    resolve_operating_site,
)
from ...services.tenant_config_service import TechnicalFilters
from .types import FunnelContext, ScoredCandidate

log = get_logger(__name__)

# Cap on concurrent Solar calls. Matches the legacy _SOLAR_CONCURRENCY in
# hunter.py — Google's quota is 100 QPS, we stay well under it.
_SOLAR_CONCURRENCY = 8

# Forward-geocode is rate-limited per Mapbox account; our effective ceiling
# is 600 requests/minute which leaves room for the dashboard's own usage.
_GEOCODE_CONCURRENCY = 6

# Per-call cost (cents) for Mapbox forward geocode — tiny but we track it
# because many candidates need it (Atoka often returns address without
# coords in the base tier).
_MAPBOX_GEOCODE_COST_CENTS = 1


async def run_level4(
    ctx: FunnelContext, scored: list[ScoredCandidate]
) -> int:
    """Run Solar on the top-N candidates and upsert surviving leads.

    Returns the number of qualified leads (roofs that passed the technical
    filters). The caller maps this onto `HunterOutput.roofs_discovered`.
    """
    if not scored:
        return 0

    # Assume the input is already score-desc sorted; re-sort defensively.
    scored_sorted = sorted(scored, key=lambda s: s.score, reverse=True)

    # Gate: top pct, with a floor to avoid "5 candidates × 20% = 1" dead-funnels.
    n_gate = max(
        ctx.solar_gate_min_candidates,
        int(len(scored_sorted) * ctx.solar_gate_pct),
    )
    n_gate = min(n_gate, len(scored_sorted))
    gated = scored_sorted[:n_gate]
    skipped = scored_sorted[n_gate:]

    # Mark the skipped ones so the dashboard waterfall shows a correct
    # "L4 skipped" bucket rather than just missing them.
    _mark_skipped_below_gate(skipped)

    log.info(
        "funnel_l4_gate",
        extra={
            "tenant_id": ctx.tenant_id,
            "scan_id": ctx.scan_id,
            "total_scored": len(scored_sorted),
            "gated_for_solar": n_gate,
            "min_score": gated[-1].score if gated else None,
            "max_score": gated[0].score if gated else None,
        },
    )

    # Fan-out Solar + forward-geocode with bounded concurrency.
    solar_sem = asyncio.Semaphore(_SOLAR_CONCURRENCY)
    geo_sem = asyncio.Semaphore(_GEOCODE_CONCURRENCY)

    filters = ctx.config.technical_b2b
    qualified = 0

    async with httpx.AsyncClient(timeout=20.0) as http_client:
        tasks = [
            _gate_one(
                cand,
                ctx=ctx,
                filters=filters,
                http_client=http_client,
                solar_sem=solar_sem,
                geo_sem=geo_sem,
            )
            for cand in gated
        ]
        for coro in asyncio.as_completed(tasks):
            result = await coro
            if result == "qualified":
                qualified += 1
                ctx.costs.mark_lead_qualified()

    return qualified


# ---------------------------------------------------------------------------
# Per-candidate Solar → filter → upsert
# ---------------------------------------------------------------------------


async def _gate_one(
    cand: ScoredCandidate,
    *,
    ctx: FunnelContext,
    filters: TechnicalFilters,
    http_client: httpx.AsyncClient,
    solar_sem: asyncio.Semaphore,
    geo_sem: asyncio.Semaphore,
) -> str:
    """Process one candidate through Solar. Returns one of:
       'qualified' | 'rejected_tech' | 'no_building' | 'api_error' | 'skipped_no_coords'
    """
    site = await _resolve_coords(
        cand,
        ctx=ctx,
        http_client=http_client,
        geo_sem=geo_sem,
    )
    lat, lng = site.lat, site.lng
    if lat is None or lng is None:
        _mark_verdict(cand.candidate_id, "api_error", roof_id=None)
        return "skipped_no_coords"

    try:
        async with solar_sem:
            insight = await fetch_building_insight(lat, lng, client=http_client)
    except SolarApiNotFound:
        # No modelled building here — still count the call against Solar budget
        # (Google charges for the 404 attempt).
        ctx.costs.add_solar(calls=1, cost_cents=SOLAR_COST_PER_CALL_CENTS)
        _mark_verdict(cand.candidate_id, "no_building", roof_id=None)
        return "no_building"
    except SolarApiError as exc:
        log.warning(
            "l4_solar_error",
            extra={
                "vat": cand.profile.vat_number,
                "lat": lat,
                "lng": lng,
                "err": str(exc),
            },
        )
        ctx.costs.add_solar(calls=1, cost_cents=SOLAR_COST_PER_CALL_CENTS)
        _mark_verdict(cand.candidate_id, "api_error", roof_id=None)
        return "api_error"

    ctx.costs.add_solar(calls=1, cost_cents=SOLAR_COST_PER_CALL_CENTS)

    verdict_accepted, reason = _apply_filters(insight, filters)
    classification = classify_roof(insight)

    roof_id, subject_id = _upsert_roof_and_subject(
        ctx=ctx,
        cand=cand,
        insight=insight,
        lat=lat,
        lng=lng,
        accepted=verdict_accepted,
        reason=reason,
        classification=classification.value,
        site=site,
    )

    verdict = "accepted" if verdict_accepted else "rejected_tech"
    _mark_verdict(cand.candidate_id, verdict, roof_id=roof_id)

    # Enqueue Email Extraction Agent for every accepted candidate.
    #
    # EmailExtractionAgent runs Phase 2 (offline filters) + Phase 3 (email
    # extraction + GDPR audit) and then enqueues scoring_task itself.
    #
    # For non-pilot tenants (pipeline_v2_pilot=false), EmailExtractionAgent
    # is a transparent pass-through that immediately forwards to scoring_task
    # — so legacy behaviour is unchanged until the pilot is enabled.
    #
    # Idempotent: deterministic job_id collapses duplicate enqueues on retry.
    if verdict_accepted and roof_id is not None and subject_id is not None:
        try:
            from ...agents.email_extraction import build_candidate_dict_from_profile

            await enqueue(
                "email_extraction_task",
                {
                    "tenant_id": ctx.tenant_id,
                    "subject_id": str(subject_id),
                    "roof_id": str(roof_id),
                    "territory_id": ctx.territory_id,
                    "candidate": build_candidate_dict_from_profile(
                        cand.profile, cand.enrichment
                    ),
                    # Pass a lightweight territory dict (provinces + caps)
                    # for the sede_operativa offline filter. Permissive when empty.
                    "territory": {
                        "provinces": (ctx.territory or {}).get("provinces") or [],
                        "caps": (ctx.territory or {}).get("caps") or [],
                    },
                },
                job_id=f"email_extraction:{ctx.tenant_id}:{roof_id}:{subject_id}",
            )
        except Exception as exc:  # noqa: BLE001
            # Enqueue failure must not break the funnel — the manual
            # POST /v1/leads/score-pending-subjects endpoint is the fallback.
            log.warning(
                "l4_email_extraction_enqueue_failed",
                extra={
                    "vat": cand.profile.vat_number,
                    "roof_id": str(roof_id),
                    "subject_id": str(subject_id),
                    "err": str(exc),
                },
            )

    return "qualified" if verdict_accepted else "rejected_tech"


async def _resolve_coords(
    cand: ScoredCandidate,
    *,
    ctx: FunnelContext,
    http_client: httpx.AsyncClient,
    geo_sem: asyncio.Semaphore,
) -> OperatingSite:
    """Run the 4-tier operating-site cascade.

    Priority is: Atoka sede_operativa → website scrape → Google Places →
    Mapbox HQ centroid. The legacy "Atoka HQ coords" early-return is
    preserved when the profile has *only* HQ coords (no operating-site
    record): we wrap them in an ``OperatingSite`` with ``source='atoka'``
    so the downstream subject row records the provenance honestly.

    Returning a single object (instead of a bare tuple) lets the caller
    persist sede_operativa_* fields and surface a "Sede operativa: X"
    badge on the dashboard without re-resolving.
    """
    p = cand.profile

    # If Atoka *only* gave us HQ coords (no operating-site flag), keep the
    # legacy fast-path: skip the cascade and skip extra HTTP work. We tag
    # the source as ``mapbox_hq`` because that's the centroid behaviour
    # the dashboard badge will reflect.
    if (
        p.sede_operativa_lat is None
        and p.sede_operativa_lng is None
        and p.hq_lat is not None
        and p.hq_lng is not None
    ):
        return OperatingSite(
            lat=p.hq_lat,
            lng=p.hq_lng,
            address=p.hq_address,
            cap=p.hq_cap,
            city=p.hq_city,
            province=p.hq_province,
            source="mapbox_hq",
            confidence="low",
        )

    # Geo concurrency cap — the cascade may issue several HTTP calls
    # (website fetch + forward_geocode + Places). Hold the semaphore for
    # the whole resolver so we never exceed our Mapbox/Places quotas.
    cost_meter: dict[str, int] = {}
    async with geo_sem:
        site = await resolve_operating_site(
            profile=p,
            legal_name=p.legal_name or "",
            website_domain=p.website_domain,
            hq_address=p.hq_address,
            hq_city=p.hq_city,
            hq_province=p.hq_province,
            http_client=http_client,
            cost_meter=cost_meter,
        )

    if cost_meter.get("google_places"):
        # The cost-tracker on FunnelContext.costs only knows about Mapbox
        # / Solar; record Places spend on the raw costs dict so the
        # nightly rollup picks it up.
        ctx.costs.add_mapbox(cost_cents=cost_meter["google_places"])
    # Tier 4 (mapbox_hq) implies one geocode call.
    if site.source in {"mapbox_hq", "website_scrape"}:
        ctx.costs.add_mapbox(cost_cents=_MAPBOX_GEOCODE_COST_CENTS)
    return site


def _apply_filters(
    insight: RoofInsight, filters: TechnicalFilters
) -> tuple[bool, str | None]:
    """Return (accepted, reason_if_rejected). Mirrors _apply_config_filters
    in hunter.py so the v2 path behaves identically to v1 for the roof
    verdict — we want existing tenants to get the same accept/reject ruling
    regardless of which scan mode ran.
    """
    if insight.area_sqm < filters.min_area_sqm:
        return False, f"area<{filters.min_area_sqm}m²"
    if insight.estimated_kwp < filters.min_kwp:
        return False, f"kwp<{filters.min_kwp}"
    if insight.shading_score < (1.0 - filters.max_shading):
        return False, f"shading={insight.shading_score:.2f}"
    if insight.dominant_exposure == "N":
        return False, f"exposure={insight.dominant_exposure}"
    return True, None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _upsert_roof_and_subject(
    *,
    ctx: FunnelContext,
    cand: ScoredCandidate,
    insight: RoofInsight,
    lat: float,
    lng: float,
    accepted: bool,
    reason: str | None,
    classification: str,
    site: OperatingSite | None = None,
) -> tuple[UUID | None, UUID | None]:
    """Upsert `roofs` (keyed on tenant_id + geohash) and `subjects`
    (one per roof). Returns (roof_id, subject_id). subject_id is None
    when the roof was rejected (no subject created) or on DB error.
    """
    sb = get_service_client()
    gh = geohash.encode(insight.lat or lat, insight.lng or lng, precision=8)

    row: dict[str, Any] = {
        "tenant_id": ctx.tenant_id,
        "territory_id": ctx.territory_id,
        "lat": insight.lat or lat,
        "lng": insight.lng or lng,
        "geohash": gh,
        "address": cand.profile.hq_address,
        "cap": cand.profile.hq_cap,
        "comune": cand.profile.hq_city,
        "provincia": cand.profile.hq_province,
        "area_sqm": insight.area_sqm,
        "estimated_kwp": insight.estimated_kwp,
        "estimated_yearly_kwh": insight.estimated_yearly_kwh,
        "exposure": insight.dominant_exposure,
        "pitch_degrees": insight.pitch_degrees,
        "shading_score": insight.shading_score,
        "data_source": RoofDataSource.GOOGLE_SOLAR.value,
        "classification": classification,
        "status": (
            RoofStatus.DISCOVERED if accepted else RoofStatus.REJECTED
        ).value,
        "scan_cost_cents": SOLAR_COST_PER_CALL_CENTS,
        "raw_data": {
            "solar": insight.raw,
            "funnel_v2": {
                "candidate_id": str(cand.candidate_id),
                "proxy_score": cand.score,
                "score_reasons": cand.reasons,
                "score_flags": cand.flags,
                "filter_reason": reason,
            },
        },
    }

    try:
        up = sb.table("roofs").upsert(row, on_conflict="tenant_id,geohash").execute()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "l4_roof_upsert_failed",
            extra={"vat": cand.profile.vat_number, "err": str(exc)},
        )
        return None, None

    roof_id = (up.data[0]["id"] if up.data else None)
    if roof_id is None:
        return None, None

    subject_id: str | None = None

    # Subject — create only for accepted roofs, and only when we don't
    # already have one (re-scan idempotency).
    if accepted:
        try:
            existing = (
                sb.table("subjects")
                .select("id")
                .eq("tenant_id", ctx.tenant_id)
                .eq("roof_id", roof_id)
                .limit(1)
                .execute()
            )
            if existing.data:
                subject_id = existing.data[0]["id"]
            else:
                subject_payload: dict[str, Any] = {
                    "tenant_id": ctx.tenant_id,
                    "roof_id": roof_id,
                    "type": "b2b",
                    "business_name": cand.profile.legal_name,
                    "business_website": cand.enrichment.website,
                    # Phone resolved upstream by L2: prefer Atoka's
                    # raw bundle (free, ~70% coverage); fall back
                    # to website scrape regex hit (free); else NULL.
                    # The `*_source` column lets the UI badge the
                    # provenance and ops audit data quality.
                    "decision_maker_phone": cand.enrichment.phone,
                    "decision_maker_phone_source": (
                        "atoka" if cand.enrichment.phone else None
                    ),
                    "vat_number": cand.profile.vat_number,
                    "ateco_code": cand.profile.ateco_code,
                    "employees": cand.profile.employees,
                    "yearly_revenue_cents": cand.profile.yearly_revenue_cents,
                    "raw_data": {
                        "source": "funnel_v2",
                        "decision_maker_name": cand.profile.decision_maker_name,
                        "decision_maker_role": cand.profile.decision_maker_role,
                        "linkedin_url": cand.profile.linkedin_url,
                        "proxy_score": cand.score,
                    },
                }
                # Stamp the operating-site cascade outcome onto the
                # subject row so the dashboard can show provenance and
                # the next pipeline run can short-circuit re-resolution.
                if site is not None and site.source != "unresolved":
                    subject_payload.update(
                        {
                            "sede_operativa_address": site.address,
                            "sede_operativa_cap": site.cap,
                            "sede_operativa_city": site.city,
                            "sede_operativa_province": site.province,
                            "sede_operativa_lat": site.lat,
                            "sede_operativa_lng": site.lng,
                            "sede_operativa_source": site.source,
                            # Persist confidence alongside source so the
                            # CreativeAgent gate + dashboard roof badge
                            # can read it back. Mapping is identical to
                            # the one in operating_site_resolver.
                            "sede_operativa_confidence": site.confidence,
                        }
                    )
                ins = sb.table("subjects").insert(subject_payload).execute()
                if ins.data:
                    subject_id = ins.data[0]["id"]
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "l4_subject_insert_failed",
                extra={"vat": cand.profile.vat_number, "err": str(exc)},
            )

    return (
        UUID(str(roof_id)) if roof_id else None,
        UUID(str(subject_id)) if subject_id else None,
    )


def _mark_verdict(
    candidate_id: UUID, verdict: str, *, roof_id: UUID | None
) -> None:
    sb = get_service_client()
    try:
        update: dict[str, Any] = {
            "solar_verdict": verdict,
            "stage": 4,
        }
        if roof_id is not None:
            update["roof_id"] = str(roof_id)
        sb.table("scan_candidates").update(update).eq(
            "id", str(candidate_id)
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.debug(
            "l4_mark_verdict_failed",
            extra={"candidate_id": str(candidate_id), "err": str(exc)},
        )


def _mark_skipped_below_gate(skipped: list[ScoredCandidate]) -> None:
    """Bulk-mark the L3 candidates that didn't make the L4 gate so the
    dashboard waterfall shows them in the 'below gate' bucket.
    """
    if not skipped:
        return
    sb = get_service_client()
    ids = [str(c.candidate_id) for c in skipped]
    try:
        sb.table("scan_candidates").update(
            {"solar_verdict": "skipped_below_gate", "stage": 4}
        ).in_("id", ids).execute()
    except Exception as exc:  # noqa: BLE001
        log.debug("l4_mark_skipped_failed", err=str(exc))
