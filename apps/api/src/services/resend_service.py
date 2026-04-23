"""Resend HTTP client — send transactional emails + verify inbound webhooks.

API surface is intentionally tiny:

    POST  https://api.resend.com/emails           (send)

Resend webhooks arrive signed via Svix. The signature header is
``svix-signature`` and the payload is signed with
``WHSEC_<base64>``-formatted secrets — we accept both the raw and
base64-decoded forms so the same code works across Resend's SDK
versions.

Split into:

    * ``send_email(...)`` — async HTTP entry point with retries.
    * ``parse_send_response(raw)`` — pure projection for tests.
    * ``parse_webhook_event(raw)`` — normalise Resend's JSON into our
      internal ``EmailEvent`` dataclass.
    * ``verify_webhook_signature(body, svix_id, svix_timestamp,
      svix_signature, secret)`` — pure bytes-in, bool-out.

Nothing here touches the DB. The OutreachAgent and TrackingAgent are
responsible for side effects.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)

RESEND_API_BASE = "https://api.resend.com"
# Per Svix, signatures older than 5 minutes are rejected to prevent
# replay attacks. We match the same window.
SVIX_TOLERANCE_SECONDS = 5 * 60

# Resend pricing is ~$0.0004 / email on the Pro plan. We log 1 cent
# per 25 emails; rounded up to 1 cent per email keeps things simple for
# `api_usage_log` aggregation.
RESEND_COST_PER_EMAIL_CENTS = 1


class ResendError(Exception):
    """Raised when Resend returns an error status.

    ``status_code`` carries the HTTP response code so callers can decide
    whether to auto-pause the sending inbox:
      * 429 → rate-limited by Resend for this sender → short pause (2 h)
      * 5xx → server-side error → medium pause (4 h)
      * Other 4xx → bad recipient / payload → don't pause the inbox
    """

    def __init__(self, message: str, *, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


class ResendSignatureError(Exception):
    """Raised when a webhook signature cannot be verified."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SendEmailInput:
    from_address: str
    to: list[str]
    subject: str
    html: str
    text: str | None = None
    reply_to: str | None = None
    tags: dict[str, str] | None = None
    headers: dict[str, str] | None = None


@dataclass(slots=True)
class SendEmailResult:
    id: str           # Resend message id, ex: "d1e3f-…"


@dataclass(slots=True)
class EmailEvent:
    """Normalised Resend webhook event.

    The Resend payload is inconsistent across event types — some use
    ``data.email_id``, some use ``data.id``. We smooth it out here so
    the TrackingAgent's switch statement stays small.
    """

    id: str                         # Svix msg id, used for idempotency
    type: str                       # delivered | opened | clicked | bounced | complained
    email_id: str                   # Resend's message id → matches campaigns.email_message_id
    occurred_at: str | None         # ISO timestamp
    to: list[str]
    raw: dict[str, Any]


# ---------------------------------------------------------------------------
# Pure helpers — fully unit-tested
# ---------------------------------------------------------------------------


def build_send_payload(data: SendEmailInput) -> dict[str, Any]:
    body: dict[str, Any] = {
        "from": data.from_address,
        "to": data.to,
        "subject": data.subject,
        "html": data.html,
    }
    if data.text:
        body["text"] = data.text
    if data.reply_to:
        body["reply_to"] = data.reply_to
    if data.tags:
        # Resend expects [{"name":..., "value":...}, ...]
        body["tags"] = [{"name": k, "value": v} for k, v in data.tags.items()]
    if data.headers:
        body["headers"] = dict(data.headers)
    return body


def parse_send_response(raw: dict[str, Any]) -> SendEmailResult:
    """Project Resend's send-email response into our dataclass."""
    mid = raw.get("id")
    if not isinstance(mid, str) or not mid:
        raise ResendError(f"missing id in send response: {raw!r}")
    return SendEmailResult(id=mid)


_EVENT_ALIASES = {
    "email.delivered": "delivered",
    "email.delivery_delayed": "delivery_delayed",
    "email.opened": "opened",
    "email.clicked": "clicked",
    "email.bounced": "bounced",
    "email.complained": "complained",
    "email.sent": "sent",
}


