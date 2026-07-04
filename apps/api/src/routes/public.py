"""Public endpoints for the lead portal — no auth.

These serve the lead-facing slug pages (/lead/:slug) and handle
opt-outs, engagement tracking, and appointment requests.

All endpoints are **idempotent** — they are the public ingress for
bots and email clients that prefetch links, so double hits from
Gmail's image proxy, antivirus scanners, or human refresh must never
produce user-visible duplicate effects.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import (
    APIRouter,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field

from ..core.logging import get_logger
from ..core.queue import enqueue
from ..core.redis import get_redis
from ..core.supabase_client import get_service_client
from ..models.enums import BlacklistReason, LeadStatus
from ..services.appointment_service import (
    fire_appointment_webhook,
    is_tenant_moderated,
    notify_tenant_contact_request,
)
from ..services.bolletta_ocr_service import (
    OCR_PROVIDER_TAG,
    OcrResult,
    extract_from_image,
)
from ..services.engagement_service import (
    recompute_lead_engagement as _recompute_lead_engagement,
)
from ..services.savings_compare_service import compute_savings_compare

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
            # decision_maker_phone is surfaced so the dossier's contact form can
            # PRE-FILL the phone we already scraped (the prospect's own, usually
            # public, business number) — they just confirm or correct it. Only
            # the phone is exposed; the email stays server-side.
            "tenant_id, subjects(type, business_name, owner_first_name, decision_maker_phone), "
            "roofs(address, cap, comune, provincia, area_sqm, "
            "estimated_kwp, estimated_yearly_kwh, derivations)"
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
            detail="lead_unsubscribed",
        )

    # Dossier link auto-expiry — same TTL as the frontend fallback.
    # See `_dossier_is_expired`; the 410 detail discriminator lets the
    # portal show the "scaduto" page rather than the opt-out page.
    if _dossier_is_expired(lead):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="dossier_expired",
        )

    # Fetch tenant branding + About narrative (Sprint 8 Fase A.2/A.3).
    # The portal AboutSection consumes about_md/year_founded/team_size/
    # certifications/hero_image/tagline; legal_* feeds the GDPR footer.
    # epc_enabled / privacy_policy_url drive the EPC commercial section
    # and the GDPR consent link on the appointment form (Sprint client-feedback).
    tenant = (
        sb.table("tenants")
        .select(
            "business_name, brand_logo_url, brand_primary_color, brand_color_accent, "
            "settings, "
            "whatsapp_number, contact_email, "
            "legal_name, vat_number, legal_address, "
            "about_md, about_year_founded, about_team_size, "
            "about_certifications, about_hero_image_url, about_tagline, "
            "epc_enabled, privacy_policy_url, website_url, installations_count"
        )
        .eq("id", lead["tenant_id"])
        .limit(1)
        .execute()
    )
    t = tenant.data[0] if tenant.data else None
    if t is not None:
        # The DOSSIER accent is decoupled from ``brand_color_accent`` (which
        # the OUTREACH EMAIL uses): the operator wanted the dossier CTAs in a
        # vivid colour without recolouring the emails. Surface only the one
        # setting and drop the rest of ``settings`` so admin config never
        # leaks to the public dossier.
        _settings = t.pop("settings", None)
        t["dossier_accent"] = (
            _settings.get("dossier_accent") if isinstance(_settings, dict) else None
        )
    lead["tenant"] = t

    # "Lavori realizzati" — case study attivi del tenant per la sezione
    # portfolio del dossier. Best-effort: un errore qui non deve far
    # fallire il dossier.
    try:
        cs = (
            sb.table("tenant_case_studies")
            .select("id, client_name, story, image_url, logo_url, kwp, location, year")
            .eq("tenant_id", lead["tenant_id"])
            .eq("active", True)
            .order("display_order")
            .limit(12)
            .execute()
        )
        lead["case_studies"] = cs.data or []
    except Exception:  # noqa: BLE001
        lead["case_studies"] = []

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

    Open/click are NO LONGER synthesized here. Resend open+click tracking
    is now enabled at the domain level, so the genuine ``email.opened`` /
    ``email.clicked`` webhooks are the single source of truth (recorded by
    the tracking agent → ``outreach_opened_at`` / ``outreach_clicked_at``).
    A portal visit records only the visit itself (``dashboard_visited_at``
    + ``lead.portal_visited``), so "opened the email" and "visited the
    dossier" stay DISTINCT signals — the whole point of re-enabling real
    open tracking.
    """
    sb = get_service_client()
    lead = _load_lead_by_slug(sb, slug)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    first_visit = not lead.get("dashboard_visited_at")

    update: dict[str, Any] = {}
    if first_visit:
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
        if first_visit:
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
        wa_update: dict[str, object] = {
            "whatsapp_initiated_at": "now()",
            "pipeline_status": LeadStatus.WHATSAPP.value,
        }
        if not lead.get("source"):
            wa_update["source"] = "whatsapp_reply"
        sb.table("leads").update(wa_update).eq("id", lead["id"]).execute()
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


# ---------------------------------------------------------------------------
# Dossier survey widget — progressive "one question at a time" lead-capture
# ---------------------------------------------------------------------------

# Public survey submissions are unauthenticated (slug-gated). Cap per slug to
# bound abuse — a genuine prospect completes the quiz once.
_SURVEY_RATE_PER_HOUR = 5
_SURVEY_RATE_KEY_TTL = 60 * 60 + 60  # 1h + 1m grace


async def _survey_rate_allows(slug: str) -> bool:
    """Cap public survey submissions per slug/hour. Fail-open (slug unguessable)."""
    try:
        r = get_redis()
        key = f"survey:submit:{slug}:{datetime.now(UTC):%Y%m%d%H}"
        pipe = r.pipeline()
        pipe.incr(key, 1)
        pipe.expire(key, _SURVEY_RATE_KEY_TTL)
        results = await pipe.execute()
        return int(results[0]) <= _SURVEY_RATE_PER_HOUR
    except Exception as exc:  # noqa: BLE001
        log.warning("survey.rate_check_failed", err=str(exc))
        return True


class SurveySubmission(BaseModel):
    """Completed dossier survey — the engaging quiz that ends by asking for the
    prospect's phone. ``answers`` maps each question id to the chosen answer."""

    answers: dict[str, str] = Field(default_factory=dict)
    phone: str | None = Field(default=None, max_length=40)


@router.post("/lead/{slug}/survey/step")
async def track_survey_step(slug: str, step: int = 0, total: int = 0) -> dict[str, str]:
    """Fire-and-forget per-step tracking for the dossier survey widget.

    Records how far a prospect gets through the quiz (survey_started → each
    step → completed) so a drop-off point is visible in the lead timeline.
    """
    sb = get_service_client()
    lead = _load_lead_by_slug(sb, slug)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    _emit_public_event(
        sb,
        event_type="lead.survey_step",
        tenant_id=lead["tenant_id"],
        lead_id=lead["id"],
        payload={"slug": slug, "step": step, "total": total},
    )
    return {"ok": "tracked"}


