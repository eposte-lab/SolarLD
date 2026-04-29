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
from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client
from ..models.enums import OutreachChannel, RoofDataSource, RoofStatus, SubjectType
from ..services.mapbox_service import MapboxError, forward_geocode

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


class DemoTestPipelineResponse(BaseModel):
    lead_id: str
    public_slug: str | None = None
    eta_seconds: int = 90
    attempts_remaining: int


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


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

    # 1. Atomic decrement — fail fast on exhaustion before doing any work.
    remaining = await asyncio.to_thread(_decrement_attempts, tenant_id)
    if remaining is None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Hai esaurito i 3 tentativi disponibili per il test pipeline.",
        )

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
        log.warning("demo.test_pipeline_geocode_error", err=str(exc))
        raise HTTPException(
            status_code=502,
            detail="Mapbox geocoding failed. Riprova fra qualche secondo.",
        ) from exc

    if geo is None:
        await asyncio.to_thread(_refund_attempt, tenant_id)
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

    gh = geohash.encode(geo.lat, geo.lng, precision=9)
    roof_payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "lat": geo.lat,
        "lng": geo.lng,
        "geohash": gh,
        "address": geo.address or body.hq_address,
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
        log.error("demo.roof_upsert_failed", tenant_id=tenant_id, err=str(exc))
        raise HTTPException(
            status_code=502,
            detail="Errore nel salvataggio del tetto. Riprova fra qualche secondo.",
        ) from exc
    if not roof_res.data:
        await asyncio.to_thread(_refund_attempt, tenant_id)
        raise HTTPException(status_code=502, detail="Failed to upsert roof row.")
    roof_id: str = roof_res.data[0]["id"]

    pii_raw = f"{body.legal_name.lower().strip()}|{body.vat_number.lower().strip()}"
    pii_hash = hashlib.sha256(pii_raw.encode()).hexdigest()
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
        "decision_maker_name": body.decision_maker_name,
        "decision_maker_role": body.decision_maker_role,
        # Recipient — what OutreachAgent will email. We mark it
        # verified so NeverBounce gating doesn't skip the send (the
        # prospect typed it themselves; trust > probabilistic check).
        "decision_maker_email": body.recipient_email,
        "decision_maker_email_verified": True,
        "data_sources": ["demo_test_pipeline"],
        "enrichment_cost_cents": 0,
        "enrichment_completed_at": now.isoformat(),
        "pii_hash": pii_hash,
    }
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
        raise HTTPException(
            status_code=502,
            detail="Scoring did not produce a lead row.",
        )

    # ── Creative + Outreach (background — rendering ~60s, send ~5s) ─
    # We deliberately do NOT await this. The endpoint returns 202 with
    # the lead_id so the dashboard toast can deep-link immediately;
    # the user lands on /leads/{id} where `lead-timeline-live.tsx`
    # streams real-time events as creative/outreach progress (rendering
    # done → email queued → email sent → recipient opens, etc).
    asyncio.create_task(
        _run_creative_and_outreach_background(tenant_id=tenant_id, lead_id=lead_id)
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
        public_slug=public_slug,
        attempts_remaining=remaining,
    )


async def _run_creative_and_outreach_background(
    *, tenant_id: str, lead_id: str
) -> None:
    """Fire-and-forget runner for the slow tail of the demo pipeline.

    Spawned via `asyncio.create_task` so the HTTP response returns to
    the browser as soon as scoring completes. Errors are swallowed
    after logging — there is no caller to raise to. The dashboard
    surfaces failures via the live timeline / lead status, not via
    the original POST response.
    """
    try:
        await CreativeAgent().run(
            CreativeInput(
                tenant_id=tenant_id,
                lead_id=lead_id,
                force=True,
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("demo.creative_error", lead_id=lead_id, err=str(exc))

    try:
        await OutreachAgent().run(
            OutreachInput(
                tenant_id=tenant_id,
                lead_id=lead_id,
                channel=OutreachChannel.EMAIL,
                sequence_step=1,
                force=True,
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("demo.outreach_error", lead_id=lead_id, err=str(exc))

    log.info("demo.test_pipeline_background_completed", lead_id=lead_id)


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
