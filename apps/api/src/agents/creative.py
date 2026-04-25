"""Creative Agent — Google Solar aerial → deterministic PV overlay → ROI → lead assets.

Pipeline:

    lead_id
        ↓
    load lead + roof + subject + tenant (brand)
        ↓
    idempotency: if lead.rendering_image_url already set and not force → skip
        ↓
    fetch Google Solar buildingInsights (panel geometry) + dataLayers (RGB aerial)
        ↓
    solar_rendering_service.render_before_after()
          → "before" image  (Google aerial crop, 1024×1024, 10 cm/px)
          → "after"  image  (same crop + PIL panel overlay, deterministic)
        ↓
    upload before.png + after.png to Supabase Storage
        renderings/{tenant_id}/{lead_id}/before.png
        renderings/{tenant_id}/{lead_id}/after.png
        ↓
    compute_roi(kwp, yearly_kwh, subject_type)  → leads.roi_data
        ↓
    Remotion sidecar: render_transition(before, after, roi)  → MP4 + GIF
        ↓
    UPDATE leads SET rendering_image_url, rendering_video_url, rendering_gif_url, roi_data
    UPDATE roofs.status = 'rendered'
        ↓
    emit lead.rendered event

Degradation:
  * Google Solar 404 (no building data) or bad imagery quality → skip_reason
    set, ROI still persisted, outreach continues without rendering.
  * Google Solar API key missing → same graceful skip.
  * PIL rendering error → before uploaded only (no after), Remotion skipped.
  * Remotion sidecar failure → images still committed, video/GIF skipped.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from ..core.config import settings
from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from ..models.enums import RoofStatus
from ..services.google_solar_service import (
    SolarApiError,
    SolarApiNotFound,
    fetch_building_insight,
)
from ..services.remotion_service import (
    RemotionError,
    RenderTransitionInput,
    render_transition,
)
from ..services.roi_service import compute_roi
from ..services.solar_rendering_service import (
    SolarRenderingError,
    render_before_after,
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
            .select("id, business_name, brand_primary_color, brand_logo_url")
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

        # 4) Fetch Google Solar panel geometry + aerial RGB imagery, then
        # render before/after deterministically with PIL.
        # Non-fatal: missing coords or Solar API failure → skip rendering,
        # ROI is still persisted and outreach continues without images.
        before_url: str | None = None
        after_url: str | None = None
        skipped_reason: str | None = None

        lat = _to_float(roof.get("lat"))
        lng = _to_float(roof.get("lng"))

        if lat is None or lng is None:
            skipped_reason = "missing_coords"
        elif not settings.google_solar_api_key and not settings.google_solar_mock_mode:
            skipped_reason = "solar_api_key_not_configured"
            log.warning("creative.solar_api_key_missing", lead_id=payload.lead_id)
        else:
            try:
                # Re-fetch buildingInsights to get the full solarPanels list.
                # Hunter L4 already fetched this but the panel geometry is not
                # stored in the DB, so a second call is necessary here.
                # Cost: ~$0.02 (buildingInsights) + ~$0.03 (dataLayers) per lead.
                async with httpx.AsyncClient(timeout=30.0) as http:
                    insight = await fetch_building_insight(lat, lng, client=http)

                log.info(
                    "creative.solar_insight_fetched",
                    lead_id=payload.lead_id,
                    panels=len(insight.panels),
                    kwp=insight.estimated_kwp,
                )

                before_bytes, after_bytes = await render_before_after(
                    lat, lng, insight,
                    api_key=settings.google_solar_api_key or None,
                )

                before_url = upload_bytes(
                    bucket=RENDERINGS_BUCKET,
                    path=f"{payload.tenant_id}/{payload.lead_id}/before.png",
                    data=before_bytes,
                    content_type="image/png",
                )
                after_url = upload_bytes(
                    bucket=RENDERINGS_BUCKET,
                    path=f"{payload.tenant_id}/{payload.lead_id}/after.png",
                    data=after_bytes,
                    content_type="image/png",
                )

                # Book-keep cost (buildingInsights + dataLayers).
                _log_api_cost(
                    sb,
                    tenant_id=payload.tenant_id,
                    provider="google_solar",
                    endpoint="solar/buildingInsights+dataLayers",
                    cost_cents=5,  # $0.02 + $0.03
                    status="success",
                    metadata={"lead_id": payload.lead_id, "panels": len(insight.panels)},
                )

            except SolarApiNotFound:
                skipped_reason = "solar_no_building"
                log.info(
                    "creative.solar_no_building",
                    lead_id=payload.lead_id,
                    lat=lat,
                    lng=lng,
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
        video_url: str | None = None
        gif_url: str | None = None
        if (
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
                log.warning(
                    "creative.remotion_failed",
                    lead_id=payload.lead_id,
                    err=str(exc),
                )
                if skipped_reason is None:
                    skipped_reason = "remotion_error"

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