@router.post("/lead/{slug}/survey")
async def submit_survey(slug: str, body: SurveySubmission) -> dict[str, str]:
    """Capture a completed dossier survey (progressive quiz) + the hot phone.

    The survey replaces a flat contact form: a few curious questions the
    prospect happily answers, ending by asking their phone. A self-provided
    number is the HOTTEST contact we can get, so it's written to
    ``subjects.decision_maker_phone`` (source='survey') — it beats any
    scraped/Atoka number. Emits ``survey_completed`` + ``phone_captured`` and
    bumps the lead to ``engaged`` (a completed survey is deep engagement).
    Rate-limited per slug (public, unauthenticated endpoint).
    """
    if not await _survey_rate_allows(slug):
        raise HTTPException(status_code=429, detail="Too many submissions")
    sb = get_service_client()
    lead = _load_lead_by_slug(sb, slug)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    phone = (body.phone or "").strip()
    if phone:
        # subject_id isn't on the slim slug-lookup — fetch it to write the
        # prospect-provided phone (the hottest contact) onto the anagrafica.
        sres = sb.table("leads").select("subject_id").eq("id", lead["id"]).limit(1).execute()
        subject_id = (sres.data or [{}])[0].get("subject_id")
        if subject_id:
            sb.table("subjects").update(
                {"decision_maker_phone": phone, "decision_maker_phone_source": "survey"}
            ).eq("id", subject_id).execute()

    # A completed survey is stronger engagement than a plain visit.
    if lead.get("pipeline_status") in {
        LeadStatus.SENT.value,
        LeadStatus.DELIVERED.value,
        LeadStatus.OPENED.value,
        LeadStatus.CLICKED.value,
    }:
        sb.table("leads").update({"pipeline_status": LeadStatus.ENGAGED.value}).eq(
            "id", lead["id"]
        ).execute()

    _emit_public_event(
        sb,
        event_type="lead.survey_completed",
        tenant_id=lead["tenant_id"],
        lead_id=lead["id"],
        payload={"slug": slug, "answers": body.answers, "has_phone": bool(phone)},
    )
    if phone:
        _emit_public_event(
            sb,
            event_type="lead.phone_captured",
            tenant_id=lead["tenant_id"],
            lead_id=lead["id"],
            payload={"slug": slug, "source": "survey"},
        )
    return {"ok": "captured"}


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


# Public proposal-resend guard: the appointment form is unauthenticated, so a
# burst cap per slug bounds any abuse of the "leave your email → get the
# proposal" path. Per-slug is the right axis (each lead = one cold target, the
# slug URL is itself unguessable); 3/hour covers the honest case (resend to a
# second address, a retry) while making the endpoint useless as a spam relay.
_PROPOSAL_RESEND_RATE_PER_HOUR = 3
_PROPOSAL_RESEND_RATE_KEY_TTL = 60 * 60 + 60  # 1h + 1m grace


async def _proposal_resend_rate_allows(slug: str) -> bool:
    """Cap public-triggered proposal resends at 3/hour per slug. Fail-open."""
    try:
        r = get_redis()
        key = f"proposal:resend:{slug}:{datetime.now(UTC):%Y%m%d%H}"
        pipe = r.pipeline()
        pipe.incr(key, 1)
        pipe.expire(key, _PROPOSAL_RESEND_RATE_KEY_TTL)
        results = await pipe.execute()
        return int(results[0]) <= _PROPOSAL_RESEND_RATE_PER_HOUR
    except Exception as exc:  # noqa: BLE001
        # Fail open — the slug is unguessable, so the blast radius of a redis
        # hiccup is one prospect's own dossier link, not the open internet.
        log.warning("proposal_resend.rate_check_failed", err=str(exc))
        return True


async def _resend_proposal_to_email(*, tenant_id: str, lead_id: str, slug: str, email: str) -> bool:
    """Re-send the EXACT outreach proposal to a prospect-provided email.

    Mirrors the operator's authenticated ``/leads/{id}/resend-to-address``
    (``recipient_override`` + ``force``): same template, plant data and
    rendering as the daily outreach, just routed to the address the visitor
    typed in the popup. ``force`` bypasses the send-window (deliberate
    prospect action), but the blacklist / existing-PV / GDPR gates inside
    ``OutreachAgent`` still apply — a suppressed lead never sends. Rate-limited
    per slug. Returns ``True`` if a job was enqueued.
    """
    if not await _proposal_resend_rate_allows(slug):
        log.info("appointment.proposal_resend_rate_limited", tenant_id=tenant_id, lead_id=lead_id)
        return False
    import hashlib

    override_tag = hashlib.sha256(email.encode()).hexdigest()[:10]
    ts = int(datetime.now(tz=UTC).timestamp())
    await enqueue(
        "outreach_task",
        {
            "tenant_id": tenant_id,
            "lead_id": lead_id,
            "channel": "email",
            "force": True,
            "recipient_override": email,
        },
        job_id=f"outreach_appt_resend:{tenant_id}:{lead_id}:{override_tag}:{ts}",
    )
    log.info(
        "appointment.proposal_resent",
        tenant_id=tenant_id,
        lead_id=lead_id,
        override_domain=email.split("@", 1)[1] if "@" in email else "?",
    )
    return True


