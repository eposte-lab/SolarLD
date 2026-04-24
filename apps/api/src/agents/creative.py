"""Creative Agent — satellite tile → PV rendering → ROI → lead assets.

Sprint 4 pipeline (GIF/video shipped in Sprint 5 alongside Remotion):

    lead_id
        ↓
    load lead + roof + subject + tenant (brand)
        ↓
    idempotency: if lead.rendering_image_url already set and not force → skip
        ↓
    download high-zoom Mapbox satellite tile        → "before" image
        ↓
    upload before.png to Supabase Storage
        renderings/{tenant_id}/{lead_id}/before.png
        ↓
    Replicate create_pv_rendering(before_url, prompt_ctx)
        ↓
    download the prediction output and re-host       → "after" image
        renderings/{tenant_id}/{lead_id}/after.png
        ↓
    compute_roi(kwp, yearly_kwh, subject_type)       → leads.roi_data
        ↓
    UPDATE leads SET rendering_image_url, roi_data
    UPDATE roofs.status = 'rendered' (if currently 'scored')
        ↓
    emit lead.rendered event

Degradation:
  * Replicate timeout / failure → we DON'T touch the after URL, we still
    commit the before URL + ROI. The outreach agent can gracefully use
    the before image (labelled "situazione attuale") without the AI
    overlay. Next regenerate-rendering run retries.
  * Roof has no lat/lng → we skip the image path entirely; ROI is still
    computed if kWp / yearly_kwh are available.

We deliberately re-host the Replicate output on our own Supabase bucket
rather than linking directly to ``replicate.delivery`` — their URLs
expire after 24h and we need long-lived URLs for email/postal creatives.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from ..models.enums import RoofStatus
from ..services.mapbox_service import MapboxError, build_static_satellite_url
from ..services.remotion_service import (
    RemotionError,
    RenderTransitionInput,
    render_transition,
)
from ..services.replicate_service import (
    REPLICATE_COST_PER_CALL_CENTS,
    RenderingPromptContext,
    ReplicateError,
    create_pv_rendering,
)
from ..services.roi_service import RoiEstimate, compute_roi
from ..services.storage_service import upload_bytes
from .base import AgentBase

log = get_logger(__name__)

RENDERINGS_BUCKET = "renderings"
DEFAULT_SATELLITE_ZOOM = 20
DEFAULT_SATELLITE_SIZE = 768


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
        # can persist useful data even if Replicate is down.
        roi = compute_roi(
            estimated_kwp=roof.get("estimated_kwp"),
            estimated_yearly_kwh=roof.get("estimated_yearly_kwh"),
            subject_type=subject.get("type") or "unknown",
        )
        roi_jsonb = roi.to_jsonb() if roi else {}

        # 4) Build the "before" image URL (Mapbox static satellite) and
        # re-host it on Supabase Storage so it survives Mapbox URL churn.
        before_url: str | None = None
        after_url: str | None = None
        skipped_reason: str | None = None

        lat = _to_float(roof.get("lat"))
        lng = _to_float(roof.get("lng"))
        if lat is None or lng is None:
            skipped_reason = "missing_coords"
        else:
            try:
                async with httpx.AsyncClient(timeout=20.0) as http:
                    before_bytes = await _download_satellite_tile(
                        lat=lat, lng=lng, client=http
                    )
                before_url = upload_bytes(
                    bucket=RENDERINGS_BUCKET,
                    path=f"{payload.tenant_id}/{payload.lead_id}/before.png",
                    data=before_bytes,
                    content_type="image/png",
                )
            except (MapboxError, httpx.HTTPError) as exc:
                log.warning(
                    "creative.before_download_failed",
                    lead_id=payload.lead_id,
                    err=str(exc),
                )
                skipped_reason = "mapbox_unavailable"

        # 5) If we have a before image, try the Replicate pass. Failure
        # here is non-fatal: we still persist whatever we have.
        if before_url is not None:
            try:
                prompt_ctx = RenderingPromptContext(
                    area_sqm=_to_float(roof.get("area_sqm")),
                    exposure=roof.get("exposure"),
                    brand_primary_color=tenant_row.get("brand_primary_color"),
                    subject_type=subject.get("type") or "unknown",
                )
                prediction = await create_pv_rendering(
                    before_image_url=before_url,
                    prompt_ctx=prompt_ctx,
                )
                if prediction.is_success and prediction.output_url:
                    async with httpx.AsyncClient(timeout=30.0) as http:
                        after_bytes = await _download_url(
                            prediction.output_url, client=http
                        )
                    after_url = upload_bytes(
                        bucket=RENDERINGS_BUCKET,
                        path=f"{payload.tenant_id}/{payload.lead_id}/after.png",
                        data=after_bytes,
                        content_type="image/png",
                    )
                else:
                    skipped_reason = (
                        f"replicate_{prediction.status}"
                        if skipped_reason is None
                        else skipped_reason
                    )
                    log.warning(
                        "creative.replicate_no_output",
                        lead_id=payload.lead_id,
                        status=prediction.status,
                        error=prediction.error,
                    )
            except (ReplicateError, httpx.HTTPError) as exc:
                log.warning(
                    "creative.replicate_failed",
                    lead_id=payload.lead_id,
                    err=str(exc),
                )
                if skipped_reason is None:
                    # Preserve the actual error message (truncated) so
                    # the admin seed-test response surfaces it — otherwise
                    # "replicate_error" is opaque and requires log digging.
                    err_msg = str(exc).replace("\n", " ")[:160]
                    skipped_reason = f"replicate_error:{err_msg}"

            # Book-keep Replicate cost (always, even on failure we pay for
            # the inference time).
            _log_api_cost(
                sb,
                tenant_id=payload.tenant_id,
                endpoint="predictions:create",
                cost_cents=REPLICATE_COST_PER_CALL_CENTS,
                status="success" if after_url else "error",
                metadata={"lead_id": payload.lead_id},
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


async def _download_satellite_tile(
    *, lat: float, lng: float, client: httpx.AsyncClient
) -> bytes:
    """Fetch the Mapbox static satellite tile as raw PNG bytes."""
    url = build_static_satellite_url(
        lat,
        lng,
        zoom=DEFAULT_SATELLITE_ZOOM,
        width=DEFAULT_SATELLITE_SIZE,
        height=DEFAULT_SATELLITE_SIZE,
    )
    return await _download_url(url, client=client)


async def _download_url(url: str, *, client: httpx.AsyncClient) -> bytes:
    resp = await client.get(url)
    if resp.status_code >= 400:
        raise httpx.HTTPError(
            f"download {url[:80]}... status={resp.status_code}"
        )
    return resp.content


def _log_api_cost(
    sb: Any,
    *,
    tenant_id: str,
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
                "provider": "replicate",
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
