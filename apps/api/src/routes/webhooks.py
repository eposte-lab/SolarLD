"""Inbound webhooks from external providers.

Each endpoint verifies provider-specific signatures, then emits
events that the Tracking Agent consumes to update lead pipeline state.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, status

from ..core.logging import get_logger

log = get_logger(__name__)
router = APIRouter()


@router.post("/resend")
async def resend_webhook(request: Request, svix_signature: str | None = Header(default=None)) -> dict[str, str]:
    """Resend email events: delivered/opened/clicked/bounced/complained."""
    payload = await request.body()
    log.info("webhook.resend", size=len(payload), has_sig=bool(svix_signature))
    # TODO: verify svix signature + enqueue to tracking agent
    return {"ok": "queued"}


@router.post("/pixart")
async def pixart_webhook(request: Request) -> dict[str, str]:
    """Pixartprinting postcard tracking events."""
    payload = await request.body()
    log.info("webhook.pixart", size=len(payload))
    # TODO: verify signature + enqueue to tracking agent
    return {"ok": "queued"}


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request) -> dict[str, str]:
    """360dialog WhatsApp inbound messages."""
    payload = await request.body()
    log.info("webhook.whatsapp", size=len(payload))
    return {"ok": "queued"}


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
) -> dict[str, str]:
    """Stripe subscription + invoice events."""
    if not stripe_signature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe-Signature header",
        )
    payload = await request.body()
    log.info("webhook.stripe", size=len(payload))
    return {"ok": "queued"}