@router.post("/lead/{slug}/appointment", status_code=status.HTTP_202_ACCEPTED)
async def request_appointment(slug: str, payload: AppointmentRequest) -> dict[str, object]:
    """Lead submits the in-portal appointment form.

    **Normal tenant:** record an ``events`` row + advance the lead to
    ``pipeline_status='appointment'`` (the dashboard picks it up via
    Supabase Realtime), email the tenant's ``contact_email`` and fire
    the CRM webhook if configured.

    **Moderated trial tenant:** touch NONE of the tenant-visible surface.
    The request is simply held in ``pending_inbound_requests`` and shows up
    in the operator's super-admin queue (``/admin/trial`` → "Coda inbound").
    No operator email is sent — the operator reviews the list there. The
    held side-effects (status bump, event, tenant email, webhook) are
    replayed by the super-admin approval endpoint in ``routes/admin.py``.

    Fail-open throughout: the 202 is returned even if a side-effect raises.
    """
    sb = get_service_client()
    lead = _load_lead_by_slug(sb, slug)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    # FAIL-CLOSED inbound truncation — a lead we EXCLUDED (blacklisted, or whose
    # roof already has PV) is out of target: a form submission from it must NOT
    # advance the lead, notify the operator, hold an inbound request, or fire the
    # webhook. Accept the 202 silently (no error shown to the prospect) but do
    # nothing downstream.
    from ..services.pv_verification_service import lead_roof_sendable

    _send_ok, _send_reason = lead_roof_sendable(sb, lead["id"])
    if (
        lead.get("pipeline_status") == LeadStatus.BLACKLISTED.value
        or _send_reason == "has_existing_pv"
    ):
        log.info(
            "appointment.truncated_out_of_target",
            slug=slug,
            reason=_send_reason or "blacklisted",
        )
        return {"ok": True, "status": "received"}

    tenant_id = lead["tenant_id"]
    payload_dict = payload.model_dump()

    # Link al dossier (riusato da webhook + email).
    from ..core.config import settings as _settings

    _portal_origin = (_settings.next_public_lead_portal_url or "").rstrip("/")
    # This dossier link only ever reaches the TENANT (operator-notification email
    # + CRM webhook), never the prospect. ?preview=1 makes the portal skip all
    # trackers, so the team reviewing the lead doesn't pollute its engagement data.
    dossier_url = f"{_portal_origin}/dossier/{slug}?preview=1" if _portal_origin else None

    # Tenant config for downstream notifications (CRM webhook + email).
    tenant_row = (
        sb.table("tenants")
        .select("appointment_webhook_url, contact_email, business_name")
        .eq("id", tenant_id)
        .limit(1)
        .maybe_single()
        .execute()
    )
    tenant_data = (tenant_row.data or {}) if tenant_row else {}

    # Only un-promoted contatti on a moderated tenant are still held for
    # operator review. Once a lead is released (operator_released_at set —
    # which now happens automatically for auto-release tenants), its
    # appointment follows the normal direct flow: email + event + status +
    # webhook, visible to the tenant with no approval step.
    moderated = is_tenant_moderated(sb, tenant_id) and not lead.get("operator_released_at")

    # ── Moderated trial tenant: HOLD, surface nothing to the tenant ──
    if moderated:
        # Just enqueue: the operator reviews the request in the super-admin
        # "Coda inbound" queue and approves/rejects from there. No operator
        # email — the queue UI is the single review surface.
        try:
            sb.table("pending_inbound_requests").insert(
                {
                    "tenant_id": tenant_id,
                    "lead_id": lead["id"],
                    "payload": payload_dict,
                    "dossier_url": dossier_url,
                    "status": "pending",
                }
            ).execute()
        except Exception as exc:  # noqa: BLE001 — never block the 202
            log.warning("appointment.queue_insert_failed", tenant_id=tenant_id, err=str(exc)[:200])
        # Stamp the authoritative request timestamp. This is invisible to
        # the tenant — a frozen (un-promoted) contatto has its
        # engagement_score masked by the moderation freeze — but it means
        # the score is already correct (floored to "caldo") the moment the
        # operator approves/promotes, when the approval path recomputes.
        try:
            sb.table("leads").update(
                {"appointment_requested_at": datetime.now(UTC).isoformat()}
            ).eq("id", lead["id"]).execute()
        except Exception as exc:  # noqa: BLE001 — never block the 202
            log.warning("appointment.stamp_failed", tenant_id=tenant_id, err=str(exc)[:200])
        # Operator opted in (AskUserQuestion): a contact request is urgent,
        # so notify the tenant's contact_email IMMEDIATELY even while the
        # request also sits in the inbound queue for review. Still NO status
        # change / event / webhook here — those stay gated on approval so the
        # moderation freeze on the lead surface is preserved.
        await notify_tenant_contact_request(
            sb,
            tenant_id=tenant_id,
            tenant_data=tenant_data,
            payload=payload_dict,
            dossier_url=dossier_url,
        )
        return {"ok": True, "status": "held"}

    # ── Normal tenant: original behavior ─────────────────────────────
    # Promote candidate → active lead. source='cta_click' marks the first
    # real engagement signal (dashboard "Lead Attivi" counter), and we
    # advance pipeline_status to 'appointment'.
    update_fields: dict[str, object] = {
        "pipeline_status": LeadStatus.APPOINTMENT.value,
        # Authoritative "ha richiesto contatto" timestamp — drives the
        # engagement hot-floor (engagement_service) regardless of whether
        # the portal.appointment_click beacon landed.
        "appointment_requested_at": datetime.now(UTC).isoformat(),
    }
    if not lead.get("source"):
        # Only stamp source once — do not overwrite a subsequent whatsapp_reply.
        update_fields["source"] = "cta_click"

    sb.table("leads").update(update_fields).eq("id", lead["id"]).execute()

    # Recompute the engagement score now so the lead jumps to "caldo"
    # immediately (floored via appointment_requested_at) — the dashboard
    # picks it up via Supabase Realtime without waiting for the nightly
    # rollup. Fail-open: the rollup reconciles if this raises.
    try:
        await _recompute_lead_engagement(lead["id"])
    except Exception as exc:  # noqa: BLE001
        log.warning("appointment.recompute_failed", lead_id=lead["id"], err=str(exc)[:200])

    # Se il prospect ha lasciato un'email, il sistema le re-invia SUBITO la
    # stessa identica proposta (stesso meccanismo del resend-to-address
    # dell'operatore). Mai per i lead soppressi (la guardia + i gate
    # dell'OutreachAgent lo garantiscono). Fail-open: un errore qui non blocca
    # il 202.
    resend_email = (payload.email or "").strip().lower() or None
    proposal_resent_to: str | None = None
    if resend_email and lead.get("pipeline_status") != LeadStatus.BLACKLISTED.value:
        try:
            if await _resend_proposal_to_email(
                tenant_id=tenant_id,
                lead_id=lead["id"],
                slug=slug,
                email=resend_email,
            ):
                proposal_resent_to = resend_email
        except Exception as exc:  # noqa: BLE001 — never block the 202
            log.warning(
                "appointment.proposal_resend_failed", lead_id=lead["id"], err=str(exc)[:200]
            )

    event_payload = {
        "slug": slug,
        "contact_name": payload.contact_name,
        "phone": payload.phone,
        "email": payload.email,
        "preferred_time": payload.preferred_time,
        "notes": payload.notes,
        "proposal_resent_to_email": proposal_resent_to,
    }
    _emit_public_event(
        sb,
        event_type="lead.appointment_requested",
        tenant_id=tenant_id,
        lead_id=lead["id"],
        payload=event_payload,
    )

    # Evento dedicato per la cronologia/activity-log: il re-invio della
    # proposta a un indirizzo lasciato dal prospect è un fatto a sé,
    # tracciato distintamente dalla richiesta di contatto.
    if proposal_resent_to:
        _emit_public_event(
            sb,
            event_type="lead.proposal_resent_to_email",
            tenant_id=tenant_id,
            lead_id=lead["id"],
            payload={"slug": slug, "email": proposal_resent_to, "trigger": "appointment_form"},
        )

    # Notifica email al tenant (contact_email, dall'inbox verificato,
    # reply-to = email del prospect) + webhook CRM. Entrambi fail-open.
    await notify_tenant_contact_request(
        sb,
        tenant_id=tenant_id,
        tenant_data=tenant_data,
        payload=payload_dict,
        dossier_url=dossier_url,
        proposal_resent_to=proposal_resent_to,
    )

    webhook_url: str | None = tenant_data.get("appointment_webhook_url")
    if webhook_url:
        await fire_appointment_webhook(
            webhook_url,
            lead_id=lead["id"],
            payload=payload_dict,
            dossier_url=dossier_url,
        )

    return {"ok": True, "status": LeadStatus.APPOINTMENT.value}


