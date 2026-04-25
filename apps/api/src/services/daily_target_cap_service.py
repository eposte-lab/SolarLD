"""Tenant daily "in-target" send cap (Sprint 2).

Enforces the contractual SLA: each tenant may send at most
``tenants.daily_target_send_cap`` (default 250) cold-outreach emails
per Europe/Rome calendar day.

Contrast with ``rate_limit_service.py``:
  - rate_limit_service is a *deliverability* guard — domain warm-up
    + tier hourly cap; protects reputation against burst sends.
  - daily_target_cap_service is a *commercial* guard — limits the
    aggregate volume the tenant may push out regardless of how many
    inboxes / domains / channels they wire up.

Both run on every send. They're orthogonal: a tenant with no
warm-up issues can still hit the daily 250 cap; a fresh-domain
tenant in warm-up will hit the warm-up curve first.

Implementation:
  - Counter in Redis: ``daily_target_cap:{tenant_id}:{YYYY-MM-DD}``
    where date is Europe/Rome (so reset happens at local midnight,
    matching the dashboard widget the customer looks at).
  - INCR on reserve. If post-increment value > cap, DECR back and
    return BLOCKED. Race window: at the cap boundary two concurrent
    INCRs may briefly exceed by 1; harmless for our throughput.
  - TTL 36h on the key so a tenant that stops sending doesn't leave
    a stale counter around forever.

The pure helpers (``cap_for_tenant``, ``redis_key_for``) are kept
unit-testable. The full ``check_and_reserve`` requires Redis and
gets exercised in integration tests with a fakeredis instance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from ..core.logging import get_logger
from ..core.redis import get_redis

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Default SLA cap — used when a tenant row predates the migration or
# somehow has the column NULL'd (defensive; CHECK constraint should
# prevent NULL but we don't crash if it slips through).
DEFAULT_DAILY_CAP = 250

# Counter TTL: 36 hours covers the 24h window plus a generous slack
# for daylight-saving transitions and clock drift between API nodes.
COUNTER_TTL_S = 36 * 3600

# Calendar timezone — must match the timezone shown in the dashboard
# widget. We use Europe/Rome regardless of where the API is deployed
# because the product is sold to Italian installers and "today" must
# mean "today in Italy" to them, not UTC.
TZ_ROME = ZoneInfo("Europe/Rome")


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


Verdict = Literal["allowed", "cap_reached"]


@dataclass(slots=True)
class DailyTargetCapDecision:
    """Outcome of a ``check_and_reserve`` call."""

    verdict: Verdict
    used: int           # post-reserve count (or pre-reserve if blocked)
    limit: int
    tenant_id: str

    @property
    def allowed(self) -> bool:
        return self.verdict == "allowed"

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def cap_for_tenant(tenant_row: dict[str, Any]) -> int:
    """Resolve the daily target cap for a tenant row.

    Reads ``daily_target_send_cap`` and falls back to
    ``DEFAULT_DAILY_CAP`` when the column is missing/None/non-positive.
    """
    raw = tenant_row.get("daily_target_send_cap")
    if isinstance(raw, int) and raw > 0:
        return raw
    if isinstance(raw, float) and raw > 0:
        return int(raw)
    return DEFAULT_DAILY_CAP


def redis_key_for(tenant_id: str, *, now_utc: datetime | None = None) -> str:
    """Build the Redis counter key for a tenant + the current Rome date.

    ``now_utc`` is exposed for unit-testability; production callers
    should leave it None to use the wall clock.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    rome_date = now_utc.astimezone(TZ_ROME).strftime("%Y-%m-%d")
    return f"daily_target_cap:{tenant_id}:{rome_date}"


# ---------------------------------------------------------------------------
# Hot-path entry: check + atomic reserve
# ---------------------------------------------------------------------------


async def check_and_reserve(
    tenant_row: dict[str, Any],
) -> DailyTargetCapDecision:
    """Reserve one slot from the tenant's daily quota.

    Returns ``allowed=True`` if a slot was consumed; the caller must
    proceed with the send. Returns ``allowed=False`` (verdict
    ``cap_reached``) when the daily quota is already exhausted; the
    caller must skip this send (which the follow-up scheduler retries
    tomorrow when the counter resets).

    Redis-down behaviour: log + fail-open (return ``allowed`` with
    used=0). Reasoning: a 5-minute Redis blip shouldn't kill the
    tenant's entire send pipeline. The downside is at most ~250
    extra sends slipping through during the outage, which is
    bounded by the inbox-level caps that always run.
    """
    tenant_id = tenant_row.get("id")
    if not tenant_id:
        # Defensive: tenant_row malformed → fail-open with a log.
        log.warning("daily_target_cap.no_tenant_id", row_keys=list(tenant_row.keys()))
        return DailyTargetCapDecision("allowed", 0, DEFAULT_DAILY_CAP, "")

    cap = cap_for_tenant(tenant_row)
    key = redis_key_for(str(tenant_id))

    try:
        r = get_redis()
        # Atomic INCR: returns the post-increment count.
        new_count = await r.incr(key)
        # On the very first increment, set the TTL.
        if new_count == 1:
            await r.expire(key, COUNTER_TTL_S)

        if new_count > cap:
            # We crossed the cap with this reservation — roll back and
            # report blocked. The DECR keeps the counter accurate so
            # subsequent calls also see "at cap" rather than slowly
            # diverging upward.
            await r.decr(key)
            return DailyTargetCapDecision(
                verdict="cap_reached",
                used=cap,           # report the cap as "used" so the UI shows 250/250
                limit=cap,
                tenant_id=str(tenant_id),
            )

        return DailyTargetCapDecision(
            verdict="allowed",
            used=int(new_count),
            limit=cap,
            tenant_id=str(tenant_id),
        )
    except Exception as exc:  # noqa: BLE001
        # Fail-open with a loud log. Inbox-level caps still apply,
        # which keeps the worst-case blast radius bounded.
        log.warning(
            "daily_target_cap.redis_error_fail_open",
            tenant_id=str(tenant_id),
            err=str(exc),
        )
        return DailyTargetCapDecision("allowed", 0, cap, str(tenant_id))


# ---------------------------------------------------------------------------
# Read-only peek for the dashboard widget
# ---------------------------------------------------------------------------


async def peek_usage(tenant_row: dict[str, Any]) -> DailyTargetCapDecision:
    """Read current count without reserving.

    Used by ``GET /v1/usage/daily-target`` for the dashboard widget.
    Always returns ``verdict='allowed'`` — the verdict field is just
    so the consumer can use the same dataclass shape as
    ``check_and_reserve``.
    """
    tenant_id = tenant_row.get("id")
    cap = cap_for_tenant(tenant_row)
    if not tenant_id:
        return DailyTargetCapDecision("allowed", 0, cap, "")

    key = redis_key_for(str(tenant_id))
    try:
        r = get_redis()
        used = int(await r.get(key) or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "daily_target_cap.peek_error",
            tenant_id=str(tenant_id),
            err=str(exc),
        )
        used = 0

    verdict: Verdict = "cap_reached" if used >= cap else "allowed"
    return DailyTargetCapDecision(
        verdict=verdict,
        used=used,
        limit=cap,
        tenant_id=str(tenant_id),
    )
