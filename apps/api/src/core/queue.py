"""Lightweight arq enqueue helpers.

The FastAPI side doesn't import `src.workers.main` to avoid circular imports
(workers import agents which import services which may import routes); it
talks to Redis directly via `arq.connections.ArqRedis`.

Usage:

    from ..core.queue import enqueue

    job = await enqueue("hunter_task", {"tenant_id": ..., "territory_id": ...})
    return {"job_id": job.job_id}
"""

from __future__ import annotations

from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from .config import settings
from .logging import get_logger

log = get_logger(__name__)

_pool: ArqRedis | None = None


async def get_pool() -> ArqRedis:
    """Return (or lazily create) a shared arq Redis pool."""
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool


async def enqueue(
    function: str, payload: dict[str, Any], *, job_id: str | None = None
) -> dict[str, Any]:
    """Enqueue an arq job and return a minimal descriptor.

    `function` must match one of the callables registered in
    `src/workers/main.py::WorkerSettings.functions`.
    """
    pool = await get_pool()
    job = await pool.enqueue_job(function, payload, _job_id=job_id)
    if job is None:
        log.warning("enqueue_skipped_duplicate", function=function, job_id=job_id)
        return {"job_id": job_id, "status": "duplicate"}
    return {"job_id": job.job_id, "status": "queued"}


async def close_pool() -> None:
    """Close the pool — called from FastAPI lifespan shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
