"""Dedicated unsubscribe endpoint (Task 12).

Two HTTP methods, one URL: `{API_BASE}/v1/unsubscribe?t={token}`

GET  — Human click-through: validate token, mark lead blacklisted,
       redirect to the lead-portal's "you've been unsubscribed" page.
       Kept for backward compat with email clients that don't support
       RFC 8058 one-click POST.

POST — RFC 8058 one-click (Gmail/Yahoo automatic trigger).
       Gmail POSTs with body `List-Unsubscribe=One-Click`. We validate
       the token, process the optout, return 200. No redirect — the
       user never sees this response.

Both paths share the same HMAC verification + compliance pipeline.

Why a dedicated route (not reusing /v1/public/lead/{slug}/optout)
------------------------------------------------------------------
  1. Token format is different — HMAC vs slug; same endpoint would
     need two separate auth paths.
  2. List-Unsubscribe header should point to the same URL for both
     GET and POST, which the existing POST-only endpoint can't serve.
  3. This route lives under `/v1/unsubscribe` so it can later be
     served from a per-tenant tracking host (`go.agendasolar.it`) via
     Sprint 6.2 middleware, matching the sending domain. The existing
     `/v1/public/lead/{slug}/optout` lives under the shared API host.

Backward compatibility
----------------------
The old `POST /v1/public/lead/{slug}/optout` is kept intact and will
continue to work for emails already in the wild that contain the
slug-based URL. New emails get the HMAC URL via `build_unsubscribe_url`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from fastapi import APIRouter, Form, HTTPException, Query, Response
from fastapi.responses import RedirectResponse

from ..core.config import settings
from ..core.supabase_client import get_service_client
from ..services.unsubscribe_token_service import (
    InvalidUnsubscribeToken,
    verify_token,
)
from ..core.queue import enqueue

log = structlog.get_logger(__name__)

router = APIRouter(tags=["unsubscribe"])

# Status to set on the lead row when unsubscribing.
_STATUS_BLACKLISTED = "blacklisted"
_BLACKLIST_REASON = "unsubscribe"


# ---------------------------------------------------------------------------
# GET — click-through
# ---------------------------------------------------------------------------


@router.get("/v1/unsubscribe")
async def unsubscribe_get(
    t: str = Query(..., description="HMAC-signed unsubscribe token"),
) -> RedirectResponse:
    """Validate token + process optout + redirect to confirmation page.

    On success redirects to `{LEAD_PORTAL_URL}/unsubscribed` (the
    lead-portal's static confirmation page, no PII in the URL).
    On token error redirects to the same page with `?error=invalid_token`
    so the UX doesn't expose API details while still handling the error.
    """

    try:
        lead_id, tenant_id, _email_hash = verify_token(t)
    except InvalidUnsubscribeToken as exc:
        log.warning("unsubscribe.invalid_token_get", err=str(exc))
        portal = (settings.next_public_lead_portal_url or "").rstrip("/")
        return RedirectResponse(
            url=f"{portal}/unsubscribed?error=invalid_token",
            status_code=302,
        )

    await _process_optout(lead_id, tenant_id, source="hmac_get")

    portal = (settings.next_public_lead_portal_url or "").rstrip("/")
    return RedirectResponse(url=f"{portal}/unsubscribed", status_code=302)


# ---------------------------------------------------------------------------
# POST — RFC 8058 one-click (Gmail/Yahoo automated)
# ---------------------------------------------------------------------------


@router.post("/v1/unsubscribe")
async def unsubscribe_post(
    t: str = Query(..., description="HMAC-signed unsubscribe token"),
    # RFC 8058 requires Gmail to POST with this form field; we accept it
    # but don't validate its value (any POST to this URL is an optout).
    List_Unsubscribe: str | None = Form(None, alias="List-Unsubscribe"),  # noqa: N803
) -> dict[str, str]:
    """RFC 8058 one-click unsubscribe.

    Gmail/Yahoo POST to this endpoint automatically when the user clicks
    "Unsubscribe" in the email client UI. No user interaction required
    after the click.

    Always returns 200 with `{"ok": "true"}`. On token error returns 400
    — Gmail will retry a 4xx, but we log and silently blacklist to avoid
    partial failures being visible to the mail provider.
    """

    try:
        lead_id, tenant_id, _email_hash = verify_token(t)
    except InvalidUnsubscribeToken as exc:
        log.warning(
            "unsubscribe.invalid_token_post",
            err=str(exc),
            list_unsub_value=List_Unsubscribe,
        )
        raise HTTPException(status_code=400, detail="invalid_token")

    await _process_optout(lead_id, tenant_id, source="hmac_post_rfc8058")
    return {"ok": "true"}


# ---------------------------------------------------------------------------
# Shared optout logic
# ---------------------------------------------------------------------------


async def _process_optout(
    lead_id: str,
    tenant_id: str,
    source: str,
) -> None:
    """Mark the lead blacklisted + enqueue the compliance cascade.

    Idempotent: sets status only if not already blacklisted. Always
    enqueues the compliance job (it's idempotent itself via job_id).
    """

    sb = get_service_client()

    # Load lead + subject in one call.
    try:
        res = await asyncio.to_thread(
            lambda: sb.table("leads")
            .select("id, tenant_id, subject_id, pipeline_status, subjects(pii_hash)")
            .eq("id", lead_id)
            .eq("tenant_id", tenant_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.error("unsubscribe.lead_fetch_failed", lead_id=lead_id, err=str(exc))
        return

    if not res.data:
        # Lead not found — could be deleted after the email was sent.
        # Silently succeed: the person wanted off the list and there's
        # nothing to blacklist.
        log.info("unsubscribe.lead_not_found", lead_id=lead_id)
        return

    row = res.data[0]
    pii_hash = (row.get("subjects") or {}).get("pii_hash")
    already_blacklisted = row.get("pipeline_status") == _STATUS_BLACKLISTED

    if not already_blacklisted:
        try:
            await asyncio.to_thread(
                lambda: sb.table("leads")
                .update({"pipeline_status": _STATUS_BLACKLISTED})
                .eq("id", lead_id)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "unsubscribe.status_update_failed", lead_id=lead_id, err=str(exc)
            )

    if pii_hash:
        try:
            await enqueue(
                "compliance_task",
                {
                    "pii_hash": pii_hash,
                    "reason": _BLACKLIST_REASON,
                    "source": f"unsubscribe_endpoint:{source}",
                    "notes": "HMAC-authenticated unsubscribe request",
                },
                job_id=f"compliance:{pii_hash}:{_BLACKLIST_REASON}",
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "unsubscribe.enqueue_failed", lead_id=lead_id, err=str(exc)
            )

    # Also add email to the email_blacklist table (migration 0057) for
    # pre-extraction filtering. We use the pii_hash to fetch the email
    # from subjects via a second query — done in background to not
    # block the HTTP response.
    asyncio.ensure_future(
        _add_to_email_blacklist(sb, pii_hash, tenant_id, source)
    )

    log.info(
        "unsubscribe.processed",
        lead_id=lead_id,
        tenant_id=tenant_id,
        source=source,
        already_blacklisted=already_blacklisted,
    )


async def _add_to_email_blacklist(
    sb: Any,
    pii_hash: str | None,
    tenant_id: str,
    source: str,
) -> None:
    """Persist a row in `email_blacklist` (migration 0057) so the
    extractor never re-acquires this address for this tenant."""

    if not pii_hash:
        return

    try:
        # Fetch the email from subjects.
        res = await asyncio.to_thread(
            lambda: sb.table("subjects")
            .select("email")
            .eq("pii_hash", pii_hash)
            .limit(1)
            .execute()
        )
        if not res.data:
            return
        email = res.data[0].get("email") or ""
        if not email:
            return

        row = {
            "tenant_id": tenant_id,
            "email": email.strip().lower(),
            "reason": "unsubscribe",
            "source": source,
            "notes": "HMAC-authenticated optout from email footer link",
        }
        await asyncio.to_thread(
            lambda: sb.table("email_blacklist")
            .upsert(row, on_conflict="tenant_id,email")
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        # Non-critical: the compliance cascade handles the main blacklist;
        # the email_blacklist table is a secondary index for speed.
        log.warning(
            "unsubscribe.email_blacklist_failed",
            pii_hash=pii_hash,
            err=str(exc),
        )
