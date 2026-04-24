"""Gmail API provider — cold outreach over Google Workspace OAuth2.

Why not stick with Resend? For cold B2B outreach, Gmail-to-Gmail
delivery is the single biggest deliverability win you can get: Gmail
inboxes have their own reputation, independent of any SaaS shared IP
pool. Per-inbox warm-up + human-looking sender = inbox placement.

Flow
----
1. OAuth consent already completed via ``routes/inboxes.py``:
   - Refresh token stored (Fernet-encrypted) in
     ``tenant_inboxes.oauth_refresh_token_encrypted``.
2. On each ``send(...)``:
   - Decrypt refresh token.
   - Check ``oauth_token_expires_at``: if <5 min away, fetch a new
     access token from Google's token endpoint.
   - Build a RFC 822 MIME message (headers + html + text alt).
   - POST to ``gmail.googleapis.com/gmail/v1/users/me/messages/send``
     with the access token as Bearer.
3. Persist any new access token back to the inbox row (best-effort —
   if the write fails we just refresh again next send).

References
----------
* OAuth2 refresh: https://developers.google.com/identity/protocols/oauth2/web-server#offline
* Gmail send: https://developers.google.com/gmail/api/reference/rest/v1/users.messages/send
* MIME format: RFC 2822 / RFC 5322.
"""

from __future__ import annotations

import base64
import email.utils
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

import httpx

from ...core.config import settings
from ...core.logging import get_logger
from ..encryption_service import EncryptionError, decrypt, encrypt
from .base import EmailProvider, ProviderError, SendEmailInput, SendResult

log = get_logger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = (
    "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
)
# Refresh an access token 5 min before expiry so parallel workers don't
# race at the cliff.
REFRESH_BUFFER_SECONDS = 300


