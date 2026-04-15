"""Redis client (async) for cache, rate limiting, and queue ops."""

from __future__ import annotations

import redis.asyncio as redis_async

from .config import settings

_client: redis_async.Redis | None = None


def get_redis() -> redis_async.Redis:
    """Lazy singleton redis client."""
    global _client
    if _client is None:
        _client = redis_async.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
        )
    return _client


async def close_redis() -> None:
    """Close the redis client on shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
