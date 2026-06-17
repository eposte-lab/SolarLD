"""Account-level client-side throttle for Replicate prediction CREATES.

Replicate rate-limits *creating predictions* per-account. This account is
currently throttled to ~6/min with a burst of 1, so firing the render pipeline
(``creative_task``) at arq concurrency self-inflicted HTTP 429s: 8-10 prediction
creates hit the limit at once, each retried once into the *same* throttled
minute-window, and roughly half the renders were skipped
(``lead.render_skipped``) — which starved the send pipeline (~28/50 sent on
2026-06-17).

Every prediction-create path shares the one account budget — ai_panel_paint
(nano-banana), masked_inpaint, and replicate_service (img2img) — so the throttle
lives here and each create site awaits :func:`acquire_create_slot` immediately
before its ``POST /predictions``. We serialise creates and *space* them to
``settings.replicate_creates_per_min``; spacing (not just a semaphore) is what
keeps us under a *per-minute* limit, and serialising honours the burst-of-1.

In-process: correct for our single arq worker service. If the worker is ever
scaled to >1 replica, move this to a Redis token-bucket so the replicas
coordinate — otherwise each replica gets its own private budget and the 429s
return.
"""

from __future__ import annotations

import asyncio

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)

# Serialises slot acquisition and guards the last-create timestamp. The lock is
# held *through* the spacing sleep on purpose: concurrent callers queue and each
# is handed its own spaced slot, instead of all waking together and bursting.
_LOCK = asyncio.Lock()
_last_create_at: float = 0.0


async def acquire_create_slot() -> None:
    """Block until a Replicate prediction-create slot is free.

    Enforces at most ``settings.replicate_creates_per_min`` creates per minute,
    burst 1. A misconfigured (<= 0) rate falls back to 6/min so a bad env value
    can never disable the throttle entirely.
    """
    global _last_create_at
    per_min = settings.replicate_creates_per_min or 6
    if per_min <= 0:
        per_min = 6
    min_interval = 60.0 / per_min
    async with _LOCK:
        loop = asyncio.get_event_loop()
        wait = _last_create_at + min_interval - loop.time()
        if wait > 0:
            log.info("replicate.throttle_wait", seconds=round(wait, 2), per_min=per_min)
            await asyncio.sleep(wait)
        _last_create_at = loop.time()
