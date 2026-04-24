"""Tenant inboxes — CRUD for multi-inbox email sending.

Each inbox represents a distinct ``From`` address on the tenant's
verified domain.  The InboxSelector in ``inbox_service.py`` picks the
best available inbox for every outreach send using round-robin + daily
cap enforcement.

Endpoints
---------
GET  /v1/inboxes              List tenant inboxes + live usage stats
POST /v1/inboxes              Create a new inbox
PATCH /v1/inboxes/{id}        Update inbox fields
POST /v1/inboxes/{id}/unpause Manually clear the auto-pause flag
DELETE /v1/inboxes/{id}       Hard delete (or soft-deactivate)
GET  /v1/inboxes/quota        Live capacity summary across all inboxes
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, EmailStr, Field

from ..core.config import settings
from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client
from ..services import inbox_service
from ..services.encryption_service import encrypt, is_configured as enc_configured

router = APIRouter()
log = get_logger(__name__)

_SELECT_FIELDS = (
    "id, tenant_id, email, display_name, reply_to_email, "
    "signature_html, daily_cap, paused_until, pause_reason, "
    "sent_date, total_sent_today, last_sent_at, active, "
    "provider, oauth_account_email, oauth_connected_at, "
    "oauth_last_error, oauth_last_error_at, "
    "created_at, updated_at"
)

# Google OAuth constants — Gmail send scope only (least privilege).
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "openid",
    "email",
]
_OAUTH_STATE_TTL_SECONDS = 600  # 10 min to complete consent


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class InboxCreate(BaseModel):
    email: EmailStr
    display_name: str = Field(default="", max_length=120)
    reply_to_email: EmailStr | None = None
    signature_html: str | None = Field(default=None, max_length=8000)
    daily_cap: int = Field(default=50, ge=1, le=2000)


class InboxUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    reply_to_email: EmailStr | None = None
    signature_html: str | None = Field(default=None, max_length=8000)
    daily_cap: int | None = Field(default=None, ge=1, le=2000)
    active: bool | None = None


# ---------------------------------------------------------------------------
# GET /v1/inboxes
# ---------------------------------------------------------------------------


@router.get("")
async def list_inboxes(ctx: CurrentUser) -> dict[str, Any]:
    """List all inboxes for the current tenant.

    Returns live stats (``total_sent_today``, ``paused_until``) so the
    dashboard can show capacity at-a-glance without a separate quota call.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    try:
        res = (
            sb.table("tenant_inboxes")
            .select(_SELECT_FIELDS)
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=False)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("inboxes.list_failed", tenant_id=tenant_id, err=str(exc))
        return {"inboxes": [], "total": 0}

    rows = res.data or []
    today = datetime.now(timezone.utc).date().isoformat()

    # Annotate each inbox with derived fields for the UI.
    for row in rows:
        # If sent_date is not today, the counter is stale — show 0.
        if row.get("sent_date") != today:
            row["total_sent_today"] = 0
        now_utc = datetime.now(timezone.utc).isoformat()
        paused_until = row.get("paused_until")
        row["is_paused"] = bool(paused_until and paused_until > now_utc)
        remaining = max(0, int(row.get("daily_cap", 50)) - int(row.get("total_sent_today", 0)))
        row["remaining_today"] = remaining

    return {"inboxes": rows, "total": len(rows)}


# ---------------------------------------------------------------------------
# GET /v1/inboxes/quota
# ---------------------------------------------------------------------------


