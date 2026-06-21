"""Repaint an existing rendering's panels WITHOUT re-calling Google Solar.

The full creative pipeline re-fetches Google Solar (buildingInsights + the bare
aerial dataLayers) on EVERY regeneration. So when Solar billing is down (403) a
"Rigenera rendering" click silently produces nothing — and even when it's up,
every retry burns a Solar call. But the expensive, reusable parts are already on
disk: the bare aerial ``before.png`` in Storage and the panel geometry / KPIs in
``roofs.derivations``. This service reuses both and re-runs ONLY the nano-banana
paint (Replicate). So a repaint:

  * works even with Solar billing 403 — there is NO Google call at all;
  * is cheap (one Replicate paint, ~$0.04, no Solar imagery fetch);
  * lets the operator iterate on a bad paint straight from the lead page.

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
        sb.table("tenants").select("brand_primary_color").eq("id", tenant_id).limit(1).execute()
    )
    brand = (
        tenant_res.data[0].get("brand_primary_color") if tenant_res.data else None
    ) or "#183054"

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
    # Bump the cache-bust counter HERE — only now that the new after.png is
    # actually on disk. The dashboard busts the image with ``?v={count}``; if we
    # let the endpoint bump it at click-time (as it does), a premature refresh
    # caches the OLD image under the NEW key and the repaint "never shows".
    # Bumping post-upload yields a fresh key that no early refresh has poisoned.
    sb.table("leads").update(
        {
            "rendering_image_url": after_url,
            "rendering_regen_count": regen_count + 1,
            # The repaint only refreshes the STATIC after.png. The old GIF/MP4
            # were baked from the PREVIOUS render, and the dashboard hero, the
            # outreach email and the public dossier all prefer video → gif →
            # image — so without this they would keep showing the stale (wrong)
            # animation and the repaint would be invisible everywhere except the
            # raw file. Null the stale video/gif so every surface falls back to
            # the freshly-painted static image. A full "Rigenera" rebuilds the
            # animation later (it needs Solar + the video sidecar).
            "rendering_gif_url": None,
            "rendering_gif_cdn_url": None,
            "rendering_video_url": None,
            "rendering_video_cdn_url": None,
        }
    ).eq("id", lead_id).execute()

    log.info(
        "repaint.done",
        tenant_id=tenant_id,
        lead_id=lead_id,
        panel_count=panel_count,
    )
    return {"ok": True, "lead_id": lead_id, "after_url": after_url, "panel_count": panel_count}