# ---------------------------------------------------------------------------
# Bolletta upload (Sprint 8 Fase B.2)
# ---------------------------------------------------------------------------
#
# Lead uploads a utility bill from the portal. We:
#
#   1. Validate slug → (tenant_id, lead_id) and bounce blacklisted leads.
#   2. Rate-limit per (slug, IP-bucket-hash) — max 3 uploads / hour.
#   3. Validate MIME + byte length (whitelist + 5 MB hard cap).
#   4. Stream the file to ``bollette/{tenant_id}/{lead_id}/{uuid}.{ext}``
#      via the service-role Storage client.
#   5. Run Claude Vision OCR (sync — typical latency 3-8s; if it
#      times out, persist the row with ocr_error and let the user
#      enter values manually from the BillUploadCard UI).
#   6. Insert a ``bolletta_uploads`` row + emit
#      ``portal.bolletta_uploaded`` portal event so Fase C.1 bumps
#      engagement_score by +50.
#   7. Stamp ``leads.bolletta_uploaded_at = now()`` for the dashboard
#      "ha caricato bolletta" filter.
#
# We return the OCR readout so the BillUploadCard can offer an inline
# edit form when ``manual_required=True`` (low confidence).

_BOLLETTA_ALLOWED_MIME: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "application/pdf",
    }
)
_BOLLETTA_MAX_BYTES = 10 * 1024 * 1024  # 10 MB (matches the storage bucket cap)
_BOLLETTA_RATE_PER_HOUR = 3
_BOLLETTA_RATE_KEY_TTL = 60 * 60 + 60  # 1h + 1m grace


def _bolletta_ext_from_mime(mime: str) -> str:
    return {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "application/pdf": "pdf",
    }.get(mime, "bin")


async def _bolletta_rate_allows(slug: str) -> bool:
    """Cap upload bursts at 3/hour per slug.

    Per-slug is the right axis: each lead is one cold target, three
    bills/hour covers any honest scenario (upload, retry on failure,
    upload a second bill for the husband's apartment), and any abuse
    pattern is rate-limited by the slug's URL itself being unguessable.
    """
    try:
        r = get_redis()
        key = f"bolletta:upload:{slug}:{datetime.now(UTC):%Y%m%d%H}"
        pipe = r.pipeline()
        pipe.incr(key, 1)
        pipe.expire(key, _BOLLETTA_RATE_KEY_TTL)
        results = await pipe.execute()
        used = int(results[0])
        return used <= _BOLLETTA_RATE_PER_HOUR
    except Exception as exc:  # noqa: BLE001
        # Fail open — better to accept a possible duplicate than to
        # tell a paying customer their upload was refused.
        log.warning("bolletta.rate_check_failed", err=str(exc))
        return True


def _bolletta_response_from_row(
    *,
    upload_id: str,
    ocr: OcrResult | None,
    source: str,
    manual_kwh: float | None = None,
    manual_eur: float | None = None,
) -> dict[str, Any]:
    """Shape the upload response consumed by BillUploadCard."""
    if ocr and ocr.success:
        return {
            "upload_id": upload_id,
            "source": source,
            "status": "manual_required" if ocr.manual_required else "ok",
            "ocr_kwh_yearly": ocr.kwh_yearly,
            "ocr_eur_yearly": ocr.eur_yearly,
            "ocr_confidence": ocr.confidence,
            "ocr_provider_name": ocr.provider_name,
        }
    return {
        "upload_id": upload_id,
        "source": source,
        "status": "manual_required",
        "ocr_kwh_yearly": None,
        "ocr_eur_yearly": None,
        "ocr_confidence": None,
        "ocr_error": ocr.error if ocr else None,
        "manual_kwh_yearly": manual_kwh,
        "manual_eur_yearly": manual_eur,
    }