def parse_webhook_event(raw: dict[str, Any]) -> EmailEvent:
    """Normalise the Resend webhook JSON envelope.

    Resend wraps events as:
        {"type": "email.delivered",
         "created_at": "2026-…",
         "data": { "email_id"|"id": "…", "to": [...], "subject": "...", ... }}
    """
    etype_raw = str(raw.get("type") or "")
    etype = _EVENT_ALIASES.get(etype_raw, etype_raw.split(".")[-1] or "unknown")

    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    assert isinstance(data, dict)
    email_id = str(data.get("email_id") or data.get("id") or "")
    if not email_id:
        raise ResendError(f"missing email_id in webhook payload: {raw!r}")

    to_field: Any = data.get("to") or []
    if isinstance(to_field, str):
        to_list = [to_field]
    elif isinstance(to_field, list):
        to_list = [str(x) for x in to_field if isinstance(x, (str, bytes))]
    else:
        to_list = []

    occurred_at = raw.get("created_at") or data.get("created_at")
    return EmailEvent(
        id=str(raw.get("id") or data.get("id") or ""),
        type=etype,
        email_id=email_id,
        occurred_at=str(occurred_at) if occurred_at else None,
        to=to_list,
        raw=raw,
    )


def verify_webhook_signature(
    *,
    body: bytes,
    svix_id: str,
    svix_timestamp: str,
    svix_signature: str,
    secret: str,
    tolerance_seconds: int = SVIX_TOLERANCE_SECONDS,
    now_ts: int | None = None,
) -> bool:
    """Verify a Svix-style signed webhook (Resend uses Svix under the hood).

    Docs: https://docs.svix.com/receiving/verifying-payloads/how-manual
    Signing formula: ``HMAC-SHA256(body="{msg_id}.{ts}.{body}")``.
    The header carries one or more ``v1,<base64>`` space-separated
    signatures — we accept the message if *any* of them matches.
    """
    if not svix_id or not svix_timestamp or not svix_signature or not secret:
        return False
    # Anti-replay: reject stale timestamps.
    try:
        ts = int(svix_timestamp)
    except ValueError:
        return False
    current = now_ts if now_ts is not None else int(time.time())
    if abs(current - ts) > tolerance_seconds:
        return False

    # Strip the "whsec_" prefix and base64-decode if present.
    key = secret
    if key.startswith("whsec_"):
        key = key[len("whsec_") :]
    try:
        key_bytes = base64.b64decode(key)
    except Exception:  # noqa: BLE001
        key_bytes = key.encode("utf-8")

    signed_payload = f"{svix_id}.{svix_timestamp}.".encode("utf-8") + body
    expected = hmac.new(key_bytes, signed_payload, hashlib.sha256).digest()
    expected_b64 = base64.b64encode(expected).decode("ascii")

    # Header format: "v1,sig1 v1,sig2 ..." — one per rotated key.
    for candidate in svix_signature.split():
        if "," not in candidate:
            continue
        _version, sig = candidate.split(",", 1)
        if hmac.compare_digest(sig, expected_b64):
            return True
    return False


# ---------------------------------------------------------------------------
# HTTP entry point
# ---------------------------------------------------------------------------


def _auth_headers() -> dict[str, str]:
    if not settings.resend_api_key:
        raise ResendError("RESEND_API_KEY not configured")
    return {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
    }


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def send_email(
    data: SendEmailInput,
    *,
    client: httpx.AsyncClient | None = None,
    timeout_s: float = 30.0,
) -> SendEmailResult:
    """POST /emails — returns Resend's message id on success.

    A 4xx is treated as permanent (bad from-domain, invalid address,
    suppression-list hit). 5xx/transport errors are retried with
    exponential backoff.
    """
    body = build_send_payload(data)
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        resp = await client.post(
            f"{RESEND_API_BASE}/emails",
            headers=_auth_headers(),
            json=body,
        )
    finally:
        if owns_client:
            await client.aclose()

    if resp.status_code >= 500:
        raise ResendError(
            f"resend 5xx status={resp.status_code} body={resp.text[:300]}",
            status_code=resp.status_code,
        )
    if resp.status_code >= 400:
        raise ResendError(
            f"resend 4xx status={resp.status_code} body={resp.text[:300]}",
            status_code=resp.status_code,
        )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise ResendError(f"resend non-json response: {exc}") from exc
    return parse_send_response(payload)
