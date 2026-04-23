"""Outbound CRM webhook dispatcher.

Tenants register HTTPS endpoints via
``POST /v1/tenant-config/crm-webhooks`` and subscribe to a set of
lifecycle events (``lead.created``, ``lead.scored``,
``lead.outreach_sent``, ``lead.engaged``, ``lead.contract_signed``).
When those events fire the API enqueues a ``crm_webhook_task``; the
worker resolves the tenant's active subscriptions and POSTs a signed
JSON payload to each one, logging every attempt to
``crm_webhook_deliveries`` for operator visibility.

Design:

    * HMAC-SHA256 over the canonical body using the subscription's
      stored secret. Receivers verify via
      ``X-SolarLead-Signature: sha256=<hex>``.
    * ``X-SolarLead-Event`` identifies the event type.
    * ``X-SolarLead-Delivery`` is a UUID per attempt — useful for
      receiver-side deduplication.
    * Tenacity retries on 5xx / transport errors with exponential
      backoff. 4xx responses are considered terminal — we persist
      them and mark the subscription unhealthy after repeated 4xx.
    * **Dead-letter queue**: when all tenacity retries are exhausted
      (transport error or persistent 5xx) the failed envelope is
      written to ``crm_webhook_dlq`` so operators can inspect and
      replay it from the dashboard.

Tests can import ``build_canonical_payload`` and ``sign`` to exercise
the pure helpers without hitting the network.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client

log = get_logger(__name__)

SUPPORTED_EVENTS: frozenset[str] = frozenset(
    {
        "lead.created",
        "lead.scored",
        "lead.outreach_sent",
        "lead.engaged",
        "lead.contract_signed",
    }
)

# After this many consecutive failures we mark the subscription
# inactive so we stop hammering a broken endpoint. Operators can
# reactivate it from the dashboard after fixing the URL / auth.
FAILURE_CIRCUIT_BREAKER = 10

DEFAULT_TIMEOUT_S = 10


class CrmWebhookError(Exception):
    """Transport-level failure (connect error, 5xx)."""


# ---------------------------------------------------------------------------
# Pure helpers — no I/O, easy to test
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CanonicalPayload:
    """Dispatch envelope — deterministic JSON bytes + hex signature."""

    body: bytes
    signature: str
    delivery_id: str


def build_canonical_payload(
    *,
    event_type: str,
    tenant_id: str,
    occurred_at: str,
    data: dict[str, Any],
    delivery_id: str | None = None,
) -> dict[str, Any]:
    """Return the canonical JSON envelope (pre-serialisation)."""
    return {
        "id": delivery_id or str(uuid.uuid4()),
        "event": event_type,
        "tenant_id": tenant_id,
        "occurred_at": occurred_at,
        "data": data,
    }


def sign(body: bytes, secret: str) -> str:
    """HMAC-SHA256 of the raw body, hex-encoded."""
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def serialize_payload(envelope: dict[str, Any], secret: str) -> CanonicalPayload:
    """Serialize + sign in one shot. Body uses sorted keys / compact sep
    so identical envelopes always produce identical bytes (and thus
    identical signatures).
    """
    body = json.dumps(
        envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return CanonicalPayload(
        body=body,
        signature=sign(body, secret),
        delivery_id=envelope["id"],
    )


# ---------------------------------------------------------------------------
# HTTP dispatch — side-effecting, retried
# ---------------------------------------------------------------------------


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((CrmWebhookError, httpx.TransportError)),
    reraise=True,
)
async def _post_once(
    *, url: str, body: bytes, headers: dict[str, str]
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_S) as client:
        try:
            resp = await client.post(url, content=body, headers=headers)
        except httpx.TransportError as exc:
            raise CrmWebhookError(f"transport error: {exc}") from exc
    if 500 <= resp.status_code < 600:
        # Retry on server errors; the tenacity wrapper will re-enter.
        raise CrmWebhookError(
            f"receiver returned {resp.status_code}: {resp.text[:200]}"
        )
    return resp


async def dispatch_to_subscription(
    *,
    subscription: dict[str, Any],
    event_type: str,
    tenant_id: str,
    occurred_at: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """POST one envelope to one subscription. Logs the delivery row."""
    if event_type not in subscription.get("events", []):
        return {"skipped": "event_not_subscribed"}

    envelope = build_canonical_payload(
        event_type=event_type,
        tenant_id=tenant_id,
        occurred_at=occurred_at,
        data=data,
    )
    canon = serialize_payload(envelope, subscription["secret"])
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "SolarLead/1.0 (+https://solarlead.it)",
        "X-SolarLead-Event": event_type,
        "X-SolarLead-Delivery": canon.delivery_id,
        "X-SolarLead-Signature": canon.signature,
    }

    sb = get_service_client()
    status_code: int | None = None
    response_body: str | None = None
    error: str | None = None
    try:
        resp = await _post_once(
            url=subscription["url"], body=canon.body, headers=headers
        )
        status_code = resp.status_code
        response_body = resp.text[:2000]
    except Exception as exc:  # noqa: BLE001
        error = str(exc)[:2000]
        log.warning(
            "crm_webhook.dispatch_failed",
            subscription_id=subscription["id"],
            event=event_type,
            err=error,
        )
        # All tenacity retries exhausted — persist to DLQ so the operator
        # can inspect the failure and trigger a manual replay later.
        try:
            sb.table("crm_webhook_dlq").insert(
                {
                    "subscription_id": subscription["id"],
                    "tenant_id": tenant_id,
                    "event_type": event_type,
                    "payload": envelope,
                    "error": error,
                }
            ).execute()
            log.info(
                "crm_webhook.dlq_written",
                subscription_id=subscription["id"],
                event=event_type,
            )
        except Exception as dlq_exc:  # noqa: BLE001
            # DLQ write must never surface to the caller — log and continue.
            log.error(
                "crm_webhook.dlq_write_failed",
                subscription_id=subscription["id"],
                dlq_error=str(dlq_exc),
            )

    # Record the attempt regardless of outcome.
    sb.table("crm_webhook_deliveries").insert(
        {
            "subscription_id": subscription["id"],
            "tenant_id": tenant_id,
            "event_type": event_type,
            "payload": envelope,
            "attempt": 1,
            "status_code": status_code,
            "response_body": response_body,
            "error": error,
        }
    ).execute()

    # Update subscription health counters. A 2xx resets the failure
    # count and marks last_status "ok"; anything else increments.
    healthy = status_code is not None and 200 <= status_code < 300
    new_fail_count = 0 if healthy else int(subscription.get("failure_count", 0)) + 1
    update = {
        "last_status": (
            f"{status_code}" if status_code else f"error:{(error or '')[:80]}"
        ),
        "last_delivered_at": "now()",
        "failure_count": new_fail_count,
    }
    if new_fail_count >= FAILURE_CIRCUIT_BREAKER:
        update["active"] = False
        log.warning(
            "crm_webhook.circuit_opened",
            subscription_id=subscription["id"],
            failure_count=new_fail_count,
        )
    sb.table("crm_webhook_subscriptions").update(update).eq(
        "id", subscription["id"]
    ).execute()

    return {
        "subscription_id": subscription["id"],
        "status_code": status_code,
        "healthy": healthy,
    }


async def dispatch_event(
    *,
    tenant_id: str,
    event_type: str,
    occurred_at: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Fan out one event to every active subscription for the tenant.

    Called from the worker. Returns a summary dict for logging.
    """
    if event_type not in SUPPORTED_EVENTS:
        log.warning("crm_webhook.unknown_event", event=event_type)
        return {"event": event_type, "dispatched": 0, "error": "unknown_event"}

    sb = get_service_client()
    res = (
        sb.table("crm_webhook_subscriptions")
        .select("id, tenant_id, url, secret, events, failure_count")
        .eq("tenant_id", tenant_id)
        .eq("active", True)
        .execute()
    )
    subs = res.data or []
    if not subs:
        return {"event": event_type, "dispatched": 0, "reason": "no_subscriptions"}

    summary: list[dict[str, Any]] = []
    for sub in subs:
        out = await dispatch_to_subscription(
            subscription=sub,
            event_type=event_type,
            tenant_id=tenant_id,
            occurred_at=occurred_at,
            data=data,
        )
        summary.append(out)
    return {"event": event_type, "dispatched": len(summary), "results": summary}