@router.post(
    "/lead/{slug}/bolletta",
    status_code=status.HTTP_200_OK,
)
async def upload_bolletta(
    slug: str,
    file: UploadFile = File(..., description="Utility bill (image or PDF)"),
) -> dict[str, Any]:
    """Receive a bolletta upload, run OCR, persist + return readout.

    Soft-fails OCR: if the model errors out or returns low confidence,
    the upload still lands in storage and the row in
    ``bolletta_uploads`` records the error so the UI can show a manual
    entry form. Only schema-level rejections (oversized, unsupported
    MIME, unknown slug, blacklisted lead) raise 4xx.
    """
    # ---- 1. Slug → lead resolution
    sb = get_service_client()
    lead = _load_lead_by_slug(sb, slug)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    if lead.get("pipeline_status") == LeadStatus.BLACKLISTED.value:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Lead unsubscribed",
        )

    # ---- 2. Rate-limit (soft — returns 429 only when exceeded)
    if not await _bolletta_rate_allows(slug):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many uploads — riprova fra un'ora",
        )

    # ---- 3. MIME + size validation
    mime = (file.content_type or "").lower()
    if mime not in _BOLLETTA_ALLOWED_MIME:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Tipo file non supportato: {mime or 'unknown'}",
        )
    body = await file.read()
    if not body:
        raise HTTPException(status_code=400, detail="File vuoto")
    if len(body) > _BOLLETTA_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File troppo grande ({len(body)} bytes, max {_BOLLETTA_MAX_BYTES})",
        )

    # ---- 4. Storage upload
    upload_id = str(uuid.uuid4())
    ext = _bolletta_ext_from_mime(mime)
    storage_path = f"{lead['tenant_id']}/{lead['id']}/{upload_id}.{ext}"
    try:
        sb.storage.from_("bollette").upload(
            storage_path,
            body,
            {"content-type": mime, "upsert": "false"},
        )
    except Exception as exc:  # noqa: BLE001
        log.error("bolletta.storage_upload_failed", slug=slug, err=str(exc))
        raise HTTPException(
            status_code=502,
            detail="Upload non riuscito — riprova",
        ) from exc

    # ---- 5. OCR (sync, soft-fail). PDFs are now natively supported —
    #         `extract_from_image` rasterises page 1 via pdfium before
    #         calling Claude Vision (Sprint 0108). Any failure (corrupt
    #         PDF, image too small, low confidence) falls back to the
    #         manual-entry form on the lead portal.
    ocr: OcrResult | None = None
    try:
        ocr = await extract_from_image(body, mime_type=mime)
    except Exception as exc:  # noqa: BLE001
        log.warning("bolletta.ocr_failed", slug=slug, err=str(exc))
        ocr = OcrResult(
            success=False,
            error=f"ocr_exception:{type(exc).__name__}",
        )

    # ---- 6. Insert row
    source = "upload_ocr" if (ocr and ocr.success and not ocr.manual_required) else "upload_manual"
    row: dict[str, Any] = {
        "id": upload_id,
        "tenant_id": lead["tenant_id"],
        "lead_id": lead["id"],
        "storage_path": storage_path,
        "mime_type": mime,
        "file_size_bytes": len(body),
        "ocr_provider": OCR_PROVIDER_TAG if ocr and ocr.success else None,
        "ocr_kwh_yearly": ocr.kwh_yearly if ocr and ocr.success else None,
        "ocr_eur_yearly": ocr.eur_yearly if ocr and ocr.success else None,
        "ocr_confidence": ocr.confidence if ocr and ocr.success else None,
        "ocr_raw_response": ocr.raw_response if ocr else None,
        "ocr_error": ocr.error if ocr and not ocr.success else None,
        "source": source,
    }
    try:
        sb.table("bolletta_uploads").insert(row).execute()
    except Exception as exc:  # noqa: BLE001
        log.error("bolletta.row_insert_failed", slug=slug, err=str(exc))
        raise HTTPException(status_code=500, detail="Salvataggio non riuscito") from exc

    # ---- 7. Stamp lead + emit events
    try:
        sb.table("leads").update({"bolletta_uploaded_at": datetime.now(UTC).isoformat()}).eq(
            "id", lead["id"]
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("bolletta.lead_stamp_failed", err=str(exc))

    # Portal event for the engagement timeline. Fired HERE — right after
    # the upload is stamped — not after the ROI recompute below: a slow
    # recompute or a request cut short must not cost us the event. The
    # engagement score reads the bolletta from leads.bolletta_uploaded_at
    # regardless, but the dashboard timeline still needs this row.
    try:
        sb.table("portal_events").insert(
            {
                "tenant_id": lead["tenant_id"],
                "lead_id": lead["id"],
                "session_id": f"server:{upload_id}",
                "event_kind": "portal.bolletta_uploaded",
                "metadata": {
                    "upload_id": upload_id,
                    "ocr_confidence": (ocr.confidence if ocr and ocr.success else None),
                },
                "elapsed_ms": 0,
                "occurred_at": datetime.now(UTC).isoformat(),
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("bolletta.portal_event_failed", err=str(exc))

    # ---- 7b. Recompute roof.derivations + leads.roi_data using the
    # customer's ACTUAL annual consumption from OCR (Sprint 1.2).
    # Without this, the email body / lead portal / preventivo PDF
    # all keep showing the median-Italian estimate (60% self-
    # consumption × estimated-yearly-production) — the bolletta
    # upload would be tracked but the numbers never updated.
    if ocr and ocr.success and ocr.kwh_yearly:
        try:
            await _recompute_roi_after_bolletta(
                sb,
                lead_id=lead["id"],
                tenant_id=lead["tenant_id"],
                consumption_kwh_yearly=ocr.kwh_yearly,
                consumption_eur_yearly=ocr.eur_yearly,
                bolletta_upload_id=upload_id,
            )
        except Exception as exc:  # noqa: BLE001 — never fail the upload
            log.warning(
                "bolletta.roi_recompute_failed",
                lead_id=lead["id"],
                err_type=type(exc).__name__,
                err=str(exc)[:200],
            )

    _emit_public_event(
        sb,
        event_type="lead.bolletta_uploaded",
        tenant_id=lead["tenant_id"],
        lead_id=lead["id"],
        payload={
            "slug": slug,
            "upload_id": upload_id,
            "ocr_confidence": ocr.confidence if ocr and ocr.success else None,
            "kwh": ocr.kwh_yearly if ocr and ocr.success else None,
            "eur": ocr.eur_yearly if ocr and ocr.success else None,
        },
    )

    # Recompute the engagement score now — the bolletta is a strong
    # intent signal the "Caldi adesso" filter must see immediately. It
    # reads leads.bolletta_uploaded_at, so the +35 lands even if the
    # portal_events insert above failed.
    try:
        await _recompute_lead_engagement(lead["id"])
    except Exception as exc:  # noqa: BLE001
        log.warning("bolletta.engagement_recompute_failed", err=str(exc))

    return _bolletta_response_from_row(
        upload_id=upload_id,
        ocr=ocr,
        source=source,
    )


class ManualBollettaBody(BaseModel):
    """Manual values when the user couldn't / didn't upload a bill.

    Two callers:
      * the BillUploadCard "I'll type it instead" branch (no file)
      * the inline-edit form when OCR confidence is low and the user
        corrects the values (passes ``upload_id`` of the prior row)
    """

    # Soglie industriali (vedi bolletta_ocr_service): i clienti target
    # sono capannoni/industria con consumi fino a ~GWh/anno.
    kwh_yearly: float = Field(..., gt=0, le=10_000_000)
    eur_yearly: float = Field(..., gt=0, le=2_000_000)
    upload_id: str | None = Field(
        default=None,
        description="If patching an existing OCR row, the upload_id "
        "from the prior /bolletta call. Omit to create a manual_only "
        "row from scratch.",
    )


@router.post(
    "/lead/{slug}/bolletta/manual",
    status_code=status.HTTP_200_OK,
)
async def upload_bolletta_manual(slug: str, body: ManualBollettaBody) -> dict[str, Any]:
    """Record manual kWh/€ values, with or without a prior OCR row."""
    sb = get_service_client()
    lead = _load_lead_by_slug(sb, slug)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    if lead.get("pipeline_status") == LeadStatus.BLACKLISTED.value:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Lead unsubscribed",
        )

    if body.upload_id:
        # PATCH path: fold manual values onto the existing row.
        try:
            sb.table("bolletta_uploads").update(
                {
                    "manual_kwh_yearly": body.kwh_yearly,
                    "manual_eur_yearly": body.eur_yearly,
                    "source": "upload_manual",
                }
            ).eq("id", body.upload_id).eq("lead_id", lead["id"]).execute()
        except Exception as exc:  # noqa: BLE001
            log.error("bolletta.manual_patch_failed", err=str(exc))
            raise HTTPException(status_code=500, detail="Salvataggio non riuscito") from exc
        upload_id = body.upload_id
        source = "upload_manual"
    else:
        # INSERT path: create a manual-only row (no file, no OCR).
        upload_id = str(uuid.uuid4())
        try:
            sb.table("bolletta_uploads").insert(
                {
                    "id": upload_id,
                    "tenant_id": lead["tenant_id"],
                    "lead_id": lead["id"],
                    # No storage object — but the column is NOT NULL.
                    # Use a sentinel that won't collide with a real path.
                    "storage_path": f"manual_only/{upload_id}",
                    "mime_type": "manual/none",
                    "file_size_bytes": 0,
                    "manual_kwh_yearly": body.kwh_yearly,
                    "manual_eur_yearly": body.eur_yearly,
                    "source": "manual_only",
                }
            ).execute()
        except Exception as exc:  # noqa: BLE001
            log.error("bolletta.manual_insert_failed", err=str(exc))
            raise HTTPException(status_code=500, detail="Salvataggio non riuscito") from exc
        source = "manual_only"

    try:
        sb.table("leads").update({"bolletta_uploaded_at": datetime.now(UTC).isoformat()}).eq(
            "id", lead["id"]
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("bolletta.lead_stamp_failed", err=str(exc))

    # Portal event for the engagement timeline — see the OCR upload
    # route above. A manual consumption entry is the same intent signal.
    try:
        sb.table("portal_events").insert(
            {
                "tenant_id": lead["tenant_id"],
                "lead_id": lead["id"],
                "session_id": f"server:{upload_id}",
                "event_kind": "portal.bolletta_uploaded",
                "metadata": {"upload_id": upload_id, "source": source},
                "elapsed_ms": 0,
                "occurred_at": datetime.now(UTC).isoformat(),
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("bolletta.portal_event_failed", err=str(exc))

    # Sprint 1.2 — same recompute path as the OCR upload route, this
    # time fed by the manual values.
    if body.kwh_yearly:
        try:
            await _recompute_roi_after_bolletta(
                sb,
                lead_id=lead["id"],
                tenant_id=lead["tenant_id"],
                consumption_kwh_yearly=body.kwh_yearly,
                consumption_eur_yearly=body.eur_yearly,
                bolletta_upload_id=upload_id,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "bolletta.roi_recompute_failed",
                lead_id=lead["id"],
                err_type=type(exc).__name__,
                err=str(exc)[:200],
            )

    _emit_public_event(
        sb,
        event_type="lead.bolletta_uploaded",
        tenant_id=lead["tenant_id"],
        lead_id=lead["id"],
        payload={
            "slug": slug,
            "upload_id": upload_id,
            "kwh": body.kwh_yearly,
            "eur": body.eur_yearly,
            "source": source,
        },
    )

    # Real-time engagement recompute — the manual route never did this,
    # so a manual entry only bumped the score at the nightly rollup.
    try:
        await _recompute_lead_engagement(lead["id"])
    except Exception as exc:  # noqa: BLE001
        log.warning("bolletta.engagement_recompute_failed", err=str(exc))

    return _bolletta_response_from_row(
        upload_id=upload_id,
        ocr=None,
        source=source,
        manual_kwh=body.kwh_yearly,
        manual_eur=body.eur_yearly,
    )


@router.get("/lead/{slug}/savings-compare")
async def get_savings_compare(slug: str) -> dict[str, Any]:
    """Return the predicted-vs-actual savings comparison.

    Pulls the lead's ROI estimate from ``leads.roi_data`` plus the
    latest ``bolletta_uploads`` row (manual values take precedence
    when present, OCR values fall through). Returns ``available=False``
    until at least one bolletta has been uploaded — the
    SavingsComparePanel hides itself in that case.
    """
    sb = get_service_client()
    lead_res = (
        sb.table("leads")
        .select("id, tenant_id, pipeline_status, roi_data, subjects(type)")
        .eq("public_slug", slug)
        .limit(1)
        .execute()
    )
    if not lead_res.data:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead = lead_res.data[0]
    if lead.get("pipeline_status") == LeadStatus.BLACKLISTED.value:
        raise HTTPException(status_code=410, detail="Lead unsubscribed")

    bill_res = (
        sb.table("bolletta_uploads")
        .select(
            "id, ocr_kwh_yearly, ocr_eur_yearly, "
            "manual_kwh_yearly, manual_eur_yearly, source, uploaded_at"
        )
        .eq("lead_id", lead["id"])
        .order("uploaded_at", desc=True)
        .limit(1)
        .execute()
    )
    if not bill_res.data:
        return {"available": False, "reason": "no_bolletta_uploaded"}
    bill = bill_res.data[0]

    # Manual values override OCR — that's the user's correction.
    kwh = bill.get("manual_kwh_yearly") or bill.get("ocr_kwh_yearly")
    eur = bill.get("manual_eur_yearly") or bill.get("ocr_eur_yearly")
    if not kwh or not eur:
        return {"available": False, "reason": "bolletta_values_missing"}

    subject_type = (lead.get("subjects") or {}).get("type") or "unknown"
    result = compute_savings_compare(
        roi_data=lead.get("roi_data"),
        bolletta_kwh_yearly=float(kwh),
        bolletta_eur_yearly=float(eur),
        subject_type=subject_type,
    )
    if result is None:
        return {"available": False, "reason": "roi_data_missing"}

    return {
        "available": True,
        "uploaded_at": bill.get("uploaded_at"),
        "source": bill.get("source"),
        **result.to_jsonb(),
    }


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
_ALLOWED_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "portal.view",
        "portal.scroll_50",
        "portal.scroll_90",
        "portal.roi_viewed",
        "portal.whatsapp_click",
        "portal.appointment_click",
        "portal.video_play",
        "portal.video_complete",
        "portal.heartbeat",
        "portal.leave",
        # Sprint 8 — high-intent portal interactions surfaced by the
        # editorial redesign + bolletta upload + email reply CTA.
        "portal.audio_on",  # user un-muted the hero video (intent signal)
        "portal.video_fullscreen",  # entered fullscreen on hero video
        "portal.email_reply_click",  # clicked the secondary "Rispondi via email" CTA
        "portal.bolletta_uploaded",  # uploaded a bill (B-tier signal — score +50)
        # Contact-form funnel (richiesta di contatto).
        "portal.contact_view",  # opened the contact form (follow-up CTA landing)
        "portal.contact_started",  # typed the first character into the form
        "portal.contact_abandoned",  # typed but left without submitting (partial data + consent in metadata)
        "portal.exit_intent_shown",  # exit-intent popup surfaced
        "portal.exit_intent_dismissed",  # exit-intent popup closed without converting
        "portal.exit_intent_submitted",  # exit-intent mini-form sent
    }
)

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
        "portal.whatsapp_click",
        "portal.appointment_click",
        "portal.video_play",
        "portal.video_complete",
        "portal.heartbeat",
        "portal.leave",
        # Sprint 8 high-intent events
        "portal.audio_on",
        "portal.video_fullscreen",
        "portal.email_reply_click",
        "portal.bolletta_uploaded",
        # Contact-form funnel (richiesta di contatto)
        "portal.contact_view",
        "portal.contact_started",
        "portal.contact_abandoned",
        # Exit-intent popup
        "portal.exit_intent_shown",
        "portal.exit_intent_dismissed",
        "portal.exit_intent_submitted",
    ]
    metadata: dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: int | None = Field(default=None, ge=0, le=24 * 60 * 60 * 1000)


@router.post("/portal/track", status_code=status.HTTP_204_NO_CONTENT)
async def portal_track(request: Request) -> Response:
    """Ingest one engagement event from the lead-portal.

    Returns 204 No Content on success **and** on non-critical soft
    failures (rate-limit hit, Redis outage, unresolved slug, body
    malformato). This matches ``navigator.sendBeacon`` semantics (the
    client can't react to 4xx anyway) and keeps the portal snappy.

    Il body viene letto grezzo e validato a mano: il client invia il
    beacon con Content-Type ``text/plain`` (CORS-safelisted → nessun
    preflight cross-origin), quindi l'endpoint accetta indifferentemente
    ``text/plain`` e ``application/json``.
    """
    import json as _json

    try:
        raw = await request.body()
        event = PortalTrackEvent.model_validate(_json.loads(raw or b"{}"))
    except Exception:  # noqa: BLE001
        return Response(status_code=status.HTTP_204_NO_CONTENT)

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
                "occurred_at": datetime.now(UTC).isoformat(),
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

    # Real-time engagement recompute. We recompute the capped 3-tier
    # score from the lead's actual portal_events rather than blindly
    # incrementing per event: a free-running accumulator (no caps, no
    # dedup) let a lead with zero intent action reach 100/100 just by
    # re-visiting the portal. The recompute also stamps
    # last_portal_event_at, used by the /v1/leads/hot filter.
    try:
        await _recompute_lead_engagement(lead["id"])
    except Exception as exc:  # noqa: BLE001
        # Non-fatal — the nightly rollup still reconciles the score.
        log.warning(
            "portal.track.recompute_failed",
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
        now = datetime.now(UTC)
        key = f"beacon:portal:{slug}:{session_id}:{now.strftime('%Y%m%d%H%M')}"
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
_PIXEL_GIF: bytes = bytes(
    [
        0x47,
        0x49,
        0x46,
        0x38,
        0x39,
        0x61,
        0x01,
        0x00,
        0x01,
        0x00,
        0x80,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0xFF,
        0xFF,
        0xFF,
        0x21,
        0xF9,
        0x04,
        0x01,
        0x00,
        0x00,
        0x00,
        0x00,
        0x2C,
        0x00,
        0x00,
        0x00,
        0x00,
        0x01,
        0x00,
        0x01,
        0x00,
        0x00,
        0x02,
        0x01,
        0x44,
        0x00,
        0x3B,
    ]
)

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
async def record_conversion(slug: str, body: ConversionBody) -> dict[str, object]:
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

    now_iso = datetime.now(UTC).isoformat()
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
            sb.table("conversions").upsert(row, on_conflict="lead_id,stage").execute()
        else:
            sb.table("conversions").insert(  # type: ignore[call-arg]
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
            sb.table("leads").update({"pipeline_status": "closed_won"}).eq(
                "id", lead["id"]
            ).execute()
        except Exception as exc:  # noqa: BLE001
            log.warning("conversion.status_advance_failed", err=str(exc))
    elif stage == "lost" and current_status not in {"closed_won", "closed_lost"}:
        try:
            sb.table("leads").update({"pipeline_status": "closed_lost"}).eq(
                "id", lead["id"]
            ).execute()
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
        .select("id, tenant_id, subject_id, pipeline_status, subjects(pii_hash)")
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
        sb.table("leads").update({"pipeline_status": LeadStatus.BLACKLISTED.value}).eq(
            "id", row["id"]
        ).execute()

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


# Privacy-budget for the public dossier link: 30 giorni dopo l'invio
# dell'outreach il portale non è più raggiungibile. Stessa soglia è
# rispecchiata in `apps/lead-portal/src/app/dossier/[slug]/page.tsx`
# come fallback lato client, ma l'enforcement vero deve essere qui:
# altrimenti chiunque conoscesse lo slug potrebbe scaricare i dati via
# API dopo la scadenza.
_DOSSIER_TTL_DAYS = 30


def _dossier_is_expired(lead: dict[str, Any], *, now: datetime | None = None) -> bool:
    """True quando un lead ha superato la finestra di esposizione.

    Un lead senza ``outreach_sent_at`` non scade mai: può essere in
    anteprima per l'operatore (preview pre-invio).
    """
    sent_at = lead.get("outreach_sent_at")
    if not sent_at:
        return False
    try:
        sent_dt = datetime.fromisoformat(str(sent_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if sent_dt.tzinfo is None:
        sent_dt = sent_dt.replace(tzinfo=UTC)
    age = (now or datetime.now(UTC)) - sent_dt
    return age.days >= _DOSSIER_TTL_DAYS


def _load_lead_by_slug(sb: Any, slug: str, *, allow_expired: bool = False) -> dict[str, Any] | None:
    """Single-row fetch by slug — returns None (not raise) on miss.

    Raises 410 ``dossier_expired`` when the link has aged past the TTL
    (unless ``allow_expired=True`` — used by the optout flow, which
    must always work so a recipient can always unsubscribe).
    """
    res = (
        sb.table("leads")
        .select(
            "id, tenant_id, pipeline_status, outreach_sent_at, "
            "outreach_opened_at, outreach_clicked_at, "
            "dashboard_visited_at, whatsapp_initiated_at, source, "
            "operator_released_at"
        )
        .eq("public_slug", slug)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    lead = res.data[0]
    if not allow_expired and _dossier_is_expired(lead):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="dossier_expired",
        )
    return lead


async def _recompute_roi_after_bolletta(
    sb: Any,
    *,
    lead_id: str,
    tenant_id: str,
    consumption_kwh_yearly: int | float,
    consumption_eur_yearly: int | float | None,
    bolletta_upload_id: str,
) -> None:
    """Recompute ``roof.derivations`` + ``leads.roi_data`` from real consumption.

    Sprint 1.2 — when a prospect uploads their bolletta the OCR
    surfaces the actual annual kWh consumption. Until now we ignored
    that number and kept showing the median-Italian estimate (60% of
    the rooftop's potential production). This helper:

      * fetches the lead's roof + tenant cost_assumptions
      * calls compute_full_derivations with the real consumption as
        the ``self_consumed_kwh`` baseline (override the default
        self_consumption_ratio so the math reflects what the customer
        actually uses, not a generic 60%)
      * UPDATEs roof.derivations + leads.roi_data so every downstream
        surface (dashboard inspector, email body, lead-portal page,
        preventivo PDF) reads the refreshed numbers

    Best-effort — never raises. The bolletta upload completes either
    way; the recompute is a refinement, not a gate.
    """
    from ..services.roi_service import compute_full_derivations

    # Pull the lead row + roof + tenant cost_assumptions in a single
    # round-trip via embedded select.
    res = (
        sb.table("leads")
        .select(
            "id, subject_id, roof_id, "
            "subjects(type), "
            "roofs(id, estimated_kwp, estimated_yearly_kwh, area_sqm)"
        )
        .eq("id", lead_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        log.info(
            "bolletta.roi_recompute_skip_no_lead",
            lead_id=lead_id,
        )
        return
    lead_row = rows[0]
    roof = lead_row.get("roofs") or {}
    subj = lead_row.get("subjects") or {}
    if isinstance(roof, list):
        roof = roof[0] if roof else {}
    if isinstance(subj, list):
        subj = subj[0] if subj else {}
    if not roof.get("id"):
        log.info("bolletta.roi_recompute_skip_no_roof", lead_id=lead_id)
        return

    # Tenant cost_assumptions — we may also override
    # self_consumption_ratio to reflect the customer's real
    # consumption-vs-production split.
    tenant_res = (
        sb.table("tenants").select("cost_assumptions").eq("id", tenant_id).limit(1).execute()
    )
    tenant_cost = (tenant_res.data or [{}])[0].get("cost_assumptions") or {}

    # Real self-consumption ratio: clip(consumption / production, 0, 1).
    # If consumption exceeds production the ratio is 1 (every kWh
    # produced gets self-consumed); the surplus is bought from grid.
    yearly_production = roof.get("estimated_yearly_kwh") or 0.0
    if yearly_production > 0:
        real_ratio = min(1.0, float(consumption_kwh_yearly) / float(yearly_production))
    else:
        real_ratio = 0.6  # fallback to default if production unknown

    # Layer the bolletta-derived ratio on top of the tenant override.
    # The tenant override stays for grid_price + capex tier; we just
    # swap the self_consumption_ratio key.
    subject_type = (subj.get("type") or "b2b").lower()
    refined_assumptions = dict(tenant_cost)
    if subject_type == "b2b":
        refined_assumptions["self_consumption_ratio_b2b"] = real_ratio
    else:
        refined_assumptions["self_consumption_ratio_b2c"] = real_ratio

    derivations = compute_full_derivations(
        estimated_kwp=roof.get("estimated_kwp"),
        estimated_yearly_kwh=roof.get("estimated_yearly_kwh"),
        roof_area_sqm=roof.get("area_sqm"),
        panel_count=None,  # not needed for the refresh path
        subject_type=subject_type,
        tenant_cost_assumptions=refined_assumptions,
    )
    if derivations is None:
        log.info(
            "bolletta.roi_recompute_skip_no_derivations",
            lead_id=lead_id,
        )
        return

    # Annotate the snapshot so the dashboard can show "valori
    # ricalcolati su bolletta del cliente" provenance.
    derivations["assumptions_resolved"] = derivations.get("assumptions_resolved", {})
    derivations["assumptions_resolved"]["consumption_source"] = "bolletta_ocr"
    derivations["assumptions_resolved"]["consumption_kwh_yearly"] = float(consumption_kwh_yearly)
    if consumption_eur_yearly is not None:
        derivations["assumptions_resolved"]["consumption_eur_yearly"] = float(
            consumption_eur_yearly
        )
    derivations["assumptions_resolved"]["consumption_source_upload_id"] = bolletta_upload_id

    # Persist on roof + lead. Keep both in sync so legacy readers
    # (still on roi_data) and new readers (on derivations) see the
    # same fresh numbers.
    try:
        sb.table("roofs").update({"derivations": derivations}).eq("id", roof["id"]).execute()
        # leads.roi_data is the lite-shape (compute_roi.to_jsonb), so
        # we keep a subset of derivations matching that schema. The
        # `_jsonb_subset_for_roi_data` filter strips the sizing/monthly
        # extras that don't fit the legacy shape.
        roi_data_subset = {
            k: derivations[k]
            for k in (
                "estimated_kwp",
                "yearly_kwh",
                "gross_capex_eur",
                "incentive_eur",
                "net_capex_eur",
                "yearly_savings_eur",
                "net_self_savings_eur",
                "savings_25y_eur",
                "roi_pct_25y",
                "trees_equivalent",
                "payback_years",
                "co2_kg_per_year",
                "co2_tonnes_25_years",
                "self_consumption_ratio",
                "meets_roi_target",
            )
            if k in derivations
        }
        sb.table("leads").update({"roi_data": roi_data_subset}).eq("id", lead_id).execute()
        log.info(
            "bolletta.roi_recomputed",
            lead_id=lead_id,
            roof_id=roof["id"],
            consumption_kwh=float(consumption_kwh_yearly),
            real_self_ratio=round(real_ratio, 3),
            new_payback=derivations.get("payback_years"),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "bolletta.roi_persist_failed",
            lead_id=lead_id,
            err_type=type(exc).__name__,
            err=str(exc)[:200],
        )


def _emit_public_event(
    sb: Any,
    *,
    event_type: str,
    tenant_id: str,
    lead_id: str,
    payload: dict[str, Any],
) -> None:
    """Best-effort events insert — never fails the HTTP handler.

    For high-intent inbound events (contact-form submission and
    bolletta upload) we also schedule a fire-and-forget operator
    notification email so the operator hears about the lead within
    seconds rather than at the next dashboard visit. The dashboard
    realtime toaster catches the same events; the email is the
    durable channel for operators not actively looking at the
    dashboard.
    """
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

    # Operator-alert side effect. Best-effort — never blocks the user's
    # request. Failures inside notify_operator log + swallow.
    if event_type in ("lead.appointment_requested", "lead.bolletta_uploaded"):
        try:
            import asyncio

            from ..services.operator_notification_service import notify_operator

            asyncio.create_task(
                notify_operator(
                    tenant_id=tenant_id,
                    lead_id=lead_id,
                    event_type=event_type,
                    payload=payload,
                )
            )
        except RuntimeError:
            # No running event loop (unlikely on a FastAPI route, but
            # defensive). Skip the notification rather than crash.
            log.warning("public.operator_notify_no_loop", event_type=event_type)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "public.operator_notify_schedule_failed",
                event_type=event_type,
                err=str(exc),
            )