@router.get("/quota")
async def get_inbox_quota(ctx: CurrentUser) -> dict[str, Any]:
    """Aggregate capacity summary: total cap, used, remaining, paused count.

    Used by the settings overview card to show "X / Y sends today".
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    today = datetime.now(timezone.utc).date().isoformat()
    now_utc = datetime.now(timezone.utc).isoformat()

    try:
        res = (
            sb.table("tenant_inboxes")
            .select("daily_cap, sent_date, total_sent_today, paused_until, active")
            .eq("tenant_id", tenant_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("inboxes.quota_failed", tenant_id=tenant_id, err=str(exc))
        return {
            "total_daily_cap": 0,
            "sent_today": 0,
            "remaining_today": 0,
            "active_inboxes": 0,
            "paused_inboxes": 0,
        }

    rows = res.data or []
    active = [r for r in rows if r.get("active")]
    paused = [
        r for r in active
        if r.get("paused_until") and r["paused_until"] > now_utc
    ]

    total_cap = sum(int(r.get("daily_cap", 50)) for r in active)
    sent = sum(
        int(r.get("total_sent_today", 0))
        for r in active
        if r.get("sent_date") == today
    )

    return {
        "total_daily_cap": total_cap,
        "sent_today": sent,
        "remaining_today": max(0, total_cap - sent),
        "active_inboxes": len(active),
        "paused_inboxes": len(paused),
    }


# ---------------------------------------------------------------------------
# POST /v1/inboxes
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_inbox(body: InboxCreate, ctx: CurrentUser) -> dict[str, Any]:
    """Create a new sending inbox.

    The email must be on the tenant's verified domain (enforced by the
    domain check in the email_from_domain settings). Duplicate emails
    within the same tenant return 409 Conflict.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    insert_data: dict[str, Any] = {
        "tenant_id": tenant_id,
        "email": str(body.email).lower().strip(),
        "display_name": body.display_name.strip(),
        "daily_cap": body.daily_cap,
        "active": True,
    }
    if body.reply_to_email:
        insert_data["reply_to_email"] = str(body.reply_to_email).lower().strip()
    if body.signature_html:
        insert_data["signature_html"] = body.signature_html

    try:
        res = sb.table("tenant_inboxes").insert(insert_data).execute()
    except Exception as exc:  # noqa: BLE001
        err_str = str(exc)
        if "unique" in err_str.lower() or "duplicate" in err_str.lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Inbox with email {body.email} already exists for this tenant",
            ) from exc
        log.warning("inboxes.create_failed", tenant_id=tenant_id, err=err_str)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create inbox",
        ) from exc

    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Insert returned no data",
        )

    log.info("inboxes.created", tenant_id=tenant_id, inbox_id=res.data[0]["id"])
    return res.data[0]


# ---------------------------------------------------------------------------
# PATCH /v1/inboxes/{inbox_id}
# ---------------------------------------------------------------------------


@router.patch("/{inbox_id}")
async def update_inbox(
    inbox_id: str,
    body: InboxUpdate,
    ctx: CurrentUser,
) -> dict[str, Any]:
    """Update an inbox's display name, cap, signature, or active state."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    update_data: dict[str, Any] = {
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    if body.display_name is not None:
        update_data["display_name"] = body.display_name.strip()
    if body.reply_to_email is not None:
        update_data["reply_to_email"] = str(body.reply_to_email).lower().strip()
    if body.signature_html is not None:
        update_data["signature_html"] = body.signature_html
    if body.daily_cap is not None:
        update_data["daily_cap"] = body.daily_cap
    if body.active is not None:
        update_data["active"] = body.active

    if len(update_data) == 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No fields to update",
        )

    try:
        res = (
            sb.table("tenant_inboxes")
            .update(update_data)
            .eq("id", inbox_id)
            .eq("tenant_id", tenant_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("inboxes.update_failed", inbox_id=inbox_id, err=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Update failed",
        ) from exc

    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inbox not found or not owned by this tenant",
        )

    return res.data[0]


# ---------------------------------------------------------------------------
# POST /v1/inboxes/{inbox_id}/unpause
# ---------------------------------------------------------------------------


@router.post("/{inbox_id}/unpause")
async def unpause_inbox(inbox_id: str, ctx: CurrentUser) -> dict[str, Any]:
    """Manually clear the auto-pause flag on an inbox.

    Useful when the operator has resolved the issue (e.g. confirmed the
    Resend sender is healthy again) and wants to re-activate immediately
    without waiting for the pause window to expire.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    updated = await inbox_service.unpause_inbox(sb, inbox_id, tenant_id=tenant_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inbox not found or not owned by this tenant",
        )
    return {"ok": True, "inbox_id": inbox_id}


# ---------------------------------------------------------------------------
# DELETE /v1/inboxes/{inbox_id}
# ---------------------------------------------------------------------------


