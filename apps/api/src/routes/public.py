"""Public endpoints for the lead portal — no auth.

These serve the lead-facing slug pages (/lead/:slug) and handle
opt-outs, engagement tracking, and appointment requests.

All endpoints are **idempotent** — they are the public ingress for
bots and email clients that prefetch links, so double hits from
Gmail's image proxy, antivirus scanners, or human refresh must never
produce user-visible duplicate effects.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from ..core.logging import get_logger
from ..core.queue import enqueue
from ..core.redis import get_redis
from ..core.supabase_client import get_service_client
from ..models.enums import BlacklistReason, LeadStatus

log = get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Lead detail
# ---------------------------------------------------------------------------


@router.get("/lead/{slug}")
async def get_public_lead(slug: str) -> dict[str, object]:
    """Return sanitized lead data for the public portal."""
    sb = get_service_client()
    res = (
        sb.table("leads")
        .select(
            "public_slug, score, score_tier, rendering_image_url, "
            "rendering_video_url, rendering_gif_url, roi_data, "
            "pipeline_status, outreach_sent_at, "
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

    # Already-opted-out leads shouldn't render the CTA anymore. We
    # return 410 Gone rather than 404 so the portal can show a
    # dedicated "you've unsubscribed" page instead of the generic
    # not-found screen.
    if lead.get("pipeline_status") == LeadStatus.BLACKLISTED.value:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Lead unsubscribed",
        )

    # Fetch tenant branding
    tenant = (
        sb.table("tenants")
        .select(
            "business_name, brand_logo_url, brand_primary_color, "
            "whatsapp_number, contact_email"
        )
        .eq("id", lead["tenant_id"])
        .limit(1)
        .execute()
    )
    lead["tenant"] = tenant.data[0] if tenant.data else None
    # Hide raw tenant_id from the public response
    lead.pop("tenant_id", None)
    return lead


# ---------------------------------------------------------------------------
# Engagement tracking (idempotent upserts of timestamps)
# ---------------------------------------------------------------------------


@router.post("/lead/{slug}/visit")
async def track_visit(slug: str) -> dict[str, str]:
    """Record a lead-portal visit event.

    We only SET ``dashboard_visited_at`` if it's currently NULL —
    repeated page refreshes don't keep bumping the timestamp. The
    ``pipeline_status`` is also nudged forward to ``engaged`` when we
    were still at ``delivered``/``opened``/``clicked``.
    """
    sb = get_service_client()
    lead = _load_lead_by_slug(sb, slug)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    update: dict[str, Any] = {}
    if not lead.get("dashboard_visited_at"):
        update["dashboard_visited_at"] = "now()"
    # Visit is stronger engagement than 'clicked' — bump pipeline.
    if lead.get("pipeline_status") in {
        LeadStatus.SENT.value,
        LeadStatus.DELIVERED.value,
        LeadStatus.OPENED.value,
        LeadStatus.CLICKED.value,
    }:
        update["pipeline_status"] = LeadStatus.ENGAGED.value
    if update:
        sb.table("leads").update(update).eq("id", lead["id"]).execute()
        _emit_public_event(
            sb,
            event_type="lead.portal_visited",
            tenant_id=lead["tenant_id"],
            lead_id=lead["id"],
            payload={"slug": slug},
        )
    return {"ok": "tracked"}


@router.post("/lead/{slug}/whatsapp-click")
async def track_whatsapp_click(slug: str) -> dict[str, str]:
    """Record a WhatsApp CTA click."""
    sb = get_service_client()
    lead = _load_lead_by_slug(sb, slug)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    if not lead.get("whatsapp_initiated_at"):
        sb.table("leads").update(
            {
                "whatsapp_initiated_at": "now()",
                "pipeline_status": LeadStatus.WHATSAPP.value,
            }
        ).eq("id", lead["id"]).execute()
        _emit_public_event(
            sb,
            event_type="lead.whatsapp_click",
            tenant_id=lead["tenant_id"],
            lead_id=lead["id"],
            payload={"slug": slug},
        )
    return {"ok": "tracked"}


# ---------------------------------------------------------------------------
# Appointment request (inbound form from the portal CTA)
# ---------------------------------------------------------------------------


class AppointmentRequest(BaseModel):
    """Submission shape for the portal's "request a site visit" form.

    Deliberately minimal — we already know who the lead is via the
    slug. Phone/email are the only actionable bits we ask for, plus
    an optional free-text note.
    """

    contact_name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=5, max_length=40)
    # Loose email format — strict validation would require the optional
    # ``email-validator`` dep. We only use this for the installer's
    # audit log, not for sending mail, so a cheap regex is enough.
    email: str | None = Field(
        default=None,
        max_length=200,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    preferred_time: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)


@router.post("/lead/{slug}/appointment", status_code=status.HTTP_202_ACCEPTED)
async def request_appointment(
    slug: str, payload: AppointmentRequest
) -> dict[str, object]:
    """Lead submits the in-portal appointment form.

    We record an ``events`` row + advance the lead to
    ``pipeline_status='appointment'``. The dashboard picks it up from
    there via Supabase Realtime. We don't auto-send anything back to
    the lead — the installer calls/emails them through their normal
    channels.
    """
    sb = get_service_client()
    lead = _load_lead_by_slug(sb, slug)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    sb.table("leads").update(
        {"pipeline_status": LeadStatus.APPOINTMENT.value}
    ).eq("id", lead["id"]).execute()

    _emit_public_event(
        sb,
        event_type="lead.appointment_requested",
        tenant_id=lead["tenant_id"],
        lead_id=lead["id"],
        payload={
            "slug": slug,
            "contact_name": payload.contact_name,
            "phone": payload.phone,
            "email": payload.email,
            "preferred_time": payload.preferred_time,
            "notes": payload.notes,
        },
    )
    return {"ok": True, "status": LeadStatus.APPOINTMENT.value}


# ---------------------------------------------------------------------------
# Portal engagement beacon (Part B.1 — deep-tracking)
# ---------------------------------------------------------------------------
#
# The lead-portal posts micro-events (scroll milestones, heartbeats,
# CTA hovers, ...) to /v1/public/portal/track. The endpoint is:
#
#   * Public — no JWT. The lead is identified by ``public_slug`` in
#     the body. We resolve it to (tenant_id, lead_id) server-side
#     using the service client (RLS bypass).
#   * Rate-limited — 60 events/min per (session_id, slug) in Redis.
#     Bots and malicious browsers can't flood the table.
#   * Validated — event_kind is a closed set; unknown kinds → 400.
#   * Fire-and-forget on the client side (``navigator.sendBeacon``),
#     so we always return 204 even on Redis/DB failures (soft-fail) —
#     the beacon isn't a business-critical write.
#
# The rollup (engagement_score, portal_sessions, ...) is computed by
# the nightly ``engagement_rollup_cron``. Real-time consumers (the
# "hot leads" dashboard feed) subscribe to Supabase Realtime on
# ``portal_events`` directly — migration 0021 registers each monthly
# partition with the ``supabase_realtime`` publication.


# Closed set — any new event kind requires a coordinated change in
# engagement_service.py (score formula) and the lead-portal client.
_ALLOWED_EVENT_KINDS: frozenset[str] = frozenset({
    "portal.view",
    "portal.scroll_50",
    "portal.scroll_90",
    "portal.roi_viewed",
    "portal.cta_hover",
    "portal.whatsapp_click",
    "portal.appointment_click",
    "portal.video_play",
    "portal.video_complete",
    "portal.heartbeat",
    "portal.leave",
})

# Cap per (session, slug) per minute. 60 is generous for a human
# (one heartbeat every 15s + a handful of scrolls = ~10/min) and
# tight enough to stop a runaway client.
_BEACON_RATE_PER_MIN = 60
_BEACON_KEY_TTL = 90  # seconds


class PortalTrackEvent(BaseModel):
    """One telemetry event from the lead-portal client."""

    slug: str = Field(min_length=1, max_length=120)
    session_id: str = Field(
        min_length=1,
        max_length=64,
        description="UUID generated client-side, persisted in sessionStorage.",
    )
    event_kind: Literal[
        "portal.view",
        "portal.scroll_50",
        "portal.scroll_90",
        "portal.roi_viewed",
        "portal.cta_hover",
        "portal.whatsapp_click",
        "portal.appointment_click",
        "portal.video_play",
        "portal.video_complete",
        "portal.heartbeat",
        "portal.leave",
    ]
    metadata: dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: int | None = Field(default=None, ge=0, le=24 * 60 * 60 * 1000)


@router.post("/portal/track", status_code=status.HTTP_204_NO_CONTENT)
async def portal_track(event: PortalTrackEvent) -> Response:
    """Ingest one engagement event from the lead-portal.

    Returns 204 No Content on success **and** on non-critical soft
    failures (rate-limit hit, Redis outage, unresolved slug). The only
    400 paths are schema-level — enforced by Pydantic. This matches
    ``navigator.sendBeacon`` semantics (client can't react to 4xx
    anyway) and keeps the portal snappy.
    """
    # Fast-path guardrail in case a deferred tool mutates the enum
    # without updating the closed set above.
    if event.event_kind not in _ALLOWED_EVENT_KINDS:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Rate-limit by (session, slug) — per-IP would ban corporate NAT
    # easily, per-session is precise and honest bots don't rotate
    # session_ids.
    if not await _beacon_rate_allows(event.session_id, event.slug):
        log.info(
            "portal.track.rate_limited",
            slug=event.slug,
            session_id=event.session_id,
            event_kind=event.event_kind,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    sb = get_service_client()
    lead = _load_lead_by_slug(sb, event.slug)
    if lead is None:
        # Silently drop — an outdated link in the wild shouldn't
        # produce loud 404s that show up in monitoring.
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Blacklisted leads keep the portal showing the 410 page; events
    # past that point are noise and shouldn't be recorded.
    if lead.get("pipeline_status") == LeadStatus.BLACKLISTED.value:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    try:
        sb.table("portal_events").insert(
            {
                "tenant_id": lead["tenant_id"],
                "lead_id": lead["id"],
                "session_id": event.session_id,
                "event_kind": event.event_kind,
                "metadata": event.metadata,
                "elapsed_ms": event.elapsed_ms,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        # Transient DB / partition issue — log and swallow. The
        # client fires one event/second at worst; losing a few of
        # them doesn't corrupt the overall signal.
        log.warning(
            "portal.track.insert_failed",
            slug=event.slug,
            event_kind=event.event_kind,
            err=str(exc),
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _beacon_rate_allows(session_id: str, slug: str) -> bool:
    """Fixed-window counter in Redis: 60 events/min per (session, slug).

    Fail-open on Redis errors — losing rate-limiting for a minute is
    far better than dropping telemetry under a transient outage. The
    beacon endpoint is not a security surface; malicious abuse is
    bounded by the partitioned table size + the event_kind whitelist.
    """
    try:
        r = get_redis()
        now = datetime.now(timezone.utc)
        key = (
            f"beacon:portal:{slug}:{session_id}:"
            f"{now.strftime('%Y%m%d%H%M')}"
        )
        pipe = r.pipeline()
        pipe.incr(key, 1)
        pipe.expire(key, _BEACON_KEY_TTL)
        results = await pipe.execute()
        used = int(results[0])
        return used <= _BEACON_RATE_PER_MIN
    except Exception as exc:  # noqa: BLE001
        log.warning("portal.track.rate_check_failed", err=str(exc))
        return True


# ---------------------------------------------------------------------------
# Conversion pixel — closed-loop attribution (Part B.6)
# ---------------------------------------------------------------------------
#
# Two surfaces:
#
#   GET  /lead/{slug}/pixel?stage={booked|quoted|won|lost}
#     Returns a 1×1 transparent GIF. Operators embed this URL in CRM
#     emails / workflow triggers. The request idempotently records one
#     ``conversions`` row (first write wins — ON CONFLICT DO NOTHING).
#     Safe to pre-fetch by mail clients and antivirus proxies.
#
#   POST /lead/{slug}/conversion  { "stage": "...", "amount_cents": ... }
#     JSON endpoint for Zapier / n8n / webhook pipelines that can carry
#     a deal value. Upserts the row so the amount can be corrected.
#     Both endpoints advance leads.pipeline_status when stage ∈ {won,lost}.
#
# Neither endpoint requires auth — the public_slug is the only secret
# the CRM needs. Malicious flooding is bounded by the UNIQUE constraint
# (one row per lead × stage) and the table's partitioned indexes.

# 1×1 transparent GIF (42 bytes) — pre-computed, no Pillow dependency.
_PIXEL_GIF: bytes = bytes([
    0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 0x01, 0x00, 0x01, 0x00,
    0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0x21,
    0xF9, 0x04, 0x01, 0x00, 0x00, 0x00, 0x00, 0x2C, 0x00, 0x00,
    0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0x02, 0x01, 0x44,
    0x00, 0x3B,
])

_PIXEL_RESPONSE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}

ConversionStage = Literal["booked", "quoted", "won", "lost"]


@router.get("/lead/{slug}/pixel")
async def conversion_pixel(
    slug: str,
    stage: ConversionStage = Query(
        ...,
        description="Funnel stage to record: booked | quoted | won | lost",
    ),
) -> Response:
    """1×1 transparent GIF that records a conversion event (Part B.6).

    Always returns the pixel — even on DB failure — so broken-image
    icons never appear in CRM emails. The insert is ON CONFLICT DO
    NOTHING so email-client pre-fetches and double-loads are safe.
    """
    await _upsert_conversion(slug=slug, stage=stage, amount_cents=None, source="pixel")
    return Response(
        content=_PIXEL_GIF,
        media_type="image/gif",
        headers=_PIXEL_RESPONSE_HEADERS,
    )


class ConversionBody(BaseModel):
    stage: ConversionStage
    amount_cents: int | None = Field(
        default=None,
        ge=0,
        description="Deal value in euro-cents (optional, updatable).",
    )


@router.post("/lead/{slug}/conversion", status_code=status.HTTP_202_ACCEPTED)
async def record_conversion(
    slug: str, body: ConversionBody
) -> dict[str, object]:
    """Record / update a conversion stage from a CRM webhook or Zapier.

    Unlike the pixel endpoint, this endpoint **upserts** — a second
    call for the same (lead, stage) updates ``amount_cents`` and
    ``closed_at``. Useful when the CRM first fires ``won`` with no
    value and later sends the actual deal amount.
    """
    ok = await _upsert_conversion(
        slug=slug,
        stage=body.stage,
        amount_cents=body.amount_cents,
        source="api",
        full_upsert=True,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"ok": True, "stage": body.stage}


async def _upsert_conversion(
    *,
    slug: str,
    stage: str,
    amount_cents: int | None,
    source: str,
    full_upsert: bool = False,
) -> bool:
    """Insert or upsert a conversion row, then advance pipeline_status.

    ``full_upsert=False``  → INSERT … ON CONFLICT DO NOTHING  (pixel)
    ``full_upsert=True``   → INSERT … ON CONFLICT DO UPDATE   (API)
    """
    sb = get_service_client()
    lead = _load_lead_by_slug(sb, slug)
    if lead is None:
        return False

    now_iso = datetime.now(timezone.utc).isoformat()
    row: dict[str, Any] = {
        "tenant_id": lead["tenant_id"],
        "lead_id": lead["id"],
        "stage": stage,
        "source": source,
        "closed_at": now_iso,
    }
    if amount_cents is not None:
        row["amount_cents"] = amount_cents

    try:
        if full_upsert:
            sb.table("conversions").upsert(
                row, on_conflict="lead_id,stage"
            ).execute()
        else:
            sb.table("conversions").insert(
                row, ignore_duplicates=True
            ).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "conversion.insert_failed",
            slug=slug,
            stage=stage,
            err=str(exc),
        )
        return False

    # Advance pipeline_status for terminal stages.
    current_status = lead.get("pipeline_status", "")
    if stage == "won" and current_status != "closed_won":
        try:
            sb.table("leads").update(
                {"pipeline_status": "closed_won"}
            ).eq("id", lead["id"]).execute()
        except Exception as exc:  # noqa: BLE001
            log.warning("conversion.status_advance_failed", err=str(exc))
    elif stage == "lost" and current_status not in {"closed_won", "closed_lost"}:
        try:
            sb.table("leads").update(
                {"pipeline_status": "closed_lost"}
            ).eq("id", lead["id"]).execute()
        except Exception as exc:  # noqa: BLE001
            log.warning("conversion.status_advance_failed", err=str(exc))

    _emit_public_event(
        sb,
        event_type="lead.conversion_recorded",
        tenant_id=lead["tenant_id"],
        lead_id=lead["id"],
        payload={
            "stage": stage,
            "source": source,
            "amount_cents": amount_cents,
        },
    )
    return True


# ---------------------------------------------------------------------------
# Opt-out
# ---------------------------------------------------------------------------


@router.post("/lead/{slug}/optout")
async def optout(slug: str) -> dict[str, object]:
    """One-click opt-out → enqueue compliance blacklist job.

    The ComplianceAgent is idempotent via ``UNIQUE(pii_hash)`` on
    ``global_blacklist``, so a bot pre-fetching this URL twice is
    safe. We set the lead pipeline to ``blacklisted`` synchronously
    so the portal can immediately render the confirmation page, then
    enqueue the compliance job asynchronously to cascade across
    tenants (their pii_hash may appear on other installers' territories).
    """
    sb = get_service_client()
    lead = (
        sb.table("leads")
        .select(
            "id, tenant_id, subject_id, pipeline_status, "
            "subjects(pii_hash)"
        )
        .eq("public_slug", slug)
        .limit(1)
        .execute()
    )
    if not lead.data:
        raise HTTPException(status_code=404, detail="Lead not found")
    row = lead.data[0]
    pii_hash = (row.get("subjects") or {}).get("pii_hash")

    already = row.get("pipeline_status") == LeadStatus.BLACKLISTED.value
    if not already:
        sb.table("leads").update(
            {"pipeline_status": LeadStatus.BLACKLISTED.value}
        ).eq("id", row["id"]).execute()

    if pii_hash:
        await enqueue(
            "compliance_task",
            {
                "pii_hash": pii_hash,
                "reason": BlacklistReason.USER_OPTOUT.value,
                "source": f"lead_portal:/{slug}",
                "notes": "One-click opt-out from public lead portal",
            },
            # Dedupe: one compliance run per (pii_hash, reason).
            job_id=f"compliance:{pii_hash}:{BlacklistReason.USER_OPTOUT.value}",
        )

    _emit_public_event(
        sb,
        event_type="lead.optout_requested",
        tenant_id=row["tenant_id"],
        lead_id=row["id"],
        payload={
            "slug": slug,
            "already_blacklisted": already,
            "has_pii_hash": bool(pii_hash),
        },
    )
    return {
        "ok": True,
        "status": LeadStatus.BLACKLISTED.value,
        "already": already,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_lead_by_slug(sb: Any, slug: str) -> dict[str, Any] | None:
    """Single-row fetch by slug — returns None (not raise) on miss."""
    res = (
        sb.table("leads")
        .select(
            "id, tenant_id, pipeline_status, dashboard_visited_at, "
            "whatsapp_initiated_at"
        )
        .eq("public_slug", slug)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    return res.data[0]


def _emit_public_event(
    sb: Any,
    *,
    event_type: str,
    tenant_id: str,
    lead_id: str,
    payload: dict[str, Any],
) -> None:
    """Best-effort events insert — never fails the HTTP handler."""
    try:
        sb.table("events").insert(
            {
                "tenant_id": tenant_id,
                "lead_id": lead_id,
                "event_type": event_type,
                "event_source": "route.public",
                "payload": payload,
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "public.event_emit_failed",
            event_type=event_type,
            err=str(exc),
        )
