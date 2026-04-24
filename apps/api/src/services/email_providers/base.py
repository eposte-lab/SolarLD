"""EmailProvider abstract base class.

A provider encapsulates "how do we put an email on the wire" for a
single ``tenant_inboxes`` row. The row carries everything the provider
needs — from_address, reply_to, plus any provider-specific columns like
``oauth_refresh_token_encrypted``.

Design rules:
    * **Stateless**: providers don't cache inbox rows. The OutreachAgent
      passes the row on each ``send()`` call. This lets us hot-rotate
      an OAuth token (cron) without invalidating long-lived provider
      objects.
    * **Synchronous contract**: the ``send()`` coroutine either returns
      a ``SendResult`` (message id + a few audit hints) or raises
      ``ProviderError``. No ambiguous "queued, maybe sent" return.
    * **Error taxonomy is shared** (see ``ProviderError``). Callers read
      ``kind`` to decide whether to auto-pause the inbox, pause the
      domain, or just retry the next one.

Reusing the ``SendEmailInput`` dataclass from ``resend_service`` keeps
the OutreachAgent callsite untouched — it already constructs one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from ..resend_service import SendEmailInput

# Re-export so callers can ``from ..email_providers.base import SendEmailInput``
# without pulling resend_service into their namespace.
__all__ = [
    "SendEmailInput",
    "SendResult",
    "ProviderError",
    "EmailProvider",
    "ProviderName",
]


ProviderName = Literal["resend", "gmail_oauth", "m365_oauth", "smtp"]

# Error kinds that map onto InboxSelector's pause logic.
#
#   rate_limited   → pause inbox 2h  (like Resend 429)
#   server_error   → pause inbox 4h  (like Resend 5xx)
#   auth_failed    → OAuth refresh died; disable inbox, nudge user to
#                    re-authorize from dashboard
#   permanent      → bad recipient / suppression hit; don't pause inbox
#                    but mark the send failed
#   transport      → network blip / timeout; caller retries next inbox
ErrorKind = Literal[
    "rate_limited",
    "server_error",
    "auth_failed",
    "permanent",
    "transport",
]


@dataclass(slots=True)
class SendResult:
    """Normalised success payload.

    ``message_id`` matches whatever ``outreach_sends.email_message_id``
    stores. For Resend it's the API response id; for Gmail it's the
    ``Message-Id`` header we generated client-side (so the tracking
    webhook can correlate).
    """

    message_id: str
    provider: ProviderName
    # Optional provider-native id (e.g. Gmail thread id). Not used for
    # correlation — just kept on the send row for debugging.
    provider_ref: str | None = None
    # Free-form provider response metadata (returned to caller for
    # logging / analytics; never exposed to the tenant).
    meta: dict[str, Any] = field(default_factory=dict)


class ProviderError(Exception):
    """Raised by any EmailProvider.send() on failure.

    ``kind`` is the normalised taxonomy; ``status_code`` is the raw
    HTTP/API code when meaningful (0 otherwise). ``retryable`` tells the
    OutreachAgent whether to try the next inbox in the round-robin or
    give up on this campaign record.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: ErrorKind,
        status_code: int = 0,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.retryable = retryable


class EmailProvider(ABC):
    """Contract every email transport must implement."""

    @property
    @abstractmethod
    def name(self) -> ProviderName:
        """Registry key — matches ``tenant_inboxes.provider``."""
        raise NotImplementedError

    @abstractmethod
    async def send(
        self,
        data: SendEmailInput,
        *,
        inbox: dict[str, Any],
    ) -> SendResult:
        """Deliver ``data`` to the recipients on behalf of ``inbox``.

        The ``inbox`` dict is the raw ``tenant_inboxes`` row (dict-like);
        providers read whichever columns they need (e.g. Gmail reads
        ``oauth_refresh_token_encrypted``; Resend ignores it).

        Must raise ``ProviderError`` on failure. Retries inside a
        provider (e.g. refresh-token bounce then retry) are OK; cross-
        inbox failover is the caller's job.
        """
        raise NotImplementedError

    async def close(self) -> None:
        """Optional hook for providers that hold resources (httpx clients,
        token caches). Default is a no-op."""
        return None