@router.delete("/{inbox_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_inbox(inbox_id: str, ctx: CurrentUser) -> Response:
    """Permanently delete an inbox.

    Historical ``campaigns`` rows referencing this inbox will have their
    ``inbox_id`` set to NULL (ON DELETE SET NULL in the migration), so
    deliverability analytics are preserved for historical sends.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    try:
        res = (
            sb.table("tenant_inboxes")
            .delete()
            .eq("id", inbox_id)
            .eq("tenant_id", tenant_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("inboxes.delete_failed", inbox_id=inbox_id, err=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Delete failed",
        ) from exc

    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inbox not found or not owned by this tenant",
        )
    log.info("inboxes.deleted", tenant_id=tenant_id, inbox_id=inbox_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Gmail OAuth2 — connect a tenant inbox to a real Google Workspace account
# ---------------------------------------------------------------------------
#
# Flow
# ----
# 1. Dashboard calls ``POST /v1/inboxes/{id}/oauth/gmail/authorize`` →
#    returns a signed consent URL. User is redirected there.
# 2. Google sends user back to ``GET /v1/inboxes/{id}/oauth/gmail/callback``
#    with ``?code=...&state=<jwt>``. We verify the state, exchange the code
#    for a refresh+access token, encrypt, persist, flip the inbox row to
#    ``provider='gmail_oauth'``.
# 3. Callback redirects user back to the dashboard settings page with
#    a status flag so the UI can show "Gmail connected" or the error.
#
# The ``state`` param is a short-TTL JWT (10 min) signed with our
# ``jwt_secret``. It carries tenant_id + inbox_id + a random nonce. This
# is both CSRF protection and the binding between the original SSR user
# and the callback (the callback is unauthenticated on purpose — Google
# hits it directly in the browser with no tenant cookie).


def _sign_oauth_state(tenant_id: str, inbox_id: str) -> str:
    payload = {
        "tid": tenant_id,
        "iid": inbox_id,
        "nonce": secrets.token_urlsafe(16),
        "iat": int(time.time()),
        "exp": int(time.time()) + _OAUTH_STATE_TTL_SECONDS,
        "purpose": "gmail_oauth_connect",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def _verify_oauth_state(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            options={"require": ["exp", "iat"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid OAuth state: {exc}",
        ) from exc
    if payload.get("purpose") != "gmail_oauth_connect":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth state has wrong purpose",
        )
    return payload


@router.post("/{inbox_id}/oauth/gmail/authorize")
async def gmail_oauth_authorize(
    inbox_id: str, ctx: CurrentUser
) -> dict[str, Any]:
    """Return a Google OAuth consent URL for connecting this inbox.

    The dashboard opens the URL in a popup / new tab; Google redirects
    the user back to ``/oauth/gmail/callback`` once consent is granted.
    """
    tenant_id = require_tenant(ctx)

    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "Google OAuth is not configured on this API. "
                "Set GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET."
            ),
        )
    if not enc_configured():
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "APP_SECRET_KEY (Fernet) is not configured — refresh tokens "
                "cannot be encrypted. Generate a key and set it in env."
            ),
        )

    # Verify the inbox exists and belongs to the tenant before giving out
    # a signed state for it.
    sb = get_service_client()
    res = (
        sb.table("tenant_inboxes")
        .select("id, email")
        .eq("id", inbox_id)
        .eq("tenant_id", tenant_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inbox not found or not owned by this tenant",
        )

    state = _sign_oauth_state(tenant_id, inbox_id)
    redirect_uri = f"{settings.api_base_url.rstrip('/')}/v1/inboxes/oauth/gmail/callback"
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(_GOOGLE_SCOPES),
        "access_type": "offline",         # → returns refresh_token
        "prompt": "consent",              # force re-consent so we always get refresh_token
        "include_granted_scopes": "true",
        "state": state,
        # Nudge Google to prefill the right email in the account picker.
        "login_hint": res.data[0].get("email") or "",
    }
    url = f"{_GOOGLE_AUTH_URL}?{urlencode(params)}"
    return {"authorize_url": url, "inbox_id": inbox_id}


@router.get("/oauth/gmail/callback")
async def gmail_oauth_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    """Google redirects here after consent. Unauthenticated on purpose.

    Security rests entirely on the signed ``state`` JWT: it binds this
    callback to the inbox the user started the flow for.
    """
    dashboard_base = settings.next_public_dashboard_url.rstrip("/")
    settings_page = f"{dashboard_base}/settings/inboxes"

    if error:
        return RedirectResponse(
            f"{settings_page}?oauth_error={error}",
            status_code=status.HTTP_302_FOUND,
        )
    if not code or not state:
        return RedirectResponse(
            f"{settings_page}?oauth_error=missing_code_or_state",
            status_code=status.HTTP_302_FOUND,
        )

    try:
        state_payload = _verify_oauth_state(state)
    except HTTPException as exc:
        return RedirectResponse(
            f"{settings_page}?oauth_error=invalid_state",
            status_code=status.HTTP_302_FOUND,
        )

    tenant_id = state_payload["tid"]
    inbox_id = state_payload["iid"]
    redirect_uri = f"{settings.api_base_url.rstrip('/')}/v1/inboxes/oauth/gmail/callback"

    # Exchange authorization code → refresh + access tokens.
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            _GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if resp.status_code >= 400:
        log.warning(
            "gmail_oauth.code_exchange_failed",
            inbox_id=inbox_id,
            status=resp.status_code,
            body=resp.text[:300],
        )
        return RedirectResponse(
            f"{settings_page}?oauth_error=token_exchange_failed",
            status_code=status.HTTP_302_FOUND,
        )

    payload = resp.json()
    refresh_token = payload.get("refresh_token") or ""
    access_token = payload.get("access_token") or ""
    expires_in = int(payload.get("expires_in") or 3600)
    granted_scope = payload.get("scope") or ""
    id_token = payload.get("id_token") or ""

    if not refresh_token:
        # Can happen if the user already granted consent and Google didn't
        # re-issue a refresh token. We forced ``prompt=consent`` so this
        # should be rare — surface a clear error.
        log.warning("gmail_oauth.missing_refresh_token", inbox_id=inbox_id)
        return RedirectResponse(
            f"{settings_page}?oauth_error=missing_refresh_token",
            status_code=status.HTTP_302_FOUND,
        )

    # Parse id_token (unsigned read of the email claim — Google already
    # authenticated it by issuing the code).
    oauth_account_email = ""
    if id_token:
        try:
            claims = jwt.decode(
                id_token, options={"verify_signature": False}
            )
            oauth_account_email = str(claims.get("email") or "")
        except Exception:  # noqa: BLE001
            pass

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=expires_in)

    sb = get_service_client()
    try:
        update_payload: dict[str, Any] = {
            "provider": "gmail_oauth",
            "oauth_refresh_token_encrypted": encrypt(refresh_token),
            "oauth_access_token_encrypted": encrypt(access_token) if access_token else None,
            "oauth_token_expires_at": expires_at.isoformat(),
            "oauth_scope": granted_scope,
            "oauth_account_email": oauth_account_email or None,
            "oauth_connected_at": now.isoformat(),
            "oauth_last_error": None,
            "oauth_last_error_at": None,
            "active": True,
            "updated_at": now.isoformat(),
        }
        res = (
            sb.table("tenant_inboxes")
            .update(update_payload)
            .eq("id", inbox_id)
            .eq("tenant_id", tenant_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "gmail_oauth.persist_failed",
            inbox_id=inbox_id,
            err=str(exc),
        )
        return RedirectResponse(
            f"{settings_page}?oauth_error=persist_failed",
            status_code=status.HTTP_302_FOUND,
        )

    if not res.data:
        return RedirectResponse(
            f"{settings_page}?oauth_error=inbox_not_found",
            status_code=status.HTTP_302_FOUND,
        )

    log.info(
        "gmail_oauth.connected",
        tenant_id=tenant_id,
        inbox_id=inbox_id,
        google_account=oauth_account_email,
    )
    return RedirectResponse(
        f"{settings_page}?oauth_connected=gmail&inbox_id={inbox_id}",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/{inbox_id}/oauth/disconnect")
async def oauth_disconnect(
    inbox_id: str, ctx: CurrentUser
) -> dict[str, Any]:
    """Detach the OAuth connection from an inbox.

    Resets the provider to ``resend`` (the default) and clears all token
    fields. Does not revoke the grant on Google's side — the user should
    also remove our app from https://myaccount.google.com/permissions if
    they want a hard disconnect.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    try:
        res = (
            sb.table("tenant_inboxes")
            .update(
                {
                    "provider": "resend",
                    "oauth_refresh_token_encrypted": None,
                    "oauth_access_token_encrypted": None,
                    "oauth_token_expires_at": None,
                    "oauth_scope": None,
                    "oauth_account_email": None,
                    "oauth_connected_at": None,
                    "oauth_last_error": None,
                    "oauth_last_error_at": None,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("id", inbox_id)
            .eq("tenant_id", tenant_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "inboxes.oauth_disconnect_failed", inbox_id=inbox_id, err=str(exc)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Disconnect failed",
        ) from exc

    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inbox not found or not owned by this tenant",
        )
    log.info("inboxes.oauth_disconnected", inbox_id=inbox_id)
    return {"ok": True, "inbox_id": inbox_id, "provider": "resend"}
