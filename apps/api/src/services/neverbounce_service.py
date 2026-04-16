"""NeverBounce — real-time email validity check.

Protects sender reputation: Resend / SES / Mailwarm will suspend accounts
that cross ~2% hard-bounce rate. Running every email through NeverBounce
*before* the first send keeps us under 0.5%.

Result mapping (NeverBounce → SolarLead usage):
  - valid         → OK to send
  - accept_all    → catch-all domain, we send but mark lower confidence
  - disposable    → REJECT (10minutemail style)
  - unknown       → REJECT (fail closed to protect reputation)
  - invalid       → REJECT
  - role          → role account (info@, sales@) — we send B2B only

Docs: https://developers.neverbounce.com/v4.2/reference/single-check
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)

NEVERBOUNCE_BASE = "https://api.neverbounce.com/v4.2"
NEVERBOUNCE_COST_PER_CALL_CENTS = 1  # ~$0.008/email on starter plan


class NeverBounceError(Exception):
    pass


class VerificationResult(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    DISPOSABLE = "disposable"
    CATCHALL = "catchall"
    UNKNOWN = "unknown"

    @property
    def sendable(self) -> bool:
        """Is it safe to send to this address?"""
        return self in {VerificationResult.VALID, VerificationResult.CATCHALL}


@dataclass(slots=True)
class EmailVerification:
    email: str
    result: VerificationResult
    role_address: bool
    free_email: bool
    disposable: bool
    raw: dict[str, Any]


def _map_result(code: str) -> VerificationResult:
    """NeverBounce API codes → our enum."""
    mapping = {
        "valid": VerificationResult.VALID,
        "invalid": VerificationResult.INVALID,
        "disposable": VerificationResult.DISPOSABLE,
        "catchall": VerificationResult.CATCHALL,
        "unknown": VerificationResult.UNKNOWN,
    }
    return mapping.get(code, VerificationResult.UNKNOWN)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
async def verify_email(
    email: str,
    *,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> EmailVerification:
    """Run a single-email check against NeverBounce.

    Always returns a `EmailVerification` — on network error we return
    `UNKNOWN` rather than raising (callers must treat UNKNOWN as unsendable).
    """
    key = api_key or settings.neverbounce_api_key
    if not key:
        raise NeverBounceError("NEVERBOUNCE_API_KEY not configured")

    params = {"key": key, "email": email}

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
    try:
        resp = await client.get(f"{NEVERBOUNCE_BASE}/single/check", params=params)
    finally:
        if owns_client:
            await client.aclose()

    if resp.status_code >= 500:
        # Soft-fail: reputation > delivery. Pretend it's unknown.
        log.warning("neverbounce_upstream_error", status=resp.status_code, email=email)
        return EmailVerification(
            email=email,
            result=VerificationResult.UNKNOWN,
            role_address=False,
            free_email=False,
            disposable=False,
            raw={"error": resp.text[:200]},
        )

    body = resp.json()
    if body.get("status") != "success":
        raise NeverBounceError(body.get("message", "unknown error"))

    flags = body.get("flags") or []
    return EmailVerification(
        email=email,
        result=_map_result(body.get("result", "unknown")),
        role_address="role_account" in flags,
        free_email="free_email_host" in flags,
        disposable="disposable_email" in flags,
        raw=body,
    )
