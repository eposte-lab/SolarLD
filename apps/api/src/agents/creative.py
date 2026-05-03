"""Creative Agent — Google Solar data + AI photoreal panel paint → ROI → lead assets.

Pipeline (rendering-v2):

    lead_id
        ↓
    load lead + roof + subject + tenant (brand)
        ↓
    idempotency: if lead.rendering_image_url already set and not force → skip
        ↓
    fetch Google Solar buildingInsights (panel COUNT, kWp, primary azimuth)
        + dataLayers (high-res RGB aerial GeoTIFF)
        ↓
    solar_rendering_service.render_before_only()
          → BEFORE frame: real Google aerial crop, 1536×1536, 10 cm/px
            (no panels drawn — the photo is left untouched)
        ↓
    upload before.png to Supabase Storage
        ↓
    ai_panel_paint_service.paint_panels_on_aerial(before_url, panel_count, …)
          → Gemini 2.5 Flash Image (Replicate google/nano-banana) edits the
            real aerial: adds photoreal monocrystalline panels on the visible
            roof, preserves everything else pixel-perfect
          → AFTER frame: real-photo-with-real-looking-panels (1536²)
        ↓
    upload after.png to Supabase Storage
        ↓
    compute_roi(kwp, yearly_kwh, subject_type)  → leads.roi_data
        ↓
    Remotion sidecar: render_transition(before_url, after_url, roi)
          → Kling 1.6-Pro animates the panel-by-panel reveal between the
            two real-photo frames, with ambient motion (cars, soft cloud
            shadows, leaf rustle) and ROI stats overlaid in the final 2 s
          → MP4 + GIF (720×720 @ 15 fps)
        ↓
    UPDATE leads SET rendering_image_url, rendering_video_url, rendering_gif_url, roi_data
    UPDATE roofs.status = 'rendered'
        ↓
    emit lead.rendered event

Why we replaced the old PIL-rectangle path: the deterministic geometric
draw produced flat blue cuboids that looked "pasted" on the roof, and
fed Kling a fake end_image so the GIF inherited the same wrongness.
The Solar API still drives PANEL DATA (count, kWp, dominant azimuth) —
those numbers shape the AI prompt, the ROI, and the filtering. Only
the pixel rendering moved off PIL onto the AI engine.

Degradation:
  * Google Solar 404 / no building → skip_reason, ROI persisted, outreach
    continues without rendering.
  * Google Solar API key missing → same graceful skip.
  * AI paint failure (Replicate down, OOM, timeout) → before uploaded only,
    Kling skipped. Email falls back to static aerial.
  * Kling sidecar failure → before/after committed, MP4/GIF skipped, email
    falls back to static after image.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from ..core.config import settings
from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from ..models.enums import RoofStatus
from ..services.ai_panel_paint_service import (
    AiPaintError,
    paint_panels_on_aerial,
)
from ..services.google_solar_service import (
    SolarApiError,
    SolarApiNotFound,
    fetch_building_insight,
)
from ..services.osm_building_service import find_nearest_building
from ..services.remotion_service import (
    RemotionError,
    RenderTransitionInput,
    render_transition,
)
from ..services.roi_service import compute_roi
from ..services.solar_rendering_service import (
    SolarRenderingError,
    render_before_after,
    render_before_only,
)
from ..services.storage_service import upload_bytes
from .base import AgentBase

log = get_logger(__name__)

RENDERINGS_BUCKET = "renderings"


class CreativeInput(BaseModel):
    tenant_id: str
    lead_id: str
    force: bool = Field(
        default=False,
        description=(
            "Re-run even when rendering_image_url is already populated. "
            "The /leads/:id/regenerate-rendering route sets this true."
        ),
    )


class CreativeOutput(BaseModel):
    lead_id: str
    before_url: str | None = None
    after_url: str | None = None
    video_url: str | None = None
    gif_url: str | None = None
    roi_data: dict[str, Any] = Field(default_factory=dict)
    skipped: bool = False
    reason: str | None = None


class CreativeAgent(AgentBase[CreativeInput, CreativeOutput]):
    name = "agent.creative"

    async def execute(self, payload: CreativeInput) -> CreativeOutput:
        sb = get_service_client()

        # 1) Load lead, then the roof + subject + tenant branding in parallel-ish
        lead = _load_single(sb, "leads", payload.lead_id, payload.tenant_id)
        if not lead:
            raise ValueError(f"lead {payload.lead_id} not found")

        roof = _load_single(sb, "roofs", lead["roof_id"], payload.tenant_id)
        subject = _load_single(sb, "subjects", lead["subject_id"], payload.tenant_id)
        if not roof or not subject:
            raise ValueError(
                f"lead {payload.lead_id} missing roof or subject rows"
            )

        tenant_res = (
            sb.table("tenants")
            .select(
                "id, business_name, brand_primary_color, brand_logo_url, "
                # cost_assumptions threaded into compute_full_derivations
                # so the OSM-snap re-compute below uses the tenant's
                # specific €/kWp / grid tariff overrides when configured.
                "cost_assumptions"
            )
            .eq("id", payload.tenant_id)
            .single()
            .execute()
        )
        tenant_row = tenant_res.data or {}

        # 2) Idempotency guard
        if lead.get("rendering_image_url") and not payload.force:
            return CreativeOutput(
                lead_id=payload.lead_id,
                before_url=None,
                after_url=lead.get("rendering_image_url"),
                roi_data=lead.get("roi_data") or {},
                skipped=True,
                reason="already_rendered",
            )

        # 3) ROI first — it's pure and independent of the image path, so we
        # can persist useful data even if the rendering step is skipped.
        roi = compute_roi(
            estimated_kwp=roof.get("estimated_kwp"),
            estimated_yearly_kwh=roof.get("estimated_yearly_kwh"),
            subject_type=subject.get("type") or "unknown",
        )
        roi_jsonb = roi.to_jsonb() if roi else {}

        # 4) Fetch Solar API data + real aerial → AI-paint photoreal panels.
        # Non-fatal: missing coords / Solar key / Replicate token → skip
        # rendering, ROI still persisted, outreach continues without images.
        before_url: str | None = None
        after_url: str | None = None
        skipped_reason: str | None = None

        lat = _to_float(roof.get("lat"))
        lng = _to_float(roof.get("lng"))

        # Operating-site confidence gate.
        #
        # The 4-tier resolver (apps/api/src/services/operating_site_resolver.py)
        # tags the subject with a confidence bucket: ``high`` (Atoka match
        # confirmed), ``medium`` (website scrape or Google Places),
        # ``low`` (Mapbox HQ centroid — almost always wrong for B2B leads
        # whose registered office is the accountant or a notary), ``none``
        # (cascade fully failed). Rendering on a low-confidence point is
        # the exact failure mode that drove the user to flag panels-on-the-
        # wrong-rooftop on the demo call: Solar API picks the closest
        # building to the centroid, which is rarely the real one.
        #
        # We only block the render — the email still goes out with the
        # static fallback image. Skipping silently here used to mean the
        # operator never noticed the wrong roof until the prospect did.
        roof_confidence = (subject.get("sede_operativa_confidence") or "").lower()
        roof_source = (subject.get("sede_operativa_source") or "").lower()

        if lat is None or lng is None:
            skipped_reason = "missing_coords"
        elif roof_confidence in {"low", "none"} or roof_source in {
            "mapbox_hq",
            "unresolved",
        }:
            skipped_reason = f"roof_confidence_too_low:{roof_source or 'unknown'}"
            log.warning(
                "creative.roof_low_confidence",
                lead_id=payload.lead_id,
                tenant_id=payload.tenant_id,
                lat=lat,
                lng=lng,
                roof_source=roof_source or None,
                roof_confidence=roof_confidence or None,
            )
        elif not settings.google_solar_api_key and not settings.google_solar_mock_mode:
            skipped_reason = "solar_api_key_not_configured"
            log.warning("creative.solar_api_key_missing", lead_id=payload.lead_id)
        elif (
            not settings.replicate_api_token
            and not settings.creative_skip_replicate
        ):
            # No Replicate token AND no opt-in to the offline path
            # → skip render. (When CREATIVE_SKIP_REPLICATE=true the
            # offline path drives both the after-image and skips
            # video render, so the missing token is irrelevant.)
            skipped_reason = "replicate_token_not_configured"
            log.warning(
                "creative.replicate_token_missing", lead_id=payload.lead_id
            )
        else:
            # Single-source-of-truth gate for the OSM snap retry below.
            #
            # The legacy snap behaviour was: on Solar 404, find the
            # nearest OSM building polygon within 60 m and re-fetch
            # Solar there. That made sense when the cascade was the
            # 4-tier legacy resolver and Atoka could be 30-80 m off
            # from the actual rooftop — the snap was a corrective
            # nudge.
            #
            # With the BIC live, the upstream cascade has already
            # done OSM snapping internally and (when low confidence)
            # the operator has manually clicked the right capannone
            # via the picker. CreativeAgent then re-doing an
            # autonomous "nearest building within 60 m" snap on
            # Solar 404 can move the coords away from the
            # user-confirmed building, producing a render of the
            # neighbour's roof — exactly the failure mode the
            # operator just resolved by clicking the picker.
            #
            # Rule: only autonomous-snap when the coords source is a
            # legacy unverified value (mapbox_hq centroid, or no
            # source recorded at all). For every other source —
            # user_confirmed, atoka high-confidence, vision,
            # google_places, osm_snap — trust the upstream resolution
            # and let Solar 404 propagate to the static-fallback
            # branch.
            _trusted_sources = {
                "user_confirmed",
                "user_pick",
                "manual",
                "atoka",
                "vision",
                "google_places",
                "osm_snap",
                "website_scrape",
            }
            allow_osm_snap = roof_source not in _trusted_sources
            try:
                # Re-fetch buildingInsights to get the full solarPanels list
                # (Hunter L4 already called this but didn't persist panel
                # geometry). Cost: ~$0.02 (buildingInsights) + ~$0.03
                # (dataLayers) per lead.
                #
                # Solar 404 retry path
                # ────────────────────
                # When Solar returns 404 the geocoded coordinate doesn't
                # sit on a building it knows about. For trusted sources
                # (user-confirmed picker click, BIC high-confidence) we
                # propagate the 404 instead of snapping — moving the
                # render away from the operator's confirmed building
                # would defeat the whole point of the picker UX.
                async with httpx.AsyncClient(timeout=30.0) as http:
                    try:
                        insight = await fetch_building_insight(
                            lat, lng, client=http
                        )
                    except SolarApiNotFound:
                        if not allow_osm_snap:
                            log.info(
                                "creative.solar_404_no_snap",
                                lead_id=payload.lead_id,
                                lat=lat,
                                lng=lng,
                                roof_source=roof_source,
                                note=(
                                    "Solar has no imagery for the trusted "
                                    "coords; not snapping to a neighbour. "
                                    "Render will fall back to static aerial."
                                ),
                            )
                            raise
                        # Bound the OSM snap with an asyncio timeout so a
                        # slow Overpass mirror can't wedge the creative
                        # step. ``find_nearest_building`` already returns
                        # None on its own internal failures; the wait_for
                        # here is the belt-and-braces upper bound.
                        import asyncio
                        try:
                            snap = await asyncio.wait_for(
                                find_nearest_building(
                                    lat, lng, max_distance_m=80, client=http
                                ),
                                timeout=15.0,
                            )
                        except (asyncio.TimeoutError, Exception) as snap_exc:  # noqa: BLE001
                            log.warning(
                                "creative.osm_snap_failed",
                                lead_id=payload.lead_id,
                                err_type=type(snap_exc).__name__,
                                err=str(snap_exc)[:160],
                            )
                            snap = None
                        if snap is None or snap.distance_m > 60:
                            log.info(
                                "creative.osm_snap_unavailable",
                                lead_id=payload.lead_id,
                                lat=lat,
                                lng=lng,
                                snap_distance_m=(
                                    round(snap.distance_m, 1) if snap else None
                                ),
                            )
                            raise
                        log.info(
                            "creative.osm_snap_retry",
                            lead_id=payload.lead_id,
                            from_lat=lat,
                            from_lng=lng,
                            to_lat=snap.lat,
                            to_lng=snap.lng,
                            distance_m=round(snap.distance_m, 1),
                            osm_id=snap.osm_id,
                        )
                        # Update the working coords so all downstream
                        # rendering (before image crop, panel-paint
                        # prompt, telemetry) uses the snapped point.
                        lat, lng = snap.lat, snap.lng
                        insight = await fetch_building_insight(
                            lat, lng, client=http
                        )
                        # Sync the persisted roof row with the snapped
                        # building's coords + Solar values. Without
                        # this the email body would still show the
                        # ORIGINAL building's kWp/area (or the demo
                        # route's median fallback) while the rendered
                        # image shows the snapped neighbour — exactly
                        # the email-vs-render mismatch the operator
                        # warned about.
                        try:
                            import geohash as _gh
                            new_geohash = _gh.encode(lat, lng, precision=9)
                            from ..services.roi_service import (
                                compute_full_derivations,
                            )
                            new_derivations = compute_full_derivations(
                                estimated_kwp=insight.estimated_kwp,
                                estimated_yearly_kwh=insight.estimated_yearly_kwh,
                                roof_area_sqm=insight.area_sqm,
                                panel_count=(
                                    len(insight.panels)
                                    if insight.panels
                                    else insight.max_panel_count
                                ),
                                panel_capacity_w=insight.panel_capacity_w,
                                panel_width_m=insight.panel_width_m,
                                panel_height_m=insight.panel_height_m,
                                subject_type=(
                                    subject.get("type") or "unknown"
                                ),
                                tenant_cost_assumptions=tenant_row.get(
                                    "cost_assumptions"
                                ),
                            )
                            roof_update: dict[str, Any] = {
                                "lat": lat,
                                "lng": lng,
                                "geohash": new_geohash,
                                "area_sqm": insight.area_sqm,
                                "estimated_kwp": insight.estimated_kwp,
                                "estimated_yearly_kwh": insight.estimated_yearly_kwh,
                                "exposure": insight.dominant_exposure,
                                "pitch_degrees": insight.pitch_degrees,
                                "shading_score": insight.shading_score,
                                "data_source": "google_solar",
                            }
                            if new_derivations is not None:
                                roof_update["derivations"] = new_derivations
                            sb.table("roofs").update(roof_update).eq(
                                "id", roof.get("id")
                            ).execute()
                            log.info(
                                "creative.roof_synced_after_snap",
                                lead_id=payload.lead_id,
                                roof_id=roof.get("id"),
                                lat=lat,
                                lng=lng,
                                kwp=insight.estimated_kwp,
                            )
                        except Exception as exc:  # noqa: BLE001
                            # A roof-update failure mustn't kill the
                            # render. The mismatch persists as a
                            # quality bug but the email still goes out.
                            log.warning(
                                "creative.roof_sync_failed",
                                lead_id=payload.lead_id,
                                err=str(exc)[:200],
                            )

                log.info(
                    "creative.solar_insight_fetched",
                    lead_id=payload.lead_id,
                    panels=len(insight.panels),
                    kwp=insight.estimated_kwp,
                )

                # 4a) BEFORE — real Google aerial crop, no panels drawn.
                # When CREATIVE_SKIP_REPLICATE is on we use
                # render_before_after which does the same Solar API
                # fetch + crop AND renders panels deterministically
                # via PIL polygons in a single call — saving a
                # round-trip and getting the AFTER image without
                # touching Replicate.
                if settings.creative_skip_replicate:
                    before_bytes, after_bytes_offline = await render_before_after(
                        lat, lng, insight,
                        api_key=settings.google_solar_api_key or None,
                    )
                else:
                    before_bytes = await render_before_only(
                        lat, lng, insight,
                        api_key=settings.google_solar_api_key or None,
                    )
                    after_bytes_offline = None  # filled in by Replicate path below
                before_url = upload_bytes(
                    bucket=RENDERINGS_BUCKET,
                    path=f"{payload.tenant_id}/{payload.lead_id}/before.png",
                    data=before_bytes,
                    content_type="image/png",
                )

                # Book-keep Solar API cost early so even if AI paint fails
                # the call we did make still gets reflected in usage.
                _log_api_cost(
                    sb,
                    tenant_id=payload.tenant_id,
                    provider="google_solar",
                    endpoint="solar/buildingInsights+dataLayers",
                    cost_cents=5,  # $0.02 + $0.03
                    status="success",
                    metadata={
                        "lead_id": payload.lead_id,
                        "panels": len(insight.panels),
                    },
                )

                # 4b) AFTER — Gemini Flash Image paints photoreal panels
                #     on the real aerial photo. Solar API drives the prompt
                #     (panel count, dominant azimuth, kWp scale, roof
                #     geometry) but the pixels are produced by the AI
                #     engine, not PIL.
                primary_az = (
                    insight.panels[0].segment_azimuth_deg
                    if insight.panels
                    else None
                )
                # Number of distinct roof planes — extracted from the
                # per-panel ``segment_index`` so the prompt can constrain
                # nano-banana on multi-segment roofs (typical for L-shaped
                # houses and industrial buildings with mixed orientations).
                roof_segment_count: int | None = (
                    len({p.segment_index for p in insight.panels})
                    if insight.panels
                    else None
                )
                if settings.creative_skip_replicate and after_bytes_offline is not None:
                    # Offline path: render_before_after has already
                    # produced the geometric panel overlay via PIL
                    # polygons drawn at exact Solar lat/lng. Looks
                    # less photoreal than nano-banana's instruction-
                    # edit but is deterministic and zero-cost.
                    after_bytes = after_bytes_offline
                    log.info(
                        "creative.after_image_offline",
                        lead_id=payload.lead_id,
                        panels=len(insight.panels),
                        note="CREATIVE_SKIP_REPLICATE=true — bypassed nano-banana",
                    )
                else:
                    after_bytes = await paint_panels_on_aerial(
                        before_image_url=before_url,
                        panel_count=len(insight.panels),
                        primary_azimuth_deg=primary_az,
                        kwp=insight.estimated_kwp,
                        subject_type=subject.get("type") or "unknown",
                        roof_area_sqm=insight.area_sqm or None,
                        roof_segment_count=roof_segment_count,
                        roof_pitch_deg=insight.pitch_degrees or None,
                    )
                    _log_api_cost(
                        sb,
                        tenant_id=payload.tenant_id,
                        provider="replicate",
                        endpoint="google/nano-banana",
                        cost_cents=4,  # ~$0.039 per nano-banana call
                        status="success",
                        metadata={"lead_id": payload.lead_id},
                    )
                after_url = upload_bytes(
                    bucket=RENDERINGS_BUCKET,
                    path=f"{payload.tenant_id}/{payload.lead_id}/after.png",
                    data=after_bytes,
                    content_type="image/png",
                )

            except SolarApiNotFound:
                skipped_reason = "solar_no_building"
                log.info(
                    "creative.solar_no_building",
                    lead_id=payload.lead_id,
                    lat=lat,
                    lng=lng,
                )
            except AiPaintError as exc:
                # Solar+before succeeded; AI step failed. Keep before_url
                # uploaded so the email can fall back to a real aerial
                # without panels rather than no image at all.
                err_msg = str(exc).replace("\n", " ")[:160]
                skipped_reason = f"ai_paint_error:{err_msg}"
                log.warning(
                    "creative.ai_paint_failed",
                    lead_id=payload.lead_id,
                    err=err_msg,
                )
            except (SolarApiError, SolarRenderingError, httpx.HTTPError) as exc:
                err_msg = str(exc).replace("\n", " ")[:160]
                skipped_reason = f"solar_render_error:{err_msg}"
                log.warning(
                    "creative.solar_render_failed",
                    lead_id=payload.lead_id,
                    err=err_msg,
                )
                _log_api_cost(
                    sb,
                    tenant_id=payload.tenant_id,
                    provider="google_solar",
                    endpoint="solar/buildingInsights+dataLayers",
                    cost_cents=5,
                    status="error",
                    metadata={"lead_id": payload.lead_id, "err": err_msg[:80]},
                )

        # 6) Video + GIF via Remotion sidecar (Sprint 5). We only try
        # this when both a before and an after exist — the transition
        # doesn't make sense without both. ROI fields are optional on
        # the sidecar side, but we prefer to pass them when available
        # so the outro panel has real numbers. Failures here are
        # non-fatal — we still commit the image + ROI.
        #
        # Fail-loud: when we cannot produce a GIF we log
        # `creative.gif_fallback` with a structured `reason` so the
        # demo dashboard (and ops) can tell at a glance why an outreach
        # email went out as a static image. The reasons are:
        #   - `before_url_missing`  — Solar API skipped, no satellite tile
        #   - `after_url_missing`   — AI panel-paint failed
        #   - `roi_missing`         — quote engine produced no numbers
        #   - `remotion_failed`     — sidecar HTTP/render error
        video_url: str | None = None
        gif_url: str | None = None
        gif_fallback_reason: str | None = None
        if settings.creative_skip_replicate:
            # Operator opted out of every Replicate-billed step. The
            # Remotion sidecar drives the transition video via Kling
            # 1.6-Pro on Replicate (~$0.49 per 5 s clip), so we skip
            # it entirely. The email lands with the static after
            # image as the hero — same fall-back path used when
            # remotion fails for any reason.
            gif_fallback_reason = "creative_skip_replicate"
            log.info(
                "creative.video_skipped",
                lead_id=payload.lead_id,
                note="CREATIVE_SKIP_REPLICATE=true — bypassed Kling video",
            )
        elif (
            before_url is not None
            and after_url is not None
            and roi is not None
        ):
            try:
                render_input = RenderTransitionInput(
                    before_image_url=before_url,
                    after_image_url=after_url,
                    kwp=roi.estimated_kwp,
                    yearly_savings_eur=roi.yearly_savings_eur,
                    payback_years=roi.payback_years
                    if roi.payback_years is not None
                    else 0.0,
                    co2_tonnes_lifetime=roi.co2_tonnes_25_years,
                    tenant_name=tenant_row.get("business_name") or "SolarLead",
                    brand_primary_color=tenant_row.get("brand_primary_color")
                    or "#0F766E",
                    brand_logo_url=tenant_row.get("brand_logo_url"),
                    output_path=f"{payload.tenant_id}/{payload.lead_id}",
                )
                transition = await render_transition(render_input)
                video_url = transition.mp4_url
                gif_url = transition.gif_url
                log.info(
                    "creative.remotion_ok",
                    lead_id=payload.lead_id,
                    mp4=video_url,
                    gif=gif_url,
                )
            except (RemotionError, httpx.HTTPError) as exc:
                gif_fallback_reason = "remotion_failed"
                # Capture exception class name explicitly because some httpx
                # exceptions (e.g. ReadTimeout) have empty str() representations
                # and just logging `err=str(exc)` produces useless "err=" lines.
                err_type = type(exc).__name__
                err_str = str(exc) or err_type
                log.warning(
                    "creative.remotion_failed",
                    lead_id=payload.lead_id,
                    err=err_str,
                    err_type=err_type,
                )
                log.warning(
                    "creative.gif_fallback",
                    lead_id=payload.lead_id,
                    reason=gif_fallback_reason,
                    err=err_str[:160],
                    err_type=err_type,
                )
                if skipped_reason is None:
                    skipped_reason = "remotion_error"
        else:
            # Pre-conditions for the Remotion render were not satisfied.
            # Surface exactly which input was missing so the operator
            # (and the demo `demo_pipeline_runs.notes` column) can
            # explain why the outreach went out as a still image.
            if before_url is None:
                gif_fallback_reason = "before_url_missing"
            elif after_url is None:
                gif_fallback_reason = "after_url_missing"
            else:
                gif_fallback_reason = "roi_missing"
            log.warning(
                "creative.gif_fallback",
                lead_id=payload.lead_id,
                reason=gif_fallback_reason,
                before_url_present=before_url is not None,
                after_url_present=after_url is not None,
                roi_present=roi is not None,
            )

        # 7) Persist whatever we produced back onto the lead row. We
        # always update roi_data; we only overwrite rendering_*_url when
        # we have a fresh value, so a failed sidecar call doesn't nuke
        # a previously-good video from an earlier run.
        update: dict[str, Any] = {"roi_data": roi_jsonb}
        if after_url is not None:
            update["rendering_image_url"] = after_url
        if video_url is not None:
            update["rendering_video_url"] = video_url
        if gif_url is not None:
            update["rendering_gif_url"] = gif_url
        sb.table("leads").update(update).eq("id", payload.lead_id).execute()

        # 8) Roof status progression — only once we have the full
        # rendered asset (at minimum the after-image). Having a video
        # is nice-to-have and doesn't move the lifecycle past rendered.
        if after_url is not None:
            current_status = roof.get("status")
            if current_status == RoofStatus.SCORED.value:
                sb.table("roofs").update(
                    {"status": RoofStatus.RENDERED.value}
                ).eq("id", lead["roof_id"]).execute()

        out = CreativeOutput(
            lead_id=payload.lead_id,
            before_url=before_url,
            after_url=after_url,
            video_url=video_url,
            gif_url=gif_url,
            roi_data=roi_jsonb,
            skipped=after_url is None,
            reason=skipped_reason,
        )

        await self._emit_event(
            event_type="lead.rendered" if after_url else "lead.render_skipped",
            payload=out.model_dump(),
            tenant_id=payload.tenant_id,
            lead_id=payload.lead_id,
        )
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_single(
    sb: Any, table: str, row_id: str, tenant_id: str
) -> dict[str, Any] | None:
    res = (
        sb.table(table)
        .select("*")
        .eq("id", row_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    data = res.data or []
    return data[0] if data else None


def _log_api_cost(
    sb: Any,
    *,
    tenant_id: str,
    provider: str,
    endpoint: str,
    cost_cents: int,
    status: str,
    metadata: dict[str, Any],
) -> None:
    """Best-effort api_usage_log insert — never fails the agent."""
    try:
        sb.table("api_usage_log").insert(
            {
                "tenant_id": tenant_id,
                "provider": provider,
                "endpoint": endpoint,
                "request_count": 1,
                "cost_cents": cost_cents,
                "status": status,
                "metadata": metadata,
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("api_usage_log_write_failed", err=str(exc))


def _to_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
