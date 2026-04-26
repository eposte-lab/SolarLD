"""HMAC-signed unsubscribe token service (Task 12).

Why HMAC instead of the existing slug-based optout
---------------------------------------------------
The legacy `/optout/{slug}` URL uses the lead's `public_slug` as the
only token — a 36-char UUID. That's opaque but has two weaknesses:

  1. **No expiry semantics**. A slug never rotates; once a cold email
     with that URL lands in a spam folder it's a permanent, callable
     optout link with no revocation path.
  2. **No domain alignment**. RFC 8058 one-click POST requires the
     URL domain to match the sending domain's alignment. The lead
     portal lives on `portal.solarld.app` — a shared host. Gmail
     penalises mismatched domains on `List-Unsubscribe` headers.
  3. **Requires DB lookup by slug before any auth can happen**. A
     botnet scanning random slugs does one DB read per attempt.

The HMAC approach:
  * Token = `HMAC-SHA256(key=APP_SECRET_KEY, msg="{lead_id}:{tenant_id}:{email_hash}")`,
    encoded as base64url, prepended with the lead_id + ":" for routing.
  * URL = `{API_BASE}/v1/unsubscribe?t={token}` — served by the FastAPI
    app, NOT the lead portal. The API can use a per-tenant tracking
    host so domain alignment is preserved.
  * The token embeds enough data for a *cryptographic* pre-check before
    any DB read: if the HMAC doesn't verify, we 400 instantly.
  * No expiry on unsubscribe tokens — an email from 2025 must still
    let the user opt out in 2030. Instead we embed a `v1` prefix for
    future algorithm rotation.

Token format
-----------
    v1.{base64url(lead_id + ":" + tenant_id + ":" + email_hash)}:{hmac}

  * `lead_id`, `tenant_id`, `email_hash` are URL-safe by construction
    (UUIDs + hex) so the base64url layer is really just future-proofing.
  * `hmac` = hex-encoded HMAC-SHA256(APP_SECRET_KEY, message=payload).

The endpoint decodes the lead_id, then verifies the HMAC before doing
any DB read. This limits the server-side attack surface to the cost of
one HMAC verify per inbound request — same as a JWT validation.

Backward compatibility
----------------------
The legacy `POST /v1/public/lead/{slug}/optout` endpoint is kept intact
for emails already in the wild. New emails will use the HMAC URL.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as _hmac

import structlog

log = structlog.get_logger(__name__)

TOKEN_VERSION = "v1"
_SEPARATOR = ":"


def _secret() -> bytes:
    """Return the raw HMAC key bytes from config.

    Imported lazily to avoid circular-import at module load time.
    Raises `ValueError` if `APP_SECRET_KEY` is empty so callers get a
    clear error instead of silently signing with an empty key.
    """
    from ..core.config import settings

    key = (settings.app_secret_key or "").strip()
    if not key:
        raise ValueError(
            "APP_SECRET_KEY must be set to generate secure unsubscribe tokens."
        )
    return key.encode("utf-8")


# ---------------------------------------------------------------------------
# Token generation
# ---------------------------------------------------------------------------


def generate_token(
    lead_id: str,
    tenant_id: str,
    email_hash: str,
) -> str:
    """Sign and encode an unsubscribe token.

    Parameters
    ----------
    lead_id:    UUID of the lead row.
    tenant_id:  UUID of the tenant.
    email_hash: sha256(lowercase(email)), hex — not the raw email. We
                never include PII in the URL.

    Returns
    -------
    A URL-safe token string, e.g.:
        ``v1.bGVhZC1pZC10ZW5hbnQtaWQtZW1haWwtaGFzaA.a1b2c3d4...``

    Raises `ValueError` when `APP_SECRET_KEY` is not configured.
    """

    payload = _SEPARATOR.join([lead_id, tenant_id, email_hash])
    payload_b64 = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
    sig = _sign(payload)
    return f"{TOKEN_VERSION}.{payload_b64}.{sig}"


def build_unsubscribe_url(
    lead_id: str,
    tenant_id: str,
    email_hash: str,
    *,
    api_base: str = "",
    tracking_host: str | None = None,
) -> str:
    """Return the full unsubscribe URL for embedding in List-Unsubscribe headers.

    Domain selection:
      * `tracking_host` set  → `https://{tracking_host}/v1/unsubscribe?t=...`
        (domain-aligned with the sending domain, preferred by Gmail)
      * `tracking_host` None → `{api_base}/v1/unsubscribe?t=...`
        (falls back to the shared API base URL)
    """

    token = generate_token(lead_id, tenant_id, email_hash)
    if tracking_host:
        base = f"https://{tracking_host.strip('/')}"
    else:
        base = (api_base or "").rstrip("/")
    return f"{base}/v1/unsubscribe?t={token}"


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------


class InvalidUnsubscribeToken(ValueError):
    """Raised by `verify_token` when the token is invalid or tampered."""


def verify_token(token: str) -> tuple[str, str, str]:
    """Verify the HMAC and decode the payload.

    Returns `(lead_id, tenant_id, email_hash)` on success.
    Raises `InvalidUnsubscribeToken` on any failure (wrong version,
    malformed base64, bad HMAC, missing fields).

    The caller is responsible for looking up the lead by `lead_id`
    AFTER this function returns — we only do crypto here, not DB reads.
    """

    try:
        parts = token.split(".", 2)
        if len(parts) != 3:
            raise InvalidUnsubscribeToken("wrong segment count")
        version, payload_b64, sig = parts

        if version != TOKEN_VERSION:
            raise InvalidUnsubscribeToken(f"unsupported token version: {version!r}")

        payload = base64.urlsafe_b64decode(
            payload_b64 + "=="  # pad to multiple of 4 for urlsafe decode
        ).decode("utf-8")

        expected_sig = _sign(payload)
        if not _hmac.compare_digest(expected_sig, sig):
            raise InvalidUnsubscribeToken("HMAC mismatch")

        fields = payload.split(_SEPARATOR)
        if len(fields) != 3:
            raise InvalidUnsubscribeToken("payload has wrong field count")

        lead_id, tenant_id, email_hash = fields
        if not lead_id or not tenant_id or not email_hash:
            raise InvalidUnsubscribeToken("empty field in payload")

        return lead_id, tenant_id, email_hash

    except InvalidUnsubscribeToken:
        raise
    except Exception as exc:
        raise InvalidUnsubscribeToken(f"decode error: {exc}") from exc


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sign(payload: str) -> str:
    """Return hex-encoded HMAC-SHA256 of `payload` using `APP_SECRET_KEY`."""
    return _hmac.new(
        _secret(),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ---------------------------------------------------------------------------
# Email hash helper — used at send time to build the token
# ---------------------------------------------------------------------------


def email_to_hash(email: str) -> str:
    """SHA-256 of the lowercased email address, hex-encoded.

    Matches the format used by `email_blacklist.email_hash` (migration
    0057) so the hash is consistent across the whole codebase.
    """
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()
