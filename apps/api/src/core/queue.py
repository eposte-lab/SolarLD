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

from datetime import datetime
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
    function: str,
    payload: dict[str, Any],
    *,
    job_id: str | None = None,
    defer_until: datetime | None = None,
) -> dict[str, Any]:
    """Enqueue an arq job and return a minimal descriptor.

    `function` must match one of the callables registered in
    `src/workers/main.py::WorkerSettings.functions`.

    ``defer_until`` (B.3 send-time optimisation): when set, arq holds
    the job in its sorted-set until the given instant. Must be
    tz-aware — arq converts to UTC internally. Pass ``None`` (default)
    for "run ASAP" semantics.
    """
    pool = await get_pool()
    kwargs: dict[str, Any] = {"_job_id": job_id}
    if defer_until is not None:
        kwargs["_defer_until"] = defer_until
    job = await pool.enqueue_job(function, payload, **kwargs)
    if job is None:
        log.warning("enqueue_skipped_duplicate", function=function, job_id=job_id)
        return {"job_id": job_id, "status": "duplicate"}
    return {"job_id": job.job_id, "status": "queued"}


async def fire_crm_event(
    *,
    tenant_id: str,
    event_type: str,
    data: dict[str, Any],
    occurred_at: str | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for agents: enqueue a CRM webhook fanout.

    Keyed by (tenant, event, subject) so repeated enqueues for the
    same logical event collapse to one job. Callers should pass a
    stable ``data["id"]`` (e.g. lead_id) so the job_id stays stable.
    """
    from datetime import datetime, timezone

    subject_id = data.get("id") or data.get("lead_id") or data.get("subject_id") or ""
    payload = {
        "tenant_id": tenant_id,
        "event_type": event_type,
        "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    return await enqueue(
        "crm_webhook_task",
        payload,
        job_id=f"crm:{tenant_id}:{event_type}:{subject_id}",
    )


async def close_pool() -> None:
    """Close the pool — called from FastAPI lifespan shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
