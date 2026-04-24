"""Resend HTTP API provider.

Thin wrapper around the existing ``services.resend_service.send_email``
coroutine so that the OutreachAgent can dispatch via the unified
``EmailProvider`` interface. Zero behaviour change for tenants that
don't switch to Gmail OAuth.

We intentionally don't re-implement retries here — tenacity already
wraps ``send_email``. The job of this class is pure translation:

    ``ResendError(status_code=429)`` → ``ProviderError(kind='rate_limited')``
    ``ResendError(status_code=5xx)`` → ``ProviderError(kind='server_error')``
    ``ResendError(status_code=4xx)`` → ``ProviderError(kind='permanent')``
"""

from __future__ import annotations

from typing import Any

from ...core.logging import get_logger
from ..resend_service import ResendError, send_email
from .base import EmailProvider, ProviderError, SendEmailInput, SendResult

log = get_logger(__name__)


class ResendProvider(EmailProvider):
    """Sends via the shared Resend HTTP API."""

    @property
    def name(self) -> str:
        return "resend"

    async def send(
        self,
        data: SendEmailInput,
        *,
        inbox: dict[str, Any],
    ) -> SendResult:
        try:
            result = await send_email(data)
        except ResendError as exc:
            raise _translate(exc) from exc

        return SendResult(
            message_id=result.id,
            provider="resend",
            provider_ref=result.id,
            meta={"inbox_id": inbox.get("id")},
        )


def _translate(exc: ResendError) -> ProviderError:
    """Map a ResendError onto the shared ProviderError taxonomy."""
    code = getattr(exc, "status_code", 0) or 0
    if code == 429:
        return ProviderError(
            str(exc), kind="rate_limited", status_code=code, retryable=True
        )
    if 500 <= code < 600:
        return ProviderError(
            str(exc), kind="server_error", status_code=code, retryable=True
        )
    if 400 <= code < 500:
        return ProviderError(
            str(exc), kind="permanent", status_code=code, retryable=False
        )
    # Non-HTTP failure (JSON parse, network): treat as transport.
    return ProviderError(
        str(exc), kind="transport", status_code=code, retryable=True
    )
