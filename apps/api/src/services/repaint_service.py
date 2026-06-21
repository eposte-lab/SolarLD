"""Repaint an existing rendering's panels WITHOUT re-calling Google Solar.

The full creative pipeline re-fetches Google Solar (buildingInsights + the bare
aerial dataLayers) on EVERY regeneration. So when Solar billing is down (403) a
"Rigenera rendering" click silently produces nothing — and even when it's up,
every retry burns a Solar call. But the expensive, reusable parts are already on
disk: the bare aerial ``before.png`` in Storage and the panel geometry / KPIs in
``roofs.derivations``. This service reuses both and rebuilds the SAME clip the
full pipeline does — repaint the After (nano-banana), then the before→after
transition VIDEO (the Kling sidecar) — and writes the new image + video + gif
over the lead, so the dashboard, the dossier and the outreach email all show the
fresh result. So a repaint:

  * works even with Solar billing 403 — there is NO Google call at all (it reuses
    the stored before.png + derivations);
  * is "Rigenera minus Google Solar": same nano-banana paint + Kling transition
    video, just without the Solar buildingInsights/dataLayers fetch;
  * lets the operator iterate on a bad render straight from the lead page.

Wired to a SECOND dashboard button ("Ridipingi pannelli"), distinct from the
full "Rigenera rendering" (which still re-derives everything from Solar).
"""

from __future__ import annotations

from typing import Any

import httpx

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from ..services.ai_panel_paint_service import generate_after_with_panels
from ..services.image_alignment_service import align_after_to_before
from ..services.remotion_service import RenderTransitionInput, render_transition
from ..services.solar_rendering_service import (
    bake_savings_strip,
    normalize_to_output_dimensions,
)
from ..services.storage_service import upload_bytes

log = get_logger(__name__)

RENDERINGS_BUCKET = "renderings"


class RepaintError(Exception):
    """Repaint could not run (missing stored aerial / panel data)."""


async def repaint_rendering(*, tenant_id: str, lead_id: str) -> dict[str, Any]:
    """Re-paint the after image from the STORED aerial. No Google Solar call.

    Raises ``RepaintError`` when there is no stored ``before.png`` to paint on —
    the caller should fall back to the full Solar "Rigenera" in that case.
    """
    sb = get_service_client()

    lead_res = (
        sb.table("leads")
        .select("id, roof_id, rendering_regen_count")
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    if not lead_res.data:
        raise RepaintError("lead_not_found")
    roof_id = lead_res.data[0].get("roof_id")
    regen_count = int(lead_res.data[0].get("rendering_regen_count") or 0)

    derivations: dict[str, Any] = {}
    if roof_id:
        roof_res = sb.table("roofs").select("derivations").eq("id", roof_id).limit(1).execute()
        if roof_res.data:
            derivations = roof_res.data[0].get("derivations") or {}

    tenant_res = (
        sb.table("tenants")
        .select("business_name, brand_primary_color, brand_logo_url")
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    tenant_row = tenant_res.data[0] if tenant_res.data else {}
    brand = tenant_row.get("brand_primary_color") or "#183054"

    panel_count = int(derivations.get("panel_count") or 0)
    _kwp = derivations.get("estimated_kwp")
    kwp = float(_kwp) if _kwp else None
    savings = float(
        derivations.get("realistic_yearly_savings_eur")
        or derivations.get("yearly_savings_eur")
        or 0.0
    )

    # The bare aerial we painted last time is already in Storage — reuse it.
    before_path = f"{tenant_id}/{lead_id}/before.png"
    before_url = sb.storage.from_(RENDERINGS_BUCKET).get_public_url(before_path)

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(before_url)
            resp.raise_for_status()
            before_bytes = resp.content
        except httpx.HTTPError as exc:
            # No stored aerial → the full Solar "Rigenera" must create it first.
            raise RepaintError("before_image_missing") from exc

    # Re-paint panels on the SAME aerial (Replicate only — no Google Solar).
    after_bytes = await generate_after_with_panels(
        before_image_url=before_url,
        panel_count=panel_count,
        kwp=kwp,
    )
    after_bytes = normalize_to_output_dimensions(after_bytes)
    # Lock the painted frame back onto the original aerial (ECC, best-effort) so
    # the roof doesn't drift — the same fidelity step the full pipeline uses.
    after_bytes = align_after_to_before(before_bytes, after_bytes)

    if savings > 0:
        after_bytes = bake_savings_strip(
            after_bytes,
            savings_eur=savings,
            kwp=kwp,
            brand_color_hex=brand,
        )

    after_url = upload_bytes(
        bucket=RENDERINGS_BUCKET,
        path=f"{tenant_id}/{lead_id}/after.png",
        data=after_bytes,
        content_type="image/png",
    )

    # Rebuild the before→after transition VIDEO from the SAME stored aerial + the
    # fresh After (the Kling sidecar). This is what makes the dashboard hero, the
    # dossier and the outreach email show the new ANIMATION — not just the static
    # — exactly what the full "Rigenera" produces. Billing-free: the sidecar only
    # needs the two image URLs, no Google Solar. Best-effort: a sidecar failure
    # falls back to the static (we never strand the repaint).
    video_url: str | None = None
    gif_url: str | None = None
    try:
        transition = await render_transition(
            RenderTransitionInput(
                before_image_url=before_url,
                after_image_url=after_url,
                kwp=float(kwp or 0.0),
                yearly_savings_eur=savings,
                payback_years=float(derivations.get("payback_years") or 0.0),
                co2_tonnes_lifetime=derivations.get("co2_tonnes_25_years"),
                tenant_name=tenant_row.get("business_name") or "SolarLead",
                brand_primary_color=brand,
                brand_logo_url=tenant_row.get("brand_logo_url"),
                output_path=f"{tenant_id}/{lead_id}",
            )
        )
        video_url = transition.mp4_url
        gif_url = transition.gif_url
    except Exception as exc:  # noqa: BLE001 — video is non-fatal; keep the static.
        log.warning(
            "repaint.video_failed",
            lead_id=lead_id,
            err=str(exc)[:200],
            err_type=type(exc).__name__,
        )

    # Persist the new clip. The cache-bust counter bumps HERE — only now that the
    # new after.png is on disk — so a premature dashboard refresh can't cache the
    # OLD image under the new ``?v={count}`` key. ALWAYS overwrite video/gif (and
    # null the previous CDN copies) so no surface keeps showing the stale
    # animation: a fresh transition when the sidecar succeeded, else null so every
    # surface falls back to the new static image.
    update: dict[str, Any] = {
        "rendering_image_url": after_url,
        "rendering_regen_count": regen_count + 1,
        "rendering_video_url": video_url,
        "rendering_gif_url": gif_url,
        "rendering_video_cdn_url": None,
        "rendering_gif_cdn_url": None,
        # Clear any stale failure reason on success; surface a current one when
        # the video step itself failed (so the lead page's chip is accurate, not
        # a leftover Solar 403 from an old "Rigenera").
        "creative_skipped_reason": None if (video_url and gif_url) else "remotion_error",
    }
    sb.table("leads").update(update).eq("id", lead_id).execute()

    log.info(
        "repaint.done",
        tenant_id=tenant_id,
        lead_id=lead_id,
        panel_count=panel_count,
        video=bool(video_url),
    )
    return {
        "ok": True,
        "lead_id": lead_id,
        "after_url": after_url,
        "video_url": video_url,
        "panel_count": panel_count,
    }
