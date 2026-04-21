"""Inbound webhooks from external providers.

Each endpoint verifies provider-specific signatures, then emits
events that the Tracking Agent consumes to update lead pipeline state.

Resend (email) uses Svix-style HMAC-SHA256 signatures over the raw
request body. We validate ``svix-id``, ``svix-timestamp``,
``svix-signature`` against ``settings.resend_webhook_secret`` before
enqueuing anything, so unauthenticated traffic never reaches the
tracking agent.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse, Response

from ..core.config import settings
from ..core.logging import get_logger
from ..core.queue import enqueue
from ..core.supabase_client import get_service_client
from ..services.resend_service import (
    parse_webhook_event,
    verify_webhook_signature,
)

log = get_logger(__name__)
router = APIRouter()


@router.post("/resend")
async def resend_webhook(
    request: Request,
    svix_id: str | None = Header(default=None, alias="svix-id"),
    svix_timestamp: str | None = Header(default=None, alias="svix-timestamp"),
    svix_signature: str | None = Header(default=None, alias="svix-signature"),
) -> dict[str, str]:
    """Resend email events: delivered/opened/clicked/bounced/complained.

    The full request body is read once and used both as the signature
    payload and as the JSON payload passed to the tracking agent. The
    signature verification is mandatory when ``RESEND_WEBHOOK_SECRET``
    is configured — in development with no secret we still log the
    event for troubleshooting but return 200 so Resend doesn't retry.
    """
    raw_body = await request.body()

    if not settings.resend_webhook_secret:
        log.warning(
            "webhook.resend.no_secret_configured",
            size=len(raw_body),
            has_sig=bool(svix_signature),
        )
        # Development mode: accept but don't process.
        return {"ok": "ignored", "reason": "no_secret_configured"}

    if not (svix_id and svix_timestamp and svix_signature):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Svix signature headers",
        )

    ok = verify_webhook_signature(
        body=raw_body,
        svix_id=svix_id,
        svix_timestamp=svix_timestamp,
        svix_signature=svix_signature,
        secret=settings.resend_webhook_secret,
    )
    if not ok:
        log.warning(
            "webhook.resend.signature_invalid",
            svix_id=svix_id,
            size=len(raw_body),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signature",
        )

    try:
        payload_json = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON body",
        ) from exc

    # Surface the Svix message id inside the envelope so the tracking
    # agent can dedupe retries without re-reading request headers.
    envelope = dict(payload_json)
    envelope.setdefault("id", svix_id)

    # Quick sanity-parse: if the body is not an email event we still
    # accept it (Resend may add new event types), but we log what it is.
    try:
        parsed = parse_webhook_event(envelope)
        log.info(
            "webhook.resend.accepted",
            type=parsed.type,
            email_id=parsed.email_id,
            svix_id=svix_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "webhook.resend.unparseable",
            svix_id=svix_id,
            err=str(exc),
        )

    await enqueue(
        "tracking_task",
        {
            "provider": "resend",
            "event_type": str(payload_json.get("type") or ""),
            "raw_payload": envelope,
        },
        # Dedupe on the Svix id so arq collapses duplicates.
        job_id=f"tracking:resend:{svix_id}",
    )
    return {"ok": "queued"}


@router.post("/email-inbound")
async def email_inbound_webhook(
    request: Request,
    secret: str | None = Query(default=None),
) -> dict[str, str]:
    """Resend inbound email webhook — receives replies from leads.

    Authentication: URL-based shared secret (``?secret=…``). Resend
    inbound doesn't support Svix HMAC, so we use a long random token
    configured via ``RESEND_INBOUND_SECRET`` instead.

    The ``to`` field of the inbound message encodes the lead's
    ``public_slug`` in the address local-part:
        ``reply+{slug}@{domain}``
    We extract the slug, resolve the lead, insert a ``lead_replies``
    row, and enqueue the ``RepliesAgent`` for async Claude analysis.

    In development (``resend_inbound_secret`` empty) we accept the
    request and log it for troubleshooting — same policy as the
    outbound webhook.
    """
    raw_body = await request.body()

    # Auth gate
    if settings.resend_inbound_secret:
        if secret != settings.resend_inbound_secret:
            log.warning(
                "webhook.email_inbound.auth_failed",
                has_secret=bool(secret),
                size=len(raw_body),
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing secret",
            )
    else:
        log.warning(
            "webhook.email_inbound.no_secret_configured",
            size=len(raw_body),
        )
        return {"ok": "ignored", "reason": "no_secret_configured"}

    # Parse body
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON body",
        ) from exc

    log.info("webhook.email_inbound.received", keys=list(payload.keys()))

    # ----------------------------------------------------------------
    # Extract slug from the `to` address.
    # Resend inbound sends the `to` field as a list of objects or
    # strings; we look for the first entry matching ``reply+*@*``.
    # ----------------------------------------------------------------
    slug = _extract_slug_from_to(payload.get("to") or [])
    if not slug:
        log.warning(
            "webhook.email_inbound.no_slug",
            to=payload.get("to"),
        )
        # Return 200 so Resend doesn't retry — it's a mis-routed message.
        return {"ok": "ignored", "reason": "no_slug_in_to"}

    # ----------------------------------------------------------------
    # Resolve lead by public_slug
    # ----------------------------------------------------------------
    sb = get_service_client()
    lead_res = (
        sb.table("leads")
        .select("id, tenant_id, public_slug")
        .eq("public_slug", slug)
        .limit(1)
        .execute()
    )
    leads = lead_res.data or []
    if not leads:
        log.warning(
            "webhook.email_inbound.lead_not_found",
            slug=slug,
        )
        return {"ok": "ignored", "reason": "lead_not_found"}

    lead = leads[0]
    lead_id = lead["id"]
    tenant_id = lead["tenant_id"]

    # ----------------------------------------------------------------
    # Insert lead_replies row (unanalysed; RepliesAgent will fill it)
    # ----------------------------------------------------------------
    from_email = _extract_from_email(payload.get("from") or "")
    body_text = (
        payload.get("text")
        or _strip_html(payload.get("html") or "")
        or ""
    ).strip()
    reply_subject = (payload.get("subject") or "").strip() or None

    now_iso = datetime.now(timezone.utc).isoformat()
    insert_res = (
        sb.table("lead_replies")
        .insert(
            {
                "tenant_id": tenant_id,
                "lead_id": lead_id,
                "from_email": from_email,
                "reply_subject": reply_subject,
                "body_text": body_text or None,
                "received_at": now_iso,
            }
        )
        .execute()
    )
    reply_rows = insert_res.data or []
    if not reply_rows:
        log.error(
            "webhook.email_inbound.insert_failed",
            lead_id=lead_id,
        )
        return {"ok": "error", "reason": "db_insert_failed"}

    reply_id = reply_rows[0]["id"]

    # ----------------------------------------------------------------
    # Enqueue RepliesAgent for async Claude analysis
    # ----------------------------------------------------------------
    await enqueue(
        "replies_task",
        {
            "reply_id": reply_id,
            "tenant_id": tenant_id,
            "lead_id": lead_id,
        },
        job_id=f"replies:{reply_id}",
    )

    log.info(
        "webhook.email_inbound.queued",
        reply_id=reply_id,
        lead_id=lead_id,
        tenant_id=tenant_id,
    )
    return {"ok": "queued", "reply_id": reply_id}


# ---------------------------------------------------------------------------
# Inbound helpers
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"reply\+([A-Za-z0-9_-]+)@", re.IGNORECASE)


def _extract_slug_from_to(to_field: list | str | None) -> str | None:
    """Extract lead public_slug from the ``reply+{slug}@domain`` address.

    Resend inbound can deliver `to` as:
      - a list of strings  [``"reply+abc123@example.com"``]
      - a list of objects  [``{"email": "reply+abc123@example.com", "name": "..."}``]
      - a plain string     ``"reply+abc123@example.com"``
    """
    if not to_field:
        return None
    items: list[str] = []
    if isinstance(to_field, str):
        items = [to_field]
    elif isinstance(to_field, list):
        for item in to_field:
            if isinstance(item, str):
                items.append(item)
            elif isinstance(item, dict):
                items.append(item.get("email") or "")
    for addr in items:
        m = _SLUG_RE.search(addr)
        if m:
            return m.group(1)
    return None


def _extract_from_email(from_field: str | dict | None) -> str:
    """Normalise the From field to a plain email address."""
    if not from_field:
        return "unknown@unknown"
    if isinstance(from_field, dict):
        return (from_field.get("email") or "unknown@unknown").strip().lower()
    # Could be "Name <email@domain>" or plain "email@domain"
    raw = str(from_field).strip()
    m = re.search(r"<([^>]+)>", raw)
    return (m.group(1) if m else raw).strip().lower() or "unknown@unknown"


def _strip_html(html: str) -> str:
    """Very lightweight HTML → plain text for fallback body extraction."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


