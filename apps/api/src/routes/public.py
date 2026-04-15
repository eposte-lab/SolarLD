"""Public endpoints for the lead portal — no auth.

These serve the lead-facing slug pages (/lead/:slug) and handle
opt-outs and engagement tracking.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..core.supabase_client import get_service_client

router = APIRouter()


@router.get("/lead/{slug}")
async def get_public_lead(slug: str) -> dict[str, object]:
    """Return sanitized lead data for the public portal."""
    sb = get_service_client()
    res = (
        sb.table("leads")
        .select(
            "public_slug, score, score_tier, rendering_image_url, "
            "rendering_video_url, rendering_gif_url, roi_data, "
            "tenant_id, subjects(type, business_name, owner_first_name), "
            "roofs(address, cap, comune, provincia, area_sqm, "
            "estimated_kwp, estimated_yearly_kwh)"
        )
        .eq("public_slug", slug)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead = res.data[0]

    # Fetch tenant branding
    tenant = (
        sb.table("tenants")
        .select("business_name, brand_logo_url, brand_primary_color, whatsapp_number")
        .eq("id", lead["tenant_id"])
        .limit(1)
        .execute()
    )
    lead["tenant"] = tenant.data[0] if tenant.data else None
    # Hide raw tenant_id from the public response
    lead.pop("tenant_id", None)
    return lead


@router.post("/lead/{slug}/visit")
async def track_visit(slug: str) -> dict[str, str]:
    """Record a dashboard visit event."""
    sb = get_service_client()
    sb.table("leads").update({"dashboard_visited_at": "now()"}).eq("public_slug", slug).execute()
    return {"ok": "tracked"}


@router.post("/lead/{slug}/whatsapp-click")
async def track_whatsapp_click(slug: str) -> dict[str, str]:
    """Record a WhatsApp CTA click."""
    sb = get_service_client()
    sb.table("leads").update({"whatsapp_initiated_at": "now()"}).eq("public_slug", slug).execute()
    return {"ok": "tracked"}


@router.post("/lead/{slug}/optout")
async def optout(slug: str) -> dict[str, str]:
    """One-click opt-out → compliance agent adds to global blacklist."""
    # TODO: enqueue compliance job
    return {"ok": "optout_requested"}
