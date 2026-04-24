"""Provider registry — map ``tenant_inboxes.provider`` → ``EmailProvider``.

Kept as a tiny factory (not a DI framework) because the whole surface
is ~3 providers. ``get_provider("resend")`` returns a singleton that's
safe to reuse across sends; each call is self-contained (the inbox row
is passed on ``send()``).

Callsite::

    from ..services.email_providers import get_provider

    provider = get_provider(selected_inbox["provider"])
    result = await provider.send(send_input, inbox=selected_inbox)

Adding a new provider: implement ``EmailProvider``, import it here,
register in ``_FACTORIES``.
"""

from __future__ import annotations

from typing import Any, Callable

from ...core.logging import get_logger
from .base import EmailProvider, ProviderName
from .gmail_provider import GmailProvider
from .resend_provider import ResendProvider

log = get_logger(__name__)

# Lazy singletons — instantiated on first access per-process. None of
# the providers hold mutable state that can't be shared across coroutines.
_instances: dict[str, EmailProvider] = {}

_FACTORIES: dict[str, Callable[..., EmailProvider]] = {
    "resend": lambda **_: ResendProvider(),
    "gmail_oauth": lambda **kw: GmailProvider(sb=kw.get("sb")),
}


def get_provider(
    name: str,
    *,
    sb: Any | None = None,
) -> EmailProvider:
    """Return the singleton provider for ``name``.

    ``sb`` (Supabase service-role client) is forwarded to providers
    that need to persist side-effects (e.g. refreshed access tokens).
    It's optional so pure-send callers don't have to thread it through.
    """
    if not name:
        name = "resend"
    factory = _FACTORIES.get(name)
    if factory is None:
        log.warning("email_provider.unknown", name=name, fallback="resend")
        name = "resend"
        factory = _FACTORIES["resend"]

    # For providers that take constructor args (GmailProvider needs
    # ``sb``), re-create on sb change to avoid stale client refs. For
    # the stateless ResendProvider, singleton is fine.
    cache_key = f"{name}:{id(sb) if sb else 0}"
    instance = _instances.get(cache_key)
    if instance is None:
        instance = factory(sb=sb)
        _instances[cache_key] = instance
    return instance


def list_providers() -> list[ProviderName]:
    """List known provider names — used by the inboxes CRUD validator."""
    return ["resend", "gmail_oauth", "m365_oauth", "smtp"]