@router.post("/pixart")
async def pixart_webhook(request: Request) -> dict[str, str]:
    """Pixartprinting postcard tracking events.

    Authentication: Pixart signs each request with HMAC-SHA256 over the
    raw request body in ``X-Pixart-Signature``. If ``PIXART_WEBHOOK_SECRET``
    is configured we enforce the signature (401 on mismatch); in
    development without a secret we accept + log + ignore — same policy
    as the Resend webhook.

    Payload: the exact field names depend on Pixart's API version. We
    look for a tracking identifier under either ``tracking_number`` or
    ``tracking_code`` (the two names documented by Pixart over the years)
    and an event type under ``event_type`` / ``status`` (``printed`` →
    ``shipped`` → ``delivered`` → ``returned``). The TrackingAgent
    normalises these into ``lead.postal_*`` events.
    """
    import hashlib
    import hmac as _hmac

    raw_body = await request.body()

    # ------------------------------------------------------------------
    # 1) Auth (HMAC-SHA256)
    # ------------------------------------------------------------------
    sig_header = (
        request.headers.get("X-Pixart-Signature")
        or request.headers.get("x-pixart-signature")
        or ""
    )
    if not settings.pixart_webhook_secret:
        log.warning(
            "webhook.pixart.no_secret_configured",
            size=len(raw_body),
            has_sig=bool(sig_header),
        )
        return {"ok": "ignored", "reason": "no_secret_configured"}

    if not sig_header:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-Pixart-Signature header",
        )

    expected = _hmac.new(
        settings.pixart_webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    # Pixart's signature may be sent as either "sha256=<hex>" or just "<hex>".
    presented = sig_header.split("=", 1)[-1].strip()
    if not _hmac.compare_digest(expected, presented):
        log.warning(
            "webhook.pixart.signature_invalid",
            size=len(raw_body),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signature",
        )

    # ------------------------------------------------------------------
    # 2) Parse body
    # ------------------------------------------------------------------
    try:
        payload_json: dict = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON body",
        ) from exc

    # ------------------------------------------------------------------
    # 3) Extract tracking id + event type (defensive on field names)
    # ------------------------------------------------------------------
    tracking_id = (
        payload_json.get("tracking_number")
        or payload_json.get("tracking_code")
        or payload_json.get("trackingId")
        or ""
    )
    event_type = (
        payload_json.get("event_type")
        or payload_json.get("status")
        or payload_json.get("type")
        or ""
    ).strip().lower()

    if not tracking_id:
        log.warning(
            "webhook.pixart.no_tracking_id",
            keys=list(payload_json.keys()),
        )
        # 200 so Pixart doesn't retry forever — mis-routed event
        return {"ok": "ignored", "reason": "no_tracking_id"}

    if not event_type:
        log.warning(
            "webhook.pixart.no_event_type",
            tracking_id=tracking_id,
        )
        return {"ok": "ignored", "reason": "no_event_type"}

    # ------------------------------------------------------------------
    # 4) Enqueue tracking task (TrackingAgent resolves campaign+lead)
    # ------------------------------------------------------------------
    await enqueue(
        "tracking_task",
        {
            "provider": "pixart",
            "event_type": event_type,
            "raw_payload": payload_json,
        },
        # Dedupe on tracking_id + event_type so retries collapse
        job_id=f"tracking:pixart:{tracking_id}:{event_type}",
    )
    log.info(
        "webhook.pixart.queued",
        tracking_id=tracking_id,
        event_type=event_type,
    )
    return {"ok": "queued"}


