"""Customer-facing "Avvia test pipeline" — demo tenant only.

The demo tenant (`tenants.is_demo = true`) gets a banner in `/leads`
that lets the prospect run the full discovery → scoring → creative
→ outreach pipeline against a company they pick. They get a real
email in their inbox, can click through to the public lead portal,
and the dashboard shows all tracking events live.

We cap usage at 3 lifetime attempts per tenant via
`tenants.demo_pipeline_test_remaining` (migration 0077). The cap
is enforced atomically on the SQL side — see `_decrement_attempts`.

This module deliberately mirrors the shape of the super-admin
``POST /v1/admin/seed-test-candidate`` endpoint so we don't fork
the pipeline runner. The differences are:

  * Auth gate: tenant-scoped (any role) + ``is_demo`` flag, NOT
    ``super_admin``. The prospect doesn't have a super-admin role
    and shouldn't need one.
  * Counter: every successful run decrements
    ``demo_pipeline_test_remaining``. 0 → 429.
  * Geocoding: the operator types one address; we forward-geocode
    via Mapbox to fill in lat/lng/cap/comune/provincia. Admin seed
    requires lat/lng inline because it's used by ops with known coords.
  * From-address: derived from the tenant's verified inbox or
    ``email_from_domain``. Falls back to the legacy
    ``outreach@{email_from_domain}`` shape when no inbox is configured
    yet — same resolution as `OutreachAgent`.

Surface area:

    POST /v1/demo/test-pipeline    — run the full pipeline (decrement counter)
    POST /v1/demo/geocode-preview  — forward-geocode an address (no decrement)

The geocode-preview endpoint exists so the dialog can show the
prospect a map pin + "Indirizzo riconosciuto" badge before they
commit one of their 3 attempts. It does NOT decrement the counter.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Any

import geohash  # type: ignore[import-untyped]
import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ..agents.creative import CreativeAgent, CreativeInput
from ..agents.outreach import OutreachAgent, OutreachInput
from ..agents.scoring import ScoringAgent, ScoringInput
from ..core.config import settings
from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client
from ..models.enums import OutreachChannel, RoofDataSource, RoofStatus, SubjectType
from ..services.italian_business_service import AtokaProfile
from ..services.mapbox_service import MapboxError, forward_geocode
from ..services.building_identification import (
    identify_building,
    match_to_operating_site,
)
from ..services.operating_site_resolver import (
    OperatingSite,
    resolve_operating_site,
)

log = get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class GeocodePreviewRequest(BaseModel):
    """Single-field address preview. The frontend dialog hits this on
    blur of the indirizzo input so we can show the prospect what we
    actually resolved before they commit an attempt.
    """

    address: str = Field(min_length=4, max_length=300)


class GeocodePreviewResponse(BaseModel):
    found: bool
    lat: float | None = None
    lng: float | None = None
    formatted: str | None = None
    cap: str | None = None
    comune: str | None = None
    provincia: str | None = None
    relevance: float | None = None
    # Human-friendly note shown alongside the map pin in the dialog.
    notes: str | None = None


class DemoTestPipelineRequest(BaseModel):
    """Inputs for the customer-facing test run.

    All fields are required EXCEPT lat/lng (we geocode the HQ address
    server-side; admin seed asks for lat/lng inline because ops paste
    them from Atoka / Maps directly).
    """

    vat_number: str = Field(min_length=5, max_length=30)
    legal_name: str = Field(min_length=1, max_length=255)
    ateco_code: str | None = Field(default=None, max_length=20)

    # HQ address — single field, geocoded server-side.
    hq_address: str = Field(min_length=4, max_length=300)

    decision_maker_name: str = Field(min_length=1, max_length=120)
    decision_maker_role: str | None = Field(default=None, max_length=120)
    decision_maker_email: str = Field(
        min_length=5,
        max_length=320,
        description="Decision-maker email (used for personalisation in copy).",
    )
    recipient_email: str = Field(
        min_length=5,
        max_length=320,
        description=(
            "Where the test email is actually delivered. "
            "Usually the prospect's own inbox so they can see the result land."
        ),
    )
    inbox_id: str | None = Field(
        default=None,
        description=(
            "Optional UUID of the tenant_inboxes row to send FROM. When set, "
            "OutreachAgent pins the selector to this single inbox so the "
            "prospect can pick whether the demo email arrives 'from Alfonso' "
            "vs 'from Gaetano'. Defaults to None → round-robin across all "
            "active inboxes for the tenant (legacy behaviour)."
        ),
    )
    # Building Identification Cascade (BIC) — optional pre-resolved coords.
    # When the dashboard ran POST /v1/demo/identify-building (and possibly
    # POST /v1/demo/confirm-building) before this submit, it sends back the
    # winning lat/lng so the pipeline skips the cascade entirely and uses
    # those coords directly. None = run the cascade inline (legacy path).
    confirmed_building_lat: float | None = Field(
        default=None,
        description=(
            "Latitude of the user-confirmed (or auto-resolved high-confidence) "
            "building from the BIC preview. When set together with "
            "confirmed_building_lng, the pipeline skips the operating-site "
            "cascade and uses these coords for the Roof + Solar API calls."
        ),
    )
    confirmed_building_lng: float | None = Field(
        default=None,
        description="Companion to confirmed_building_lat.",
    )


class DemoTestPipelineResponse(BaseModel):
    lead_id: str
    # Tracker for the async creative+outreach tail. The dashboard polls
    # GET /v1/demo/pipeline-runs/{run_id} on this id to surface progress
    # and — crucially — failures that happen after we 202.
    run_id: str
    public_slug: str | None = None
    eta_seconds: int = 90
    attempts_remaining: int


class DemoPipelineRunResponse(BaseModel):
    """Polled by the dialog to surface the async pipeline state.

    `status` advances ``scoring → creative → outreach → done`` on the
    happy path, or jumps to ``failed`` with ``failed_step`` set. The
    dialog only flips to a success toast on ``done``; on ``failed`` it
    shows ``error_message`` and the user has already been refunded.

    Beyond the pipeline state, we also project the email-delivery truth
    (``email_status`` / ``email_status_detail``) and the rooftop
    identification provenance (``roof_source`` / ``roof_confidence``)
    so the dialog can keep polling past ``done`` until Resend confirms
    the delivery — that's the only way to tell ``DEMO_EMAIL_RECIPIENT_OVERRIDE``
    redirects and silent bounces apart from a real successful send.
    """

    id: str
    lead_id: str | None
    status: str
    failed_step: str | None
    error_message: str | None
    notes: str | None
    updated_at: str
    # Delivery truth — populated from outreach_sends after the
    # OutreachAgent has inserted the row, then mutated by the Resend
    # webhook (TrackingAgent) when delivered/bounced events land.
    email_status: str | None = None              # SENT|DELIVERED|FAILED|...
    email_status_detail: str | None = None        # bounce_reason, complaint code
    email_recipient: str | None = None            # actual To: address (post override)
    email_message_id: str | None = None
    # Roof identification cascade outcome (subjects.sede_operativa_*).
    roof_source: str | None = None                # atoka|website_scrape|google_places|mapbox_hq|osm_snap|unresolved
    roof_confidence: str | None = None            # high|medium|low|none


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _lookup_mock_enrichment(vat_number: str) -> dict[str, Any] | None:
    """Pull pre-computed enrichment for a known demo VAT number.

    The demo path skips Atoka (cost + latency) but we still want the
    resulting lead row to look as fully-fleshed as a real production
    lead — phone with source, ATECO description, revenue, employees,
    LinkedIn, sede operativa coords. The `demo_mock_enrichment` table
    holds these for the small set of VAT numbers we expect customers
    to type during a sales call (the MULTILOG default + a few seeds).

    Returns None when the VAT isn't seeded; the caller falls through
    to the "leave nulls" behaviour. Best-effort: a lookup failure
    must not bubble up because then a stray DB blip would burn the
    user's attempt.
    """
    if not vat_number:
        return None
    try:
        sb = get_service_client()
        res = (
            sb.table("demo_mock_enrichment")
            .select(
                "vat_number, decision_maker_phone, decision_maker_phone_source, "
                "ateco_description, yearly_revenue_cents, employees, "
                "linkedin_url, sede_operativa_address, sede_operativa_lat, "
                "sede_operativa_lng"
            )
            .eq("vat_number", vat_number.strip())
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "demo.mock_enrichment_lookup_failed",
            vat_number=vat_number,
            err=str(exc),
        )
        return None


def _create_run(tenant_id: str) -> str:
    """Insert a fresh demo_pipeline_runs row in 'scoring' state.

    Called at the very top of the request so we have a tracker even if
    scoring itself blows up. Returns the new run id so we can plumb it
    through to the response and the background task.
    """
    sb = get_service_client()
    res = (
        sb.table("demo_pipeline_runs")
        .insert({"tenant_id": tenant_id, "status": "scoring"})
        .execute()
    )
    rows = res.data or []
    if not rows:
        # Best-effort: the insert should never fail under normal load.
        # Return a deterministic stub so the route can still return 202.
        # The dashboard polling will get a 404 and surface a generic
        # "stato non disponibile" — preferable to crashing the request.
        return ""
    return rows[0]["id"]


def _update_run(
    run_id: str,
    *,
    status: str | None = None,
    lead_id: str | None = None,
    failed_step: str | None = None,
    error_message: str | None = None,
    notes: str | None = None,
) -> None:
    """Patch a demo_pipeline_runs row. Best-effort: a failed update
    should never compound the user's primary failure, so we swallow
    exceptions after logging."""
    if not run_id:
        return
    payload: dict[str, Any] = {}
    if status is not None:
        payload["status"] = status
    if lead_id is not None:
        payload["lead_id"] = lead_id
    if failed_step is not None:
        payload["failed_step"] = failed_step
    if error_message is not None:
        # Truncate so a stack trace doesn't bloat the row beyond what
        # the dialog can sensibly render.
        payload["error_message"] = error_message[:500]
    if notes is not None:
        payload["notes"] = notes[:500]
    if not payload:
        return
    sb = get_service_client()
    try:
        sb.table("demo_pipeline_runs").update(payload).eq("id", run_id).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("demo.run_update_failed", run_id=run_id, err=str(exc))


async def _require_demo_tenant(tenant_id: str) -> dict[str, Any]:
    """Fetch the tenant row and 403 unless it's flagged as demo.

    Returns the row so callers can read `email_from_domain`,
    `email_from_name`, etc., without a second roundtrip.
    """
    sb = get_service_client()
    res = await asyncio.to_thread(
        lambda: sb.table("tenants")
        .select(
            "id, is_demo, demo_pipeline_test_remaining, "
            "email_from_domain, email_from_domain_verified_at, email_from_name"
        )
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Tenant not found")
    row = rows[0]
    if not row.get("is_demo"):
        # We deliberately surface a generic 403 — non-demo tenants
        # shouldn't even know this endpoint exists.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo pipeline test is not enabled for this tenant.",
        )
    return row


def _decrement_attempts(tenant_id: str) -> int | None:
    """Atomic decrement. Returns the new remaining count, or None when
    the tenant was already at 0 (caller should 429).

    Implemented via a single conditional UPDATE so we never observe
    a race where two concurrent attempts both think they have a
    remaining slot. PostgREST returns the post-update row in `data`
    when we ask for it via `Prefer: return=representation` (default
    on the supabase-py client we use).
    """
    sb = get_service_client()
    try:
        res = (
            sb.rpc(
                "demo_decrement_pipeline_attempts",
                {"p_tenant_id": tenant_id},
            ).execute()
        )
        # The RPC returns NULL when the counter was already 0.
        val = res.data
        if val is None:
            return None
        # Some PostgREST shapes wrap scalar returns in a list of dicts.
        if isinstance(val, list):
            if not val:
                return None
            row = val[0]
            return row if isinstance(row, int) else row.get("remaining")
        if isinstance(val, dict):
            return val.get("remaining")
        return int(val)
    except Exception as exc:  # noqa: BLE001
        # Fallback: do the decrement in Python via two queries. Not
        # truly atomic, but safe for the demo path because concurrent
        # attempts from the same tenant are vanishingly rare (one
        # browser, one operator) and the `demo_pipeline_test_remaining
        # >= 0` CHECK constraint stops us from ever going negative.
        log.warning("demo.decrement_rpc_failed", err=str(exc), tenant_id=tenant_id)
        cur = (
            sb.table("tenants")
            .select("demo_pipeline_test_remaining")
            .eq("id", tenant_id)
            .limit(1)
            .execute()
        )
        if not cur.data:
            return None
        remaining_now: int = cur.data[0].get("demo_pipeline_test_remaining") or 0
        if remaining_now <= 0:
            return None
        sb.table("tenants").update(
            {"demo_pipeline_test_remaining": remaining_now - 1}
        ).eq("id", tenant_id).gt("demo_pipeline_test_remaining", 0).execute()
        return remaining_now - 1


# ---------------------------------------------------------------------------
# Geocode preview
# ---------------------------------------------------------------------------


@router.post("/geocode-preview", response_model=GeocodePreviewResponse)
async def demo_geocode_preview(
    ctx: CurrentUser, body: GeocodePreviewRequest
) -> GeocodePreviewResponse:
    """Preview a forward-geocode result. No counter decrement, no DB writes.

    Used by the test-pipeline dialog to show the prospect what
    Mapbox actually resolved BEFORE they commit one of their 3 lifetime
    attempts. Returns `found=false` with a `notes` string when relevance
    is too low or the API has nothing — the dialog can then prompt for
    a more precise address.
    """
    tenant_id = require_tenant(ctx)
    await _require_demo_tenant(tenant_id)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            result = await forward_geocode(
                body.address.strip(),
                client=client,
                # Loosen min_relevance a bit for the preview — the
                # prospect can still see the resolved result and decide
                # whether to refine. The actual run uses the standard
                # 0.75 threshold.
                min_relevance=0.5,
            )
    except MapboxError as exc:
        log.warning("demo.geocode_preview_mapbox_error", err=str(exc))
        raise HTTPException(
            status_code=502,
            detail="Mapbox geocoding failed. Try again in a few seconds.",
        ) from exc

    if result is None:
        return GeocodePreviewResponse(
            found=False,
            notes=(
                "Indirizzo non riconosciuto con sufficiente precisione. "
                "Prova ad aggiungere il numero civico, il CAP, o la città."
            ),
        )

    return GeocodePreviewResponse(
        found=True,
        lat=result.lat,
        lng=result.lng,
        formatted=result.address,
        cap=result.cap,
        comune=result.comune,
        provincia=result.provincia,
        relevance=result.relevance,
    )


# ---------------------------------------------------------------------------
# Inbox picker — list active senders for the demo tenant
# ---------------------------------------------------------------------------


class DemoInboxOption(BaseModel):
    """One row of the sender (mittente) dropdown in the test dialog."""

    id: str
    email: str
    display_name: str | None = None


class DemoInboxListResponse(BaseModel):
    inboxes: list[DemoInboxOption]


@router.get("/inboxes", response_model=DemoInboxListResponse)
async def demo_list_inboxes(ctx: CurrentUser) -> DemoInboxListResponse:
    """Return the active inboxes the demo tenant can send FROM.

    Used by the test-pipeline dialog to render a "scegli mittente"
    dropdown so the prospect can pick which sender (Alfonso vs Gaetano,
    say) the demo email will appear to come from. Returns an empty list
    when the tenant has no inbox rows yet — the dialog falls back to
    the legacy ``email_from_domain`` path silently.
    """
    tenant_id = require_tenant(ctx)
    await _require_demo_tenant(tenant_id)
    sb = get_service_client()
    res = await asyncio.to_thread(
        lambda: sb.table("tenant_inboxes")
        .select("id, email, display_name")
        .eq("tenant_id", tenant_id)
        .eq("active", True)
        .order("display_name", desc=False)
        .execute()
    )
    rows = res.data or []
    return DemoInboxListResponse(
        inboxes=[
            DemoInboxOption(
                id=str(r["id"]),
                email=r["email"],
                display_name=r.get("display_name"),
            )
            for r in rows
        ]
    )


# ---------------------------------------------------------------------------
# Test pipeline — the headline endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/test-pipeline",
    response_model=DemoTestPipelineResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def demo_test_pipeline(
    ctx: CurrentUser, body: DemoTestPipelineRequest
) -> DemoTestPipelineResponse:
    """Run the full pipeline (scoring → creative → outreach) for one
    customer-supplied company. Decrements the demo attempt counter.

    Pipeline stages run synchronously in-request, same as the admin
    ``seed-test-candidate`` endpoint. Total wall-clock is dominated
    by Remotion render (~60-90s) — the response 202s with the lead_id
    only AFTER all three agents have completed, so the dashboard can
    immediately link to a fully-rendered lead detail page.

    Idempotent on (tenant_id, vat_number) via the geohash unique
    index on `roofs` — running the same input twice from a buggy retry
    upserts rather than duplicates, but DOES decrement the counter
    twice. The counter is the rate limit; uniqueness is for data hygiene.
    """
    tenant_id = require_tenant(ctx)
    tenant_row = await _require_demo_tenant(tenant_id)

    # 0. Validate the inbox pin (if any) belongs to this tenant. We do this
    #    BEFORE decrementing the counter so a stale UUID from the dropdown
    #    doesn't burn an attempt — the dialog can show a friendly 422 and
    #    the prospect re-picks. A genuine mid-flight inbox deletion is rare
    #    enough that we don't try to recover automatically.
    if body.inbox_id:
        sb_check = get_service_client()
        try:
            inbox_check = await asyncio.to_thread(
                lambda: sb_check.table("tenant_inboxes")
                .select("id, active")
                .eq("id", body.inbox_id)
                .eq("tenant_id", tenant_id)
                .limit(1)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "demo.inbox_validate_failed",
                tenant_id=tenant_id,
                inbox_id=body.inbox_id,
                err=str(exc),
            )
            raise HTTPException(
                status_code=502,
                detail="Errore nella verifica del mittente. Riprova fra qualche secondo.",
            ) from exc
        rows = inbox_check.data or []
        if not rows or not rows[0].get("active"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Il mittente selezionato non è più disponibile. "
                    "Ricarica la pagina e scegli un'altra inbox."
                ),
            )

    # 1. Atomic decrement — fail fast on exhaustion before doing any work.
    remaining = await asyncio.to_thread(_decrement_attempts, tenant_id)
    if remaining is None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Hai esaurito i 3 tentativi disponibili per il test pipeline.",
        )

    # Run tracker — created right after the counter decrement so even a
    # failure in geocoding/roof/subject upsert leaves a row the user can
    # poll. The tracker carries the run forward into the async tail
    # (creative + outreach), which is otherwise invisible to the dialog.
    run_id: str = await asyncio.to_thread(_create_run, tenant_id)

    # 2. Geocode the address. Refund the attempt if the address is bad.
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            geo = await forward_geocode(
                body.hq_address.strip(),
                client=client,
                min_relevance=0.5,
            )
    except MapboxError as exc:
        await asyncio.to_thread(_refund_attempt, tenant_id)
        await asyncio.to_thread(
            _update_run,
            run_id,
            status="failed",
            failed_step="scoring",
            error_message=f"Mapbox geocoding failed: {exc}",
        )
        log.warning("demo.test_pipeline_geocode_error", err=str(exc))
        raise HTTPException(
            status_code=502,
            detail="Mapbox geocoding failed. Riprova fra qualche secondo.",
        ) from exc

    if geo is None:
        await asyncio.to_thread(_refund_attempt, tenant_id)
        await asyncio.to_thread(
            _update_run,
            run_id,
            status="failed",
            failed_step="scoring",
            error_message="Indirizzo non riconosciuto da Mapbox.",
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Indirizzo non riconosciuto. Aggiungi il numero civico "
                "o il CAP e riprova."
            ),
        )

    # 3. Run the synchronous pipeline. Reuses the exact same shape as
    #    `seed_test_candidate` so any change to the pipeline runner
    #    propagates here without code duplication.
    sb = get_service_client()
    now = datetime.now(timezone.utc)

    # Resolve the operating site (sede operativa) — Sprint Demo Polish
    # Phase B. The cascade is identical to the production one in
    # ``level4_solar_gate``: Atoka → website scrape → Google Places →
    # Mapbox HQ centroid. For demo runs the "Atoka tier" is fed by
    # ``demo_mock_enrichment`` so the seeded MULTILOG VAT lands on a
    # hand-curated rooftop, while un-seeded VATs fall through to the
    # real cascade against the company website / Google Places.
    early_mock = _lookup_mock_enrichment(body.vat_number) or {}
    demo_profile = AtokaProfile(
        vat_number=body.vat_number,
        legal_name=body.legal_name,
        ateco_code=body.ateco_code,
        ateco_description=early_mock.get("ateco_description"),
        yearly_revenue_cents=early_mock.get("yearly_revenue_cents"),
        employees=early_mock.get("employees"),
        website_domain=None,  # demo form doesn't ask for the website
        decision_maker_name=body.decision_maker_name,
        decision_maker_role=body.decision_maker_role,
        linkedin_url=early_mock.get("linkedin_url"),
        phone=early_mock.get("decision_maker_phone"),
        hq_address=geo.address or body.hq_address,
        hq_cap=geo.cap,
        hq_city=geo.comune,
        hq_province=geo.provincia,
        hq_lat=geo.lat,
        hq_lng=geo.lng,
        sede_operativa_address=early_mock.get("sede_operativa_address"),
        sede_operativa_lat=(
            float(early_mock["sede_operativa_lat"])
            if early_mock.get("sede_operativa_lat") is not None
            else None
        ),
        sede_operativa_lng=(
            float(early_mock["sede_operativa_lng"])
            if early_mock.get("sede_operativa_lng") is not None
            else None
        ),
    )
    # Pass the Atoka-recorded website (when present) so the resolver can
    # fall through to tier 2 (website scrape) if Atoka tier 1 fails the
    # Solar-API validation step. Without a domain hint the cascade jumps
    # straight to Google Places, which is noisier for B2B SMEs.
    website_domain_hint: str | None = None
    if demo_profile is not None and getattr(demo_profile, "website_url", None):
        try:
            from urllib.parse import urlparse

            parsed = urlparse(str(demo_profile.website_url))
            website_domain_hint = (parsed.hostname or "").lstrip("www.") or None
        except Exception:  # noqa: BLE001
            website_domain_hint = None

    # ─── Building Identification ─────────────────────────────────────
    # If the dashboard ran the BIC preview (POST /v1/demo/identify-
    # building) and possibly POST /v1/demo/confirm-building, the
    # confirmed coords come back attached to this submit. Trust them
    # unconditionally — the user / cascade has already done the
    # disambiguation work and we'd just waste API budget re-running.
    #
    # Fallback (legacy path): no confirmed coords → run the 4-tier
    # operating-site resolver inline. This preserves backward
    # compatibility with API consumers that don't yet call
    # /identify-building first.
    resolved_site: OperatingSite
    if (
        body.confirmed_building_lat is not None
        and body.confirmed_building_lng is not None
    ):
        log.info(
            "demo.confirmed_building_used",
            vat_number=body.vat_number,
            lat=body.confirmed_building_lat,
            lng=body.confirmed_building_lng,
        )
        resolved_site = OperatingSite(
            lat=body.confirmed_building_lat,
            lng=body.confirmed_building_lng,
            address=geo.address or body.hq_address,
            cap=geo.cap,
            city=geo.comune,
            province=geo.provincia,
            source="user_confirmed",
            confidence="high",
        )
    else:
        # No confirmed coords from the dialog → run the full BIC
        # inline. Demo runs always have a human waiting on the dialog,
        # so we enable Vision (~$0.025) for the unlock case where
        # textual signals don't converge. Production runs in
        # ``level4_solar_gate.py`` use the same orchestrator with
        # ``enable_vision=False`` to keep cron costs predictable.
        async with httpx.AsyncClient(timeout=30.0) as resolver_client:
            match = await identify_building(
                vat_number=body.vat_number,
                legal_name=body.legal_name,
                profile=demo_profile,
                website_domain=website_domain_hint,
                hq_address=geo.address or body.hq_address,
                hq_city=geo.comune,
                hq_province=geo.provincia,
                ateco_code=body.ateco_code,
                http_client=resolver_client,
                enable_vision=True,
            )
        resolved_site = match_to_operating_site(match)

    if resolved_site.has_coords:
        roof_lat = resolved_site.lat
        roof_lng = resolved_site.lng
        roof_address = (
            resolved_site.address or geo.address or body.hq_address
        )
        log.info(
            "demo.operating_site_resolved",
            vat_number=body.vat_number,
            source=resolved_site.source,
            lat=roof_lat,
            lng=roof_lng,
        )
    else:
        roof_lat = geo.lat
        roof_lng = geo.lng
        roof_address = geo.address or body.hq_address

    gh = geohash.encode(roof_lat, roof_lng, precision=9)
    roof_payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "lat": roof_lat,
        "lng": roof_lng,
        "geohash": gh,
        "address": roof_address,
        "cap": geo.cap,
        "comune": geo.comune,
        "provincia": geo.provincia,
        # Use median Italian rooftop dimensions for the synthetic case
        # — these match the defaults in `SolarOverride`. The customer
        # cares about the email landing, not the kWp accuracy.
        "area_sqm": 180.0,
        "estimated_kwp": 30.0,
        "estimated_yearly_kwh": 36000,
        "exposure": "south",
        "shading_score": 0.85,
        "has_existing_pv": False,
        "data_source": RoofDataSource.GOOGLE_SOLAR.value,
        "classification": SubjectType.B2B.value,
        "status": RoofStatus.DISCOVERED.value,
        "scan_cost_cents": 0,
        "raw_data": {
            "demo_test_pipeline": True,
            "vat_number": body.vat_number,
            "inserted_at": now.isoformat(),
            "supplied_address": body.hq_address,
        },
    }

    try:
        roof_res = await asyncio.to_thread(
            lambda: sb.table("roofs")
            .upsert(roof_payload, on_conflict="tenant_id,geohash")
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        await asyncio.to_thread(_refund_attempt, tenant_id)
        await asyncio.to_thread(
            _update_run,
            run_id,
            status="failed",
            failed_step="scoring",
            error_message=f"roof upsert failed: {exc}",
        )
        log.error("demo.roof_upsert_failed", tenant_id=tenant_id, err=str(exc))
        raise HTTPException(
            status_code=502,
            detail="Errore nel salvataggio del tetto. Riprova fra qualche secondo.",
        ) from exc
    if not roof_res.data:
        await asyncio.to_thread(_refund_attempt, tenant_id)
        await asyncio.to_thread(
            _update_run,
            run_id,
            status="failed",
            failed_step="scoring",
            error_message="roof upsert returned no rows",
        )
        raise HTTPException(status_code=502, detail="Failed to upsert roof row.")
    roof_id: str = roof_res.data[0]["id"]

    pii_raw = f"{body.legal_name.lower().strip()}|{body.vat_number.lower().strip()}"
    pii_hash = hashlib.sha256(pii_raw.encode()).hexdigest()

    # Pull pre-computed enrichment (phone, ATECO description, revenue,
    # employees, LinkedIn) for this VAT number — see
    # _lookup_mock_enrichment docstring. Empty when the user typed an
    # un-seeded VAT; we still write a usable subject row, just without
    # the enriched fields.
    mock = _lookup_mock_enrichment(body.vat_number) or {}
    if mock:
        log.info(
            "demo.mock_enrichment_applied",
            vat_number=body.vat_number,
            fields=sorted(k for k, v in mock.items() if v is not None),
        )
    else:
        log.info(
            "demo.mock_enrichment_missing",
            vat_number=body.vat_number,
            note=(
                "no row in demo_mock_enrichment for this VAT — "
                "subject will be written without enriched fields"
            ),
        )

    # NOTE: `subjects` has no `raw_data` jsonb column (unlike `roofs`).
    # We deliberately keep this payload aligned with the table schema —
    # adding stray columns trips PostgREST with a 400 that the supabase
    # client raises as APIError, which used to bubble up to the browser
    # as a generic "Failed to fetch" because the request was already
    # streaming. Anything we'd want to preserve about the supplied
    # emails is already in the API logs ("demo.test_pipeline_started").
    subject_payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "roof_id": roof_id,
        "type": SubjectType.B2B.value,
        "business_name": body.legal_name,
        "vat_number": body.vat_number,
        "ateco_code": body.ateco_code,
        # Mock-sourced enrichment overlay — null-safe; the .get() defaults
        # to None when the VAT isn't in `demo_mock_enrichment`.
        "ateco_description": mock.get("ateco_description"),
        "yearly_revenue_cents": mock.get("yearly_revenue_cents"),
        "employees": mock.get("employees"),
        "linkedin_url": mock.get("linkedin_url"),
        "decision_maker_name": body.decision_maker_name,
        "decision_maker_role": body.decision_maker_role,
        "decision_maker_phone": mock.get("decision_maker_phone"),
        "decision_maker_phone_source": mock.get("decision_maker_phone_source"),
        # Recipient — what OutreachAgent will email. We mark it
        # verified so NeverBounce gating doesn't skip the send (the
        # prospect typed it themselves; trust > probabilistic check).
        #
        # The form's ``recipient_email`` is the source of truth — the
        # prospect / operator typed where the test email should land and
        # we honour that exactly. (Earlier versions of this code applied
        # a server-side ``DEMO_EMAIL_RECIPIENT_OVERRIDE`` redirect to a
        # QA inbox; we removed it because operators couldn't tell the
        # silent redirect apart from a real bounce — the dialog showed
        # "Email accettata · in attesa di consegna" while the prospect's
        # mailbox stayed empty.)
        "decision_maker_email": body.recipient_email,
        "decision_maker_email_verified": True,
        # Tag the data_sources array so ops can filter "leads from
        # demo with mock enrichment vs without" without a join.
        "data_sources": (
            ["demo_test_pipeline", "demo_mock_enrichment"]
            if mock
            else ["demo_test_pipeline"]
        ),
        "enrichment_cost_cents": 0,
        "enrichment_completed_at": now.isoformat(),
        "pii_hash": pii_hash,
    }
    # Stamp the cascade outcome onto the subject row so the dashboard
    # can show a "Sede operativa: Atoka / Sito web / Google Places /
    # Centroide HQ" badge identical to a production lead.
    if resolved_site.source != "unresolved":
        subject_payload.update(
            {
                "sede_operativa_address": resolved_site.address,
                "sede_operativa_cap": resolved_site.cap,
                "sede_operativa_city": resolved_site.city,
                "sede_operativa_province": resolved_site.province,
                "sede_operativa_lat": resolved_site.lat,
                "sede_operativa_lng": resolved_site.lng,
                "sede_operativa_source": resolved_site.source,
                # Persist the confidence bucket alongside the source so the
                # CreativeAgent's hard gate (Sprint 2.1) and the
                # /admin/demo-runs roof badge can both read it back.
                "sede_operativa_confidence": resolved_site.confidence,
            }
        )
    try:
        subject_res = await asyncio.to_thread(
            lambda: sb.table("subjects")
            .upsert(subject_payload, on_conflict="tenant_id,roof_id")
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        # Refund the attempt so a schema/validation bug doesn't burn the
        # prospect's 3 lifetime tries. We always log the actual cause so
        # we can fix the payload — silent retries make this class of bug
        # very expensive to debug.
        await asyncio.to_thread(_refund_attempt, tenant_id)
        await asyncio.to_thread(
            _update_run,
            run_id,
            status="failed",
            failed_step="scoring",
            error_message=f"subject upsert failed: {exc}",
        )
        log.error(
            "demo.subject_upsert_failed",
            tenant_id=tenant_id,
            err=str(exc),
            payload_keys=list(subject_payload.keys()),
        )
        raise HTTPException(
            status_code=502,
            detail="Errore nel salvataggio dell'anagrafica. Riprova fra qualche secondo.",
        ) from exc
    if not subject_res.data:
        await asyncio.to_thread(_refund_attempt, tenant_id)
        await asyncio.to_thread(
            _update_run,
            run_id,
            status="failed",
            failed_step="scoring",
            error_message="subject upsert returned no rows",
        )
        raise HTTPException(status_code=502, detail="Failed to upsert subject row.")
    subject_id: str = subject_res.data[0]["id"]

    # ── Scoring (sync — produces lead_id we need to return) ─────────
    # Scoring is fast (<5s) for a fresh subject with no enrichment to
    # backfill: it computes ICP fit + size band + writes a lead row.
    # Creative + Outreach (the slow ~85s pair) run async in the
    # background so the browser fetch doesn't time out behind Railway's
    # HTTP proxy (~100s) or any CDN in front of the API.
    try:
        scoring_out = await ScoringAgent().run(
            ScoringInput(
                tenant_id=tenant_id,
                roof_id=roof_id,
                subject_id=subject_id,
            )
        )
    except Exception as exc:  # noqa: BLE001
        await asyncio.to_thread(_refund_attempt, tenant_id)
        await asyncio.to_thread(
            _update_run,
            run_id,
            status="failed",
            failed_step="scoring",
            error_message=f"scoring agent failed: {exc}",
        )
        log.error(
            "demo.scoring_failed",
            tenant_id=tenant_id,
            subject_id=subject_id,
            err=str(exc),
        )
        raise HTTPException(
            status_code=502,
            detail="Errore durante lo scoring del lead. Riprova fra qualche secondo.",
        ) from exc
    lead_id: str | None = scoring_out.lead_id
    if not lead_id:
        await asyncio.to_thread(_refund_attempt, tenant_id)
        await asyncio.to_thread(
            _update_run,
            run_id,
            status="failed",
            failed_step="scoring",
            error_message="scoring agent did not produce a lead row",
        )
        raise HTTPException(
            status_code=502,
            detail="Scoring did not produce a lead row.",
        )

    # Scoring done — flip the tracker to 'creative' and attach the lead
    # id so the dashboard toast can deep-link straight to /leads/{id}.
    await asyncio.to_thread(
        _update_run, run_id, status="creative", lead_id=lead_id
    )

    # ── Creative + Outreach (background — rendering ~60s, send ~5s) ─
    # We deliberately do NOT await this. The endpoint returns 202 with
    # the lead_id so the dashboard toast can deep-link immediately;
    # the user lands on /leads/{id} where `lead-timeline-live.tsx`
    # streams real-time events as creative/outreach progress (rendering
    # done → email queued → email sent → recipient opens, etc).
    asyncio.create_task(
        _run_creative_and_outreach_background(
            tenant_id=tenant_id,
            lead_id=lead_id,
            run_id=run_id,
            inbox_id=body.inbox_id,
        )
    )

    # 4. Look up the public_slug so the dashboard toast can deep-link
    #    straight into the lead detail page.
    lead_row = await asyncio.to_thread(
        lambda: sb.table("leads")
        .select("public_slug")
        .eq("id", lead_id)
        .limit(1)
        .execute()
    )
    public_slug = (lead_row.data or [{}])[0].get("public_slug") if lead_row.data else None

    log.info(
        "demo.test_pipeline_started",
        tenant_id=tenant_id,
        lead_id=lead_id,
        attempts_remaining=remaining,
        from_domain=tenant_row.get("email_from_domain"),
    )

    return DemoTestPipelineResponse(
        lead_id=lead_id,
        run_id=run_id,
        public_slug=public_slug,
        attempts_remaining=remaining,
    )


async def _run_creative_and_outreach_background(
    *, tenant_id: str, lead_id: str, run_id: str, inbox_id: str | None = None
) -> None:
    """Fire-and-forget runner for the slow tail of the demo pipeline.

    Spawned via `asyncio.create_task` so the HTTP response returns to
    the browser as soon as scoring completes. Errors are written to
    `demo_pipeline_runs.error_message` so the dashboard polling
    surfaces them — silent failures here mean the user sees a "Lead
    creato!" success while the email never went out.
    """
    # ── Creative ────────────────────────────────────────────────────
    creative_failed = False
    creative_out = None
    try:
        creative_out = await CreativeAgent().run(
            CreativeInput(
                tenant_id=tenant_id,
                lead_id=lead_id,
                force=True,
            )
        )
    except Exception as exc:  # noqa: BLE001
        creative_failed = True
        log.warning("demo.creative_error", lead_id=lead_id, err=str(exc))
        await asyncio.to_thread(
            _update_run,
            run_id,
            status="failed",
            failed_step="creative",
            error_message=f"Creative agent failed: {exc}",
        )

    # If creative blew up there's no point trying outreach — we'd send
    # an email with a broken/missing rendering. Stop here.
    if creative_failed:
        log.info("demo.test_pipeline_background_aborted", lead_id=lead_id)
        return

    # Annotate whether the GIF was actually produced. The Creative
    # agent silently falls back to the static "before" image when
    # Remotion is unreachable or the AI panel-paint failed; surface
    # that to the dialog so the user knows what they're getting.
    sb = get_service_client()
    try:
        lead_row = (
            sb.table("leads")
            .select("rendering_gif_url, rendering_image_url")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        rows = lead_row.data or []
        if rows and not rows[0].get("rendering_gif_url"):
            # Distinguish the two failure modes by which artefact made it
            # through. If the after-image is present, panel-paint AI
            # worked → the GIF skip is on the Remotion sidecar (most
            # likely VIDEO_RENDERER_URL not pointed at a deployed
            # service). If the after-image is also missing, the AI step
            # itself never produced a frame — surface CreativeAgent's
            # exact `reason` (e.g. ``ai_paint_error:create status=401 …``)
            # so the operator sees the precise cause in the dialog
            # instead of just "Replicate failed somehow".
            after_present = bool(rows[0].get("rendering_image_url"))
            agent_reason = (
                creative_out.reason
                if creative_out is not None and creative_out.reason
                else None
            )
            if after_present:
                note = (
                    "Email inviata con immagine statica. La pittura AI del "
                    "tetto è riuscita ma il sidecar GIF non ha risposto "
                    "(verifica VIDEO_RENDERER_URL e che il servizio "
                    "video-renderer sia online su Railway)."
                )
                if agent_reason:
                    note += f" — Dettaglio agente: {agent_reason}"
            else:
                if agent_reason:
                    note = (
                        "Email inviata con immagine statica: la pittura AI "
                        f"del tetto non ha prodotto un frame. Causa esatta: "
                        f"{agent_reason}"
                    )
                else:
                    note = (
                        "Email inviata con immagine statica: la pittura AI "
                        "del tetto non ha prodotto un frame (panel-paint AI "
                        "fallita: verifica REPLICATE_API_TOKEN e i log "
                        "creative.gif_fallback)."
                    )
            await asyncio.to_thread(_update_run, run_id, notes=note)
    except Exception as exc:  # noqa: BLE001
        log.warning("demo.gif_check_failed", lead_id=lead_id, err=str(exc))

    # Flip to 'outreach' so the dialog can show "Invio email…" while
    # the send is in flight.
    await asyncio.to_thread(_update_run, run_id, status="outreach")

    # ── Outreach ────────────────────────────────────────────────────
    try:
        await OutreachAgent().run(
            OutreachInput(
                tenant_id=tenant_id,
                lead_id=lead_id,
                channel=OutreachChannel.EMAIL,
                sequence_step=1,
                force=True,
                inbox_id=inbox_id,
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("demo.outreach_error", lead_id=lead_id, err=str(exc))
        await asyncio.to_thread(
            _update_run,
            run_id,
            status="failed",
            failed_step="outreach",
            error_message=f"Outreach agent failed: {exc}",
        )
        return

    # Happy path — everything done. The dialog now shows a success
    # toast with a deep link to the lead.
    await asyncio.to_thread(_update_run, run_id, status="done")
    log.info("demo.test_pipeline_background_completed", lead_id=lead_id)


@router.get(
    "/pipeline-runs/{run_id}",
    response_model=DemoPipelineRunResponse,
)
async def demo_pipeline_run_status(
    ctx: CurrentUser, run_id: str
) -> DemoPipelineRunResponse:
    """Polled by the dialog to surface async pipeline state.

    Tenant-scoped: a tenant can only read its own runs. The dialog
    polls every 2s for ~2 minutes; we expect ``done`` or ``failed``
    well within that window.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    # Step 1: fetch the demo_pipeline_runs row alone. Keeping this query
    # narrow shields the UI from PostgREST schema-cache lag whenever we
    # add columns on subjects (the embedded-resource select was failing
    # silently in production whenever a new column hadn't propagated yet).
    res = await asyncio.to_thread(
        lambda: sb.table("demo_pipeline_runs")
        .select("id, lead_id, status, failed_step, error_message, notes, updated_at")
        .eq("id", run_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Run not found")
    row = rows[0]

    # Step 2: best-effort lookup of the lead's subject. Soft-fail keeps
    # the polling endpoint healthy even if subjects/leads is briefly
    # unreachable — the dialog falls back to the "in attesa di consegna"
    # panel instead of erroring out the whole run.
    subject_data: dict[str, Any] = {}
    lead_id = row.get("lead_id")
    if lead_id:
        try:
            lead_res = await asyncio.to_thread(
                lambda: sb.table("leads")
                .select(
                    "subject_id, "
                    "subjects(decision_maker_email, sede_operativa_source, "
                    "sede_operativa_confidence)"
                )
                .eq("id", lead_id)
                .limit(1)
                .execute()
            )
            if lead_res.data:
                subj = lead_res.data[0].get("subjects") or {}
                if isinstance(subj, list):
                    subj = subj[0] if subj else {}
                subject_data = subj or {}
        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            log.debug(
                "demo.run_subject_lookup_failed",
                err_type=type(exc).__name__,
                err=str(exc)[:120],
                run_id=run_id,
            )

    # ---- Email delivery state ------------------------------------------
    # Look up the most recent outreach_sends row for this lead. We cap to
    # one extra round-trip so polling stays cheap (the dialog hits this
    # endpoint every 2s for up to 3 minutes).
    email_state: dict[str, Any] = {}
    if lead_id:
        try:
            send_res = await asyncio.to_thread(
                lambda: sb.table("outreach_sends")
                .select("status, failure_reason, email_message_id, sent_at")
                .eq("lead_id", lead_id)
                .order("created_at", desc=False)
                .limit(1)
                .execute()
            )
            if send_res.data:
                email_state = send_res.data[0] or {}
        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            log.debug("demo.run_email_lookup_failed", err=str(exc), run_id=run_id)

    return DemoPipelineRunResponse(
        id=row["id"],
        lead_id=lead_id,
        status=row["status"],
        failed_step=row.get("failed_step"),
        error_message=row.get("error_message"),
        notes=row.get("notes"),
        updated_at=row["updated_at"],
        email_status=email_state.get("status"),
        email_status_detail=email_state.get("failure_reason"),
        email_recipient=(subject_data or {}).get("decision_maker_email"),
        email_message_id=email_state.get("email_message_id"),
        roof_source=(subject_data or {}).get("sede_operativa_source"),
        roof_confidence=(subject_data or {}).get("sede_operativa_confidence"),
    )


# ---------------------------------------------------------------------------
# Building Identification Cascade (BIC) — preview + user confirmation
# ---------------------------------------------------------------------------


class IdentifyBuildingRequest(BaseModel):
    """Inputs for the BIC preview run.

    Mirrors the subset of ``DemoTestPipelineRequest`` fields that the
    cascade actually uses, so the dialog can call this BEFORE submitting
    the whole pipeline. Returns the resolved building (or candidate
    list) without running scoring / creative / outreach — the dialog
    surfaces a "Conferma il capannone" picker when confidence is low.
    """

    vat_number: str = Field(min_length=5, max_length=30)
    legal_name: str = Field(min_length=1, max_length=255)
    hq_address: str = Field(min_length=4, max_length=300)
    ateco_code: str | None = Field(default=None, max_length=20)


class IdentifyBuildingCandidate(BaseModel):
    """One BIC candidate surfaced to the picker UI."""

    rank: int
    lat: float
    lng: float
    weight: float
    source: str
    polygon_geojson: dict | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Mapbox Static URL for the picker thumbnails — pre-computed
    # server-side so the browser doesn't have to know our access token.
    preview_url: str | None = None


class IdentifyBuildingResponse(BaseModel):
    """BIC preview result — winning match + ranked candidates."""

    confidence: str                  # high|medium|low|none|user_confirmed
    needs_user_confirmation: bool
    lat: float | None = None
    lng: float | None = None
    address: str | None = None
    source: str
    source_chain: list[dict[str, Any]] = Field(default_factory=list)
    candidates: list[IdentifyBuildingCandidate] = Field(default_factory=list)
    cached: bool = False


@router.post("/identify-building", response_model=IdentifyBuildingResponse)
async def demo_identify_building(
    ctx: CurrentUser, body: IdentifyBuildingRequest
) -> IdentifyBuildingResponse:
    """Run the BIC end-to-end and return the resolved building (no decrement).

    Used by the test-pipeline dialog as a pre-submit step: the operator
    types the address, this endpoint runs the cascade synchronously
    (~5-30s depending on which stages fire), and the response carries
    either a high-confidence winner or a ranked list of candidates so
    the user can click the right one. The chosen building is then
    passed back as ``confirmed_building_lat/lng`` on the
    ``/test-pipeline`` request, skipping the cascade on the second call.
    """
    tenant_id = require_tenant(ctx)
    await _require_demo_tenant(tenant_id)

    from ..services.building_identification import identify_building
    from ..services.italian_business_service import AtokaProfile
    from ..services.mapbox_service import (
        MapboxError,
        build_static_satellite_url,
        forward_geocode,
    )

    # Geocode the HQ address first so the cascade has hq_city / hq_province
    # to feed into Places multi-query and the OSM zone bbox computation.
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            geo = await forward_geocode(
                body.hq_address.strip(),
                client=client,
                min_relevance=0.5,
            )
    except MapboxError as exc:
        log.warning("demo.identify_building.geocode_error", err=str(exc))
        geo = None

    profile = AtokaProfile(
        vat_number=body.vat_number,
        legal_name=body.legal_name,
        ateco_code=body.ateco_code,
        hq_address=(geo.address if geo else body.hq_address) if geo else body.hq_address,
        hq_cap=geo.cap if geo else None,
        hq_city=geo.comune if geo else None,
        hq_province=geo.provincia if geo else None,
        hq_lat=geo.lat if geo else None,
        hq_lng=geo.lng if geo else None,
    )

    async with httpx.AsyncClient(timeout=30.0) as http_client:
        match = await identify_building(
            vat_number=body.vat_number,
            legal_name=body.legal_name,
            profile=profile,
            hq_address=geo.address if geo else body.hq_address,
            hq_city=geo.comune if geo else None,
            hq_province=geo.provincia if geo else None,
            ateco_code=body.ateco_code,
            http_client=http_client,
        )

    # Surface the source_chain entries (which already came back from
    # the voter) as picker candidates. We sort by weight desc + rank
    # them so the dialog can colour-code (rank 1 green, 2-3 amber,
    # 4-5 grey).
    sorted_chain = sorted(
        match.source_chain, key=lambda e: e.get("weight", 0.0), reverse=True
    )
    cands: list[IdentifyBuildingCandidate] = []
    for i, entry in enumerate(sorted_chain[:5]):
        try:
            preview_url = build_static_satellite_url(
                float(entry["lat"]),
                float(entry["lng"]),
                zoom=18,
                width=320,
                height=320,
            )
        except Exception:  # noqa: BLE001 — preview is optional
            preview_url = None
        cands.append(
            IdentifyBuildingCandidate(
                rank=i + 1,
                lat=float(entry["lat"]),
                lng=float(entry["lng"]),
                weight=float(entry.get("weight") or 0.0),
                source=str(entry.get("stage") or "unknown"),
                # Polygon may be in metadata; the voter doesn't currently
                # plumb it back to source_chain entries, so this stays
                # None for now (vision/OSM polygons are still on the
                # leader's metadata used for the winning match).
                polygon_geojson=None,
                metadata={
                    k: v for k, v in entry.items()
                    if k not in ("stage", "weight", "lat", "lng")
                },
                preview_url=preview_url,
            )
        )

    return IdentifyBuildingResponse(
        confidence=match.confidence,
        needs_user_confirmation=match.needs_user_confirmation,
        lat=match.lat,
        lng=match.lng,
        address=match.address,
        source=match.source,
        source_chain=match.source_chain,
        candidates=cands,
        cached=match.source in ("cache", "user_confirmed"),
    )


class ConfirmBuildingRequest(BaseModel):
    """User clicked a building on the picker map (or a freehand point)."""

    vat_number: str = Field(min_length=5, max_length=30)
    lat: float
    lng: float
    polygon_geojson: dict | None = None
    note: str | None = Field(default=None, max_length=400)


class ConfirmBuildingResponse(BaseModel):
    confidence: str
    cached: bool = True


@router.post("/confirm-building", response_model=ConfirmBuildingResponse)
async def demo_confirm_building(
    ctx: CurrentUser, body: ConfirmBuildingRequest
) -> ConfirmBuildingResponse:
    """Persist a user-clicked building as the authoritative pin for this VAT.

    Stage 7 of the BIC, but driven by a UI click rather than a vote.
    The cache write uses ``confidence='user_confirmed'`` which the
    automatic resolver will never overwrite — once the operator
    confirms, future runs short-circuit at Stage 0.
    """
    tenant_id = require_tenant(ctx)
    await _require_demo_tenant(tenant_id)

    from ..services.building_identification import (
        BuildingMatch,
        cache_building_match,
    )

    match = BuildingMatch(
        lat=body.lat,
        lng=body.lng,
        address=None,
        cap=None,
        city=None,
        province=None,
        polygon_geojson=body.polygon_geojson,
        confidence="user_confirmed",
        source="user_pick",
        source_chain=[
            {
                "stage": "user_pick",
                "weight": 1.0,
                "lat": body.lat,
                "lng": body.lng,
                "note": body.note,
            }
        ],
        needs_user_confirmation=False,
    )
    await cache_building_match(
        vat_number=body.vat_number,
        tenant_id=tenant_id,
        match=match,
        user_id=ctx.user_id,
    )
    return ConfirmBuildingResponse(confidence="user_confirmed", cached=True)


def _refund_attempt(tenant_id: str) -> None:
    """Increment the counter back up by 1. Used when the attempt could
    not be started (e.g. geocoding failed). Best-effort — a refund
    failure shouldn't compound the user's frustration with another error.
    """
    sb = get_service_client()
    try:
        cur = (
            sb.table("tenants")
            .select("demo_pipeline_test_remaining")
            .eq("id", tenant_id)
            .limit(1)
            .execute()
        )
        if not cur.data:
            return
        current: int = cur.data[0].get("demo_pipeline_test_remaining") or 0
        sb.table("tenants").update(
            {"demo_pipeline_test_remaining": current + 1}
        ).eq("id", tenant_id).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("demo.refund_attempt_failed", err=str(exc), tenant_id=tenant_id)
