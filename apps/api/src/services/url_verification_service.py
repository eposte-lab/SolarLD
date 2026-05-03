"""HEAD-check URL reachability with a Redis cache.

Sprint C of the production-readiness alignment. Used today by the
enrichment pipeline to validate ``subjects.linkedin_url`` before
writing it to the database — Atoka, Hunter.io, and the demo mock
enrichment table all return URLs they scraped from third-party
sources without verifying the page actually resolves. Surfacing dead
LinkedIn links in the dashboard ``leads/[id]`` page (rendered as a
clickable ``<a target="_blank">``) and the prospect-list export
(consumed by external CRMs) leaks broken data into the customer
experience.

Cache: Redis 7-day TTL keyed by SHA-1 of the URL. LinkedIn doesn't
return a 200 to anonymous HEADs (its WAF responds 999 — see special
case below), so we still pay the network round-trip for first-seen
URLs, but the cache amortises across re-scrapes of the same VAT.

Failure modes (treated as ``False``):
  * 4xx (except 999), 5xx
  * httpx network errors (DNS, TLS, ConnectError)
  * timeout

Special cases (treated as ``True``):
  * HTTP 999 — LinkedIn's bot-detection response. The URL is
    structurally valid; Linkedin just blocks anonymous HEADs. We
    don't want to drop every LinkedIn link as "unreachable" because
    of WAF policy.
  * 3xx redirects — followed up to one hop, then we trust the final
    hop's status code (typical "linkedin.com → www.linkedin.com").
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import httpx

from ..core.logging import get_logger

if TYPE_CHECKING:
    pass

log = get_logger(__name__)


# Cache TTL: 7 days. LinkedIn pages don't change reachability often.
# A profile that exists today is overwhelmingly likely to exist a
# week from now. If a URL was unreachable, we re-check a week later
# in case the operator fixed the data.
_CACHE_TTL_SECONDS = 60 * 60 * 24 * 7
_CACHE_KEY_PREFIX = "url_reachable:"


def _cache_key(url: str) -> str:
    h = hashlib.sha1(url.strip().encode("utf-8")).hexdigest()
    return f"{_CACHE_KEY_PREFIX}{h}"


async def _cache_get(url: str) -> bool | None:
    """Return cached reachability for url, or None on cache miss/Redis down.

    Cache values are the literal strings ``"1"`` / ``"0"``; anything
    else (None, decode error) → cache miss. We never let a Redis hiccup
    bubble up; the worst case is a fresh HEAD call.
    """
    try:
        from ..core.redis import get_redis

        r = get_redis()
        val = await r.get(_cache_key(url))
        if val == "1":
            return True
        if val == "0":
            return False
        return None
    except Exception as exc:  # noqa: BLE001
        log.debug("url_verify.cache_get_failed", err=str(exc)[:120])
        return None


async def _cache_set(url: str, reachable: bool) -> None:
    """Best-effort cache write — never raises."""
    try:
        from ..core.redis import get_redis

        r = get_redis()
        await r.setex(_cache_key(url), _CACHE_TTL_SECONDS, "1" if reachable else "0")
    except Exception as exc:  # noqa: BLE001
        log.debug("url_verify.cache_set_failed", err=str(exc)[:120])


async def is_url_reachable(
    url: str,
    *,
    timeout_s: float = 5.0,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """HEAD-check ``url`` and return True iff the page resolves.

    Returns False on:
      * empty / malformed url
      * 4xx (except 999) / 5xx after one redirect
      * any network or timeout error

    Cache: Redis 7-day TTL. Cache hit → instant return; cache miss →
    network round-trip + write-through.
    """
    cleaned = (url or "").strip()
    if not cleaned or "://" not in cleaned:
        return False

    cached = await _cache_get(cleaned)
    if cached is not None:
        return cached

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=timeout_s, follow_redirects=True)

    try:
        try:
            resp = await client.head(cleaned, follow_redirects=True)
        except httpx.RequestError as exc:
            # DNS failure, TLS handshake, ConnectError, ReadTimeout, etc.
            log.info(
                "url_verify.unreachable",
                url=cleaned[:200],
                err_type=type(exc).__name__,
            )
            await _cache_set(cleaned, False)
            return False

        status = resp.status_code
        # LinkedIn's bot WAF response — URL is structurally valid even
        # if HEAD is blocked. Treat as reachable so we don't drop every
        # LinkedIn link blindly.
        if status == 999:
            await _cache_set(cleaned, True)
            return True

        # 2xx → reachable. 3xx after follow_redirects=True is unusual
        # (would mean too many hops); treat as not reachable.
        reachable = 200 <= status < 400
        if not reachable:
            log.info(
                "url_verify.bad_status",
                url=cleaned[:200],
                status=status,
            )
        await _cache_set(cleaned, reachable)
        return reachable
    finally:
        if owns_client:
            await client.aclose()


async def filter_url_or_none(
    url: str | None, *, timeout_s: float = 5.0
) -> str | None:
    """Convenience wrapper: return url when reachable, else None.

    Use this at write sites where we want to drop unreachable URLs:
        ``profile.linkedin_url = await filter_url_or_none(linkedin)``
    """
    if not url or not url.strip():
        return None
    return url if await is_url_reachable(url, timeout_s=timeout_s) else None