@router.post("/whatsapp")
async def whatsapp_webhook(
    request: Request,
    tenant_id: str | None = Query(default=None),
) -> dict[str, str]:
    """360dialog WhatsApp inbound messages (Part B.8).

    Authentication: 360dialog signs each request with a HMAC-SHA256
    signature in ``X-360dialog-Signature`` (if ``DIALOG360_WEBHOOK_SECRET``
    is configured). In development without a secret we still process the
    message.

    Tenant routing: the webhook URL is configured per-tenant in the
    360dialog dashboard as:
        ``POST /v1/webhooks/whatsapp?tenant_id={tenant_id}``
    This avoids a costly phone→tenant DB lookup.

    Lead routing:
    1. If the message body starts with ``SL-{slug}``, we use that slug to
       find the lead (first message from the portal's WA deep link).
    2. Otherwise, we look up an existing ``conversations`` row for
       ``(tenant_id, phone)`` and reuse its ``lead_id``.
    3. If neither matches, we log and return 200 (no-op) — orphaned
       WA messages from unknown senders shouldn't retry forever.
    """
    import hashlib
    import hmac as _hmac

    raw_body = await request.body()

    # ------------------------------------------------------------------
    # 1) Auth (360dialog HMAC-SHA256)
    # ------------------------------------------------------------------
    sig_header = request.headers.get("X-360dialog-Signature") or ""
    if settings.dialog360_webhook_secret and sig_header:
        expected = _hmac.new(
            settings.dialog360_webhook_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not _hmac.compare_digest(expected, sig_header):
            log.warning("webhook.whatsapp.invalid_signature")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid signature",
            )

    # ------------------------------------------------------------------
    # 2) Parse body
    # ------------------------------------------------------------------
    try:
        payload_json: dict = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON"
        ) from exc

    messages_raw: list[dict] = payload_json.get("messages") or []
    if not messages_raw:
        # 360dialog sends delivery receipts + read notifications with no
        # messages[] — accept silently so it doesn't retry.
        return {"ok": "noop"}

    if not tenant_id:
        log.warning("webhook.whatsapp.no_tenant_id")
        return {"ok": "ignored", "reason": "no_tenant_id"}

    sb = get_service_client()

    processed = 0
    for msg in messages_raw:
        if msg.get("type") != "text":
            # Ignore non-text messages (images, stickers, reactions, …)
            continue

        wa_phone: str = str(msg.get("from") or "").strip()
        if not wa_phone:
            continue
        text: str = (msg.get("text") or {}).get("body") or ""
        message_id: str = str(msg.get("id") or "").strip()
        if not text.strip():
            continue

        # ------------------------------------------------------------------
        # 3) Resolve lead_id
        # ------------------------------------------------------------------
        lead_id: str | None = None

        # A) Slug in first message: "SL-{slug}" or "SL:{slug}"
        slug_match = re.match(r"^SL[-:]([A-Za-z0-9_-]+)", text.strip(), re.IGNORECASE)
        if slug_match:
            slug = slug_match.group(1)
            lead_res = (
                sb.table("leads")
                .select("id")
                .eq("public_slug", slug)
                .eq("tenant_id", tenant_id)
                .limit(1)
                .execute()
            )
            rows = lead_res.data or []
            if rows:
                lead_id = rows[0]["id"]

        # B) Existing conversation for this phone
        if not lead_id:
            conv_res = (
                sb.table("conversations")
                .select("lead_id")
                .eq("tenant_id", tenant_id)
                .eq("whatsapp_phone", wa_phone)
                .limit(1)
                .execute()
            )
            rows = conv_res.data or []
            if rows:
                lead_id = rows[0]["lead_id"]

        if not lead_id:
            log.warning(
                "webhook.whatsapp.lead_not_found",
                tenant_id=tenant_id,
                phone_suffix=wa_phone[-4:] if len(wa_phone) >= 4 else "??",
            )
            # Return 200 so 360dialog doesn't retry
            continue

        # ------------------------------------------------------------------
        # 4) Enqueue ConversationAgent
        # ------------------------------------------------------------------
        job_id = f"conversation:{tenant_id}:{wa_phone}:{message_id or datetime.now(timezone.utc).isoformat()}"
        await enqueue(
            "conversation_task",
            {
                "tenant_id": tenant_id,
                "lead_id": lead_id,
                "wa_phone": wa_phone,
                "incoming_text": text.strip(),
                "message_id": message_id,
            },
            job_id=job_id,
        )
        log.info(
            "webhook.whatsapp.queued",
            lead_id=lead_id,
            tenant_id=tenant_id,
        )
        processed += 1

    return {"ok": "queued", "processed": processed}