class GmailProvider(EmailProvider):
    """Per-inbox Gmail API send over OAuth2."""

    def __init__(self, *, sb: Any | None = None) -> None:
        # ``sb`` is the Supabase service-role client used to persist the
        # refreshed access token back on the inbox row. Injected to
        # avoid a circular import with ``core.supabase_client``.
        self._sb = sb

    @property
    def name(self) -> str:
        return "gmail_oauth"

    # ------------------------------------------------------------------
    # Public contract
    # ------------------------------------------------------------------

    async def send(
        self,
        data: SendEmailInput,
        *,
        inbox: dict[str, Any],
    ) -> SendResult:
        access_token = await self._ensure_access_token(inbox)
        raw_b64 = _build_mime_raw(data)

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                GMAIL_SEND_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"raw": raw_b64},
            )

        if resp.status_code == 401:
            # Access token was rejected even though we thought it was
            # fresh — refresh once and retry, otherwise bubble as
            # auth_failed (user needs to re-authorize from UI).
            log.warning(
                "gmail.access_token_rejected",
                inbox_id=inbox.get("id"),
                body=resp.text[:200],
            )
            access_token = await self._refresh_access_token(inbox, force=True)
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    GMAIL_SEND_URL,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                    json={"raw": raw_b64},
                )
            if resp.status_code == 401:
                raise ProviderError(
                    "Gmail rejected refreshed token — re-authorize required.",
                    kind="auth_failed",
                    status_code=401,
                    retryable=False,
                )

        if resp.status_code == 429:
            raise ProviderError(
                f"Gmail 429: {resp.text[:200]}",
                kind="rate_limited",
                status_code=429,
                retryable=True,
            )
        if resp.status_code >= 500:
            raise ProviderError(
                f"Gmail {resp.status_code}: {resp.text[:200]}",
                kind="server_error",
                status_code=resp.status_code,
                retryable=True,
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"Gmail {resp.status_code}: {resp.text[:200]}",
                kind="permanent",
                status_code=resp.status_code,
                retryable=False,
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise ProviderError(
                f"Gmail non-json response: {exc}",
                kind="transport",
                retryable=True,
            ) from exc

        gmail_id = str(payload.get("id") or "")
        thread_id = str(payload.get("threadId") or "")
        if not gmail_id:
            raise ProviderError(
                f"Gmail response missing id: {payload!r}",
                kind="transport",
                retryable=True,
            )

        return SendResult(
            message_id=gmail_id,
            provider="gmail_oauth",
            provider_ref=thread_id or gmail_id,
            meta={
                "inbox_id": inbox.get("id"),
                "gmail_thread_id": thread_id,
            },
        )

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    async def _ensure_access_token(self, inbox: dict[str, Any]) -> str:
        """Return a valid access token, refreshing if <5 min from expiry."""
        expires_at = inbox.get("oauth_token_expires_at")
        encrypted_access = inbox.get("oauth_access_token_encrypted")

        if encrypted_access and expires_at:
            # ``expires_at`` may arrive as string (PostgREST) or datetime.
            expiry_dt = _parse_ts(expires_at)
            if (
                expiry_dt
                and expiry_dt
                > datetime.now(timezone.utc) + timedelta(seconds=REFRESH_BUFFER_SECONDS)
            ):
                try:
                    return decrypt(encrypted_access)
                except EncryptionError as exc:
                    # Corrupt/tampered access token — fall through to refresh.
                    log.warning(
                        "gmail.access_token_decrypt_failed",
                        inbox_id=inbox.get("id"),
                        err=str(exc),
                    )

        return await self._refresh_access_token(inbox, force=False)

    async def _refresh_access_token(
        self, inbox: dict[str, Any], *, force: bool
    ) -> str:
        """Exchange the refresh token for a new access token."""
        encrypted_refresh = inbox.get("oauth_refresh_token_encrypted")
        if not encrypted_refresh:
            raise ProviderError(
                "Gmail inbox has no refresh token — OAuth never completed.",
                kind="auth_failed",
                retryable=False,
            )
        try:
            refresh_token = decrypt(encrypted_refresh)
        except EncryptionError as exc:
            raise ProviderError(
                f"Cannot decrypt refresh token: {exc}",
                kind="auth_failed",
                retryable=False,
            ) from exc

        if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
            raise ProviderError(
                "GOOGLE_OAUTH_CLIENT_ID/SECRET not configured on API.",
                kind="auth_failed",
                retryable=False,
            )

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": settings.google_oauth_client_id,
                    "client_secret": settings.google_oauth_client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        if resp.status_code == 400 or resp.status_code == 401:
            # Google returns 400/invalid_grant when the refresh token is
            # revoked (user un-granted consent, password changed, etc.).
            # This is a terminal state — mark the inbox and ask user to
            # re-authorize.
            await self._record_auth_error(
                inbox, f"refresh_rejected_{resp.status_code}: {resp.text[:200]}"
            )
            raise ProviderError(
                f"Google refresh rejected ({resp.status_code}): {resp.text[:200]}",
                kind="auth_failed",
                status_code=resp.status_code,
                retryable=False,
            )
        if resp.status_code >= 500:
            raise ProviderError(
                f"Google token endpoint 5xx: {resp.text[:200]}",
                kind="server_error",
                status_code=resp.status_code,
                retryable=True,
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"Google token endpoint {resp.status_code}: {resp.text[:200]}",
                kind="transport",
                status_code=resp.status_code,
                retryable=True,
            )

        payload = resp.json()
        access_token = payload.get("access_token") or ""
        expires_in = int(payload.get("expires_in") or 3600)
        if not access_token:
            raise ProviderError(
                f"Google response missing access_token: {payload!r}",
                kind="transport",
                retryable=True,
            )

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        await self._persist_access_token(inbox, access_token, expires_at)
        return access_token

    async def _persist_access_token(
        self,
        inbox: dict[str, Any],
        access_token: str,
        expires_at: datetime,
    ) -> None:
        """Best-effort cache the new access token on the inbox row."""
        if self._sb is None:
            return
        try:
            (
                self._sb.table("tenant_inboxes")
                .update(
                    {
                        "oauth_access_token_encrypted": encrypt(access_token),
                        "oauth_token_expires_at": expires_at.isoformat(),
                        "oauth_last_error": None,
                        "oauth_last_error_at": None,
                    }
                )
                .eq("id", inbox["id"])
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            # Cache-only — if the write fails we'll just refresh again
            # on the next send. Don't fail the send for this.
            log.warning(
                "gmail.persist_token_failed",
                inbox_id=inbox.get("id"),
                err=str(exc),
            )

    async def _record_auth_error(
        self, inbox: dict[str, Any], reason: str
    ) -> None:
        """Mark the inbox inactive + surface the error for the dashboard."""
        if self._sb is None:
            return
        try:
            (
                self._sb.table("tenant_inboxes")
                .update(
                    {
                        "active": False,
                        "oauth_last_error": reason[:500],
                        "oauth_last_error_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                .eq("id", inbox["id"])
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "gmail.record_auth_error_failed",
                inbox_id=inbox.get("id"),
                err=str(exc),
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_mime_raw(data: SendEmailInput) -> str:
    """Build an RFC 822 message and return it as urlsafe-base64.

    Gmail's ``users.messages.send`` expects the whole MIME wire format,
    base64url-encoded. We use stdlib ``EmailMessage`` so we never have
    to worry about header encoding, content-type quoting, multipart
    boundaries, etc.
    """
    msg = EmailMessage()
    msg["From"] = data.from_address
    msg["To"] = ", ".join(data.to)
    msg["Subject"] = data.subject
    msg["Message-ID"] = email.utils.make_msgid()
    msg["Date"] = email.utils.formatdate(localtime=True)

    if data.reply_to:
        msg["Reply-To"] = data.reply_to

    if data.headers:
        for k, v in data.headers.items():
            # Don't let headers from the caller clobber the structural
            # ones above — skip any duplicate key.
            if k.lower() in {"from", "to", "subject", "message-id", "date"}:
                continue
            msg[k] = v

    # Body: always set text, then add html as alternative when present.
    text_body = data.text or _html_to_text_fallback(data.html)
    msg.set_content(text_body, subtype="plain", charset="utf-8")
    if data.html:
        msg.add_alternative(data.html, subtype="html")

    raw_bytes = msg.as_bytes()
    return base64.urlsafe_b64encode(raw_bytes).decode("ascii")


def _html_to_text_fallback(html: str) -> str:
    """Very cheap HTML→plain fallback so the text/plain part is never empty.

    We only care about not triggering spam heuristics that flag
    HTML-only emails; we're not building a perfect markdown renderer.
    """
    import re

    if not html:
        return ""
    stripped = re.sub(r"<[^>]+>", "", html)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return stripped


def _parse_ts(value: Any) -> datetime | None:
    """Parse a PostgREST timestamptz into aware datetime. None on failure."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            # PostgREST emits e.g. "2026-04-24T10:15:32+00:00"
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
