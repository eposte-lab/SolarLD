"""360dialog WhatsApp send service.

Single-responsibility: given a phone number and message text, send a
WhatsApp message via 360dialog's v1/messages REST endpoint and return
the provider message ID (wamid) on success, or None on failure.

Shared by:
  - OutreachAgent   — outbound WA follow-ups (sequence_step >= 2)
  - ConversationAgent — AI auto-replies to inbound messages

Design decisions
----------------
* No retry logic here — callers own that choice:
  - OutreachAgent surfaces failures as campaigns rows (status='failed');
    the arq worker's own retry will re-enqueue if needed.
  - ConversationAgent degrades gracefully (reply_sent=False, conversation
    row still updated so the thread is intact).
* `wamid` (the provider's message ID) is returned for idempotency and
  tracking — callers store it in `campaigns.email_message_id`
  (reused TEXT field) or `conversations.last_inbound_id`.
* If DIALOG360_API_KEY is not configured the function logs a warning
  and returns None instead of raising — this lets the service start in
  dev/staging without a live 360dialog account.
"""

from __future__ import annotations

import httpx

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)

# 360dialog send-message endpoint (v1, non-cloud BSP)
_DIALOG360_BASE = "https://waba.360dialog.io/v1/messages"

# Approximate cost per WA business-initiated message (utility category,
# Italy) in euro-cents. Used for cost tracking in campaigns rows.
# Update when 360dialog invoices are available — this is a conservative
# estimate based on Meta pricing tier 1 (>250k msg/month).
WA_COST_PER_MESSAGE_CENTS: int = 8  # ~€0.08 per message


async def send_wa_message(
    *,
    phone: str,
    text: str,
    tenant_id: str = "",
) -> str | None:
    """Send a WhatsApp message; return the wamid or None on failure.

    Parameters
    ----------
    phone:
        E.164 number WITHOUT the leading '+', e.g. ``"393331234567"``.
    text:
        Plain-text message body (max ~4096 chars per WhatsApp spec).
    tenant_id:
        Used only for structured log context; not sent to the API.

    Returns
    -------
    str | None
        The 360dialog ``wamid`` (message ID) on success, or ``None``
        if the send failed or no API key is configured.
    """
    if not settings.dialog360_api_key:
        log.warning(
            "dialog360.no_api_key",
            tenant_id=tenant_id,
        )
        return None

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                _DIALOG360_BASE,
                headers={
                    "D360-API-KEY": settings.dialog360_api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code in (200, 201):
            body = resp.json()
            # 360dialog response: {"messages": [{"id": "wamid.xxx"}], ...}
            messages = body.get("messages") or []
            wamid: str = messages[0].get("id", "") if messages else ""
            log.info(
                "dialog360.sent",
                phone_suffix=phone[-4:] if len(phone) >= 4 else "????",
                wamid=wamid,
                tenant_id=tenant_id,
            )
            return wamid or "sent"  # fallback sentinel if id missing
        log.warning(
            "dialog360.send_failed",
            status=resp.status_code,
            body=resp.text[:200],
            tenant_id=tenant_id,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "dialog360.error",
            err=str(exc),
            tenant_id=tenant_id,
        )
        return None