# ---------------------------------------------------------------------------
# Meta Lead Ads — inbound lead submissions from Facebook/Instagram ads
# ---------------------------------------------------------------------------
#
# Meta (Facebook Graph) posts lead events to a single URL shared across
# all tenants. Every tenant that connects their Page has an
# ``meta_connections`` row holding the Page id, ad-account id and a
# per-tenant ``webhook_secret``. The incoming payload carries the
# ``page_id`` in ``entry[].id`` which we use to route the lead to the
# owning tenant before signature verification (the secret lookup is keyed
# on page id).
#
# Meta's verification flow has two halves:
#   1. GET /v1/webhooks/meta-leads?hub.mode=subscribe&hub.verify_token=…
#      &hub.challenge=… — one-time challenge we echo back verbatim when
#      the verify token matches ``settings.meta_app_verify_token``.
#   2. POST /v1/webhooks/meta-leads — signed with ``X-Hub-Signature-256``
#      = ``sha256=<hmac_hex>`` over the raw body using the tenant's
#      ``meta_connections.webhook_secret`` (a.k.a. the Meta app secret).
#
# Meta retries on non-2xx up to ~3 times with exponential backoff, so we
# upsert by ``(tenant_id, meta_lead_id)`` to make the handler idempotent.


@router.get("/meta-leads")
async def meta_leads_verify(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> Response:
    """Meta subscription verification challenge.

    Meta sends this once when the webhook is registered. We compare the
    presented ``hub.verify_token`` against our configured token and echo
    back ``hub.challenge`` as plain text if they match.
    """
    if hub_mode != "subscribe":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported hub.mode",
        )
    expected = settings.meta_app_verify_token or ""
    if not expected:
        log.warning("webhook.meta_leads.no_verify_token_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Meta verify token not configured",
        )
    if hub_verify_token != expected:
        log.warning("webhook.meta_leads.verify_token_mismatch")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Verify token mismatch",
        )
    return PlainTextResponse(hub_challenge or "")


