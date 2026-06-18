"""Event-loop watchdog — auto-restart the worker if it ever wedges.

The 2026-06-18 outage: a single sync-blocking job froze the arq worker's event
loop, so it ran a startup burst then went silent — **no crash** (so Railway
never restarted it), no sends, until the operator noticed hours later.

This makes that self-healing. An async heartbeat task stamps a monotonic
timestamp every few seconds; a daemon THREAD — which a wedged event loop can't
block, because it isn't on the loop — checks that stamp and, if it goes stale
past the threshold, force-exits the process. Railway then restarts the
container and the stranded-pick rescue cron re-fires the sends. The worst-case
freeze drops from "until a human notices" to roughly the threshold.

os._exit is deliberate: a wedged loop can't shut down gracefully, and any
half-sent email is covered by the OutreachAgent's already-sent dedup.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time

from ..core.logging import get_logger

log = get_logger(__name__)

# Monotonic timestamp of the last successful event-loop heartbeat. Read by the
# watcher thread, written by the async heartbeat task.
_last_beat: float = time.monotonic()
_started: bool = False


def _beat() -> None:
    global _last_beat
    _last_beat = time.monotonic()


def seconds_since_beat(now: float | None = None) -> float:
    """How long the event loop has gone without a heartbeat."""
    return (now if now is not None else time.monotonic()) - _last_beat


def should_exit(stale_seconds: float, threshold_seconds: float) -> bool:
    """Pure decision: is the loop wedged past the threshold? (testable)."""
    return threshold_seconds > 0 and stale_seconds > threshold_seconds


async def _heartbeat_loop(interval: float) -> None:
    while True:
        _beat()
        await asyncio.sleep(interval)


def _watch(threshold: float, check_interval: float) -> None:  # pragma: no cover - thread
    while True:
        time.sleep(check_interval)
        stale = seconds_since_beat()
        if should_exit(stale, threshold):
            log.error(
                "worker_watchdog.event_loop_wedged",
                stale_seconds=round(stale, 1),
                threshold=threshold,
            )
            # Hard-exit: a wedged loop can't unwind cleanly. Railway restarts us.
            os._exit(1)


def start_watchdog(*, threshold_seconds: float, interval_seconds: float = 5.0) -> bool:
    """Start the heartbeat task + watcher thread. Idempotent.

    Returns True if started, False if disabled (threshold<=0) or already running.
    Must be called from inside the running event loop (creates an asyncio task).
    """
    global _started
    if _started:
        return False
    if threshold_seconds <= 0:
        log.info("worker_watchdog.disabled")
        return False
    _started = True
    _beat()
    asyncio.create_task(_heartbeat_loop(interval_seconds))
    threading.Thread(
        target=_watch,
        kwargs={"threshold": threshold_seconds, "check_interval": min(10.0, threshold_seconds / 3)},
        name="worker-watchdog",
        daemon=True,
    ).start()
    log.info("worker_watchdog.started", threshold_seconds=threshold_seconds)
    return True