@router.post("/meta-leads")
async def meta_leads_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(
        default=None, alias="X-Hub-Signature-256"
    ),
) -> dict[str, str | int]:
    """Meta Lead Ads lead-submission webhook.

    Flow:
      1. Resolve tenant from ``entry[].id`` (Meta page id) →
         ``meta_connections`` row.
      2. Verify HMAC-SHA256 with the row's ``webhook_secret``.
      3. For each ``changes[].value.leadgen_id`` in the payload, upsert a
         ``leads`` row with ``source='b2c_meta_ads'``. The actual lead
         field fetch (name/email/phone via Graph API using the row's
         access token) is deferred to an async task so the webhook
         responds in <1s.
    """
    import hashlib
    import hmac as _hmac

    raw_body = await request.body()

    try:
        payload_json: dict = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON body",
        ) from exc

    if payload_json.get("object") != "page":
        # Meta can also post ``object='instagram'`` but we only opt-in
        # to page-level lead ads today.
        log.info(
            "webhook.meta_leads.ignored_object",
            obj=payload_json.get("object"),
        )
        return {"ok": "ignored", "reason": "non_page_object"}

    entries = payload_json.get("entry") or []
    if not entries:
        return {"ok": "ignored", "reason": "no_entries"}

    # ----------------------------------------------------------------
    # Tenant routing — use the first entry's page id to locate the
    # connection + webhook_secret. All entries in one POST always
    # belong to the same app, but Meta doesn't guarantee same page,
    # so we re-check per entry below.
    # ----------------------------------------------------------------
    first_page_id = str(entries[0].get("id") or "")
    if not first_page_id:
        return {"ok": "ignored", "reason": "no_page_id"}

    sb = get_service_client()
    conn_res = (
        sb.table("meta_connections")
        .select("tenant_id, webhook_secret, access_token")
        .eq("meta_page_id", first_page_id)
        .limit(1)
        .execute()
    )
    conn_rows = conn_res.data or []
    if not conn_rows:
        log.warning(
            "webhook.meta_leads.unknown_page",
            page_id=first_page_id,
        )
        # 200 so Meta doesn't retry — page is not ours (or was
        # disconnected).
        return {"ok": "ignored", "reason": "unknown_page"}

    connection = conn_rows[0]
    secret = connection["webhook_secret"]

    # ----------------------------------------------------------------
    # HMAC verification — Meta sends "sha256=<hex>" in the header.
    # ----------------------------------------------------------------
    if not x_hub_signature_256:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-Hub-Signature-256",
        )
    expected = "sha256=" + _hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    if not _hmac.compare_digest(expected, x_hub_signature_256):
        log.warning(
            "webhook.meta_leads.signature_invalid",
            page_id=first_page_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signature",
        )

    tenant_id = connection["tenant_id"]

    # ----------------------------------------------------------------
    # Upsert each leadgen event. We store the Meta lead id so the
    # follow-up Graph API field-fetch task can enrich the row.
    # ----------------------------------------------------------------
    now_iso = datetime.now(timezone.utc).isoformat()
    accepted = 0
    for entry in entries:
        if str(entry.get("id") or "") != first_page_id:
            # Mixed-page batches should never happen, but if they do
            # skip the stray rather than mis-attributing.
            log.warning(
                "webhook.meta_leads.mixed_pages",
                entry_page_id=entry.get("id"),
            )
            continue
        for change in entry.get("changes") or []:
            if change.get("field") != "leadgen":
                continue
            value = change.get("value") or {}
            leadgen_id = str(value.get("leadgen_id") or "")
            if not leadgen_id:
                continue

            # Upsert a bare lead row — enrichment (Graph API
            # /{leadgen_id}?fields=field_data) happens in the
            # ``meta_lead_enrich`` arq task added in Phase 3.6 so
            # the webhook stays fast.
            sb.table("leads").upsert(
                {
                    "tenant_id": tenant_id,
                    "meta_lead_id": leadgen_id,
                    "source": "b2c_meta_ads",
                    "pipeline_status": "new",
                    "inbound_payload": {
                        "leadgen_id": leadgen_id,
                        "form_id": value.get("form_id"),
                        "ad_id": value.get("ad_id"),
                        "page_id": value.get("page_id"),
                        "created_time": value.get("created_time"),
                        "received_at": now_iso,
                    },
                    # public_slug is generated by a DB trigger in
                    # earlier migrations for b2b rows; for b2c we
                    # synthesise one here since the trigger keys on
                    # roof_id. Short, URL-safe.
                    "public_slug": f"meta_{leadgen_id[-12:]}",
                },
                on_conflict="tenant_id,meta_lead_id",
            ).execute()

            await enqueue(
                "meta_lead_enrich_task",
                {
                    "tenant_id": tenant_id,
                    "leadgen_id": leadgen_id,
                },
                job_id=f"meta_lead_enrich:{tenant_id}:{leadgen_id}",
            )
            accepted += 1

    log.info(
        "webhook.meta_leads.accepted",
        tenant_id=str(tenant_id),
        page_id=first_page_id,
        accepted=accepted,
    )
    return {"ok": "queued", "accepted": accepted}


# Stripe webhook intentionally not registered in this release — tier
# activation is manual (see apps/dashboard/src/lib/data/tier.ts and
# tier-lock.tsx). When billing is introduced add a handler here that
# verifies `Stripe-Signature` via `stripe.Webhook.construct_event` and
# persists subscription lifecycle to a dedicated `tenant_subscriptions`
# table. Leaving a stub that accepts signed payloads and does nothing
# is strictly worse than returning 404 — it makes Stripe's dashboard
# show healthy deliveries while the system silently ignores upgrades
# and cancellations.
