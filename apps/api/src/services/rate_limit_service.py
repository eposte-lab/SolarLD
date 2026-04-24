"""Email rate limiter — protects sender reputation before Resend gets to say no.

Two orthogonal caps are enforced, both keyed by ``email_from_domain``:

  1. **Warm-up daily cap** (only for the first 7 days after
     ``tenants.email_from_domain_verified_at``). Follows the industry-
     standard 20/50/100/200/500/1000/2000 ramp — the daily cap doubles
     every 48h except the first step, preventing ISP graylisting. If the
     tenant has never verified (``verified_at IS NULL``) we fall back to
     the day-1 cap of 20 mail/die, which is *conservative on purpose* —
     an unverified domain sending more than 20/die is very likely to
     get blacklisted inside 48h.

  2. **Steady-state hourly cap** (after warm-up). Tier-based:
     founding=15/h, pro=60/h, enterprise=300/h. Override per-tenant via
     ``tenants.settings.email_rate_per_hour`` (integer).

Decision order::

    is_warming_up?  →  daily_cap = warmup_curve(day_n)
    else            →  hourly_cap = tier_or_override()

Redis keys (fixed windows — simpler + cheaper than sliding log,
error bounded to ≤1 window):

    ratelimit:email:hour:{domain}:{YYYY-MM-DD-HH}     # INCR + EXPIRE 90m
    ratelimit:email:day:{domain}:{YYYY-MM-DD}         # INCR + EXPIRE 48h

Both increments happen *after* the consumer has decided to send, via
``acquire_email_quota()`` which returns the verdict atomically. There
is no explicit release — quota is time-bounded, not transactional.

Unit testable pieces are kept pure at the bottom of the file:
``warmup_day_cap()``, ``tier_hourly_cap()``, ``is_warming_up()``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from ..core.logging import get_logger
from ..core.redis import get_redis
from ..core.tier import TenantTier

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Tunables — keep in sync with settings UI / docs
# ---------------------------------------------------------------------------

WARMUP_DAYS = 7

# Day-1 .. day-7 caps (mail/die). Index 0 = day 1.
# Standard industry ramp — ISPs prefer predictable daily deltas.
WARMUP_DAILY_CURVE: tuple[int, ...] = (20, 50, 100, 200, 500, 1000, 2000)

# ---------------------------------------------------------------------------
# Per-inbox 21-day outreach warm-up (Sprint 6.3)
# ---------------------------------------------------------------------------
# Cold outreach inboxes (Gmail Workspace, purpose='outreach') need a much
# gentler ramp than the domain-wide curve above. ISPs like Gmail track
# per-sender reputation (individual address, not just domain), and a new
# address sending 50/day from day 1 triggers spam filters.
#
# Curve: week1 → 10/day, week2 → 25/day, week3 → 40/day, day22+ → steady
# This is in line with Lemlist / Smartlead / Instantly defaults.

WARMUP_OUTREACH_DAYS = 21

# Index 0 = day 1. len = 21.
WARMUP_DAILY_CAPS_OUTREACH: tuple[int, ...] = (
    # Week 1 (days 1-7)
    10, 10, 10, 10, 10, 10, 10,
    # Week 2 (days 8-14)
    25, 25, 25, 25, 25, 25, 25,
    # Week 3 (days 15-21)
    40, 40, 40, 40, 40, 40, 40,
)
# After day 21, inbox sends at its configured daily_cap (default 50).
STEADY_STATE_OUTREACH_CAP = 50
STEADY_STATE_BRAND_CAP = 200  # transactional / nurturing uses domain-level cap

# Hourly cap after warm-up, per tier.
TIER_HOURLY_CAP: dict[TenantTier, int] = {
    "founding": 15,
    "pro": 60,
    "enterprise": 300,
}

# Redis key TTLs (seconds). A tiny buffer over the window length so a
# clock drift doesn't give a stale counter a second life.
_HOUR_KEY_TTL = 90 * 60        # 90 minutes
_DAY_KEY_TTL = 48 * 60 * 60    # 48 hours


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


Verdict = Literal["allowed", "hour_cap", "warmup_cap", "no_domain"]


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of an ``acquire_email_quota()`` call.

    ``allowed``       — quota incremented, caller may send.
    ``hour_cap``      — steady-state hourly cap hit, try next hour.
    ``warmup_cap``    — daily warm-up cap hit, try tomorrow.
    ``no_domain``     — tenant has no ``email_from_domain`` set — we
                        skip the gate entirely (fail open; the caller
                        will likely fail at the From-address check).
    """

    verdict: Verdict
    used: int
    limit: int
    window: Literal["hour", "day", "none"]
    domain: str | None

    @property
    def allowed(self) -> bool:
        return self.verdict == "allowed"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def acquire_email_quota(tenant_row: dict[str, Any]) -> RateLimitDecision:
    """Consume one unit of email quota for ``tenant_row.email_from_domain``.

    ``tenant_row`` is the tenants row as returned by Supabase (must
    include ``tier``, ``settings``, ``email_from_domain``,
    ``email_from_domain_verified_at``).

    Fails open on Redis errors — reputation is important but not so
    important that a transient Redis outage should halt all outreach.
    Returns ``Verdict.allowed`` in that case and logs a warning so
    on-call notices the degradation.
    """
    domain = (tenant_row.get("email_from_domain") or "").strip().lower()
    if not domain:
        return RateLimitDecision(
            verdict="no_domain",
            used=0,
            limit=0,
            window="none",
            domain=None,
        )

    tier: TenantTier = tenant_row.get("tier") or "founding"
    settings_obj = tenant_row.get("settings") or {}
    verified_at = _parse_verified_at(tenant_row.get("email_from_domain_verified_at"))

    now = datetime.now(timezone.utc)
    warming = is_warming_up(verified_at=verified_at, now=now)

    if warming:
        day_n = _warmup_day_index(verified_at=verified_at, now=now)
        cap = warmup_day_cap(day_n)
        return await _incr_and_check(
            key=f"ratelimit:email:day:{domain}:{now.strftime('%Y-%m-%d')}",
            limit=cap,
            ttl=_DAY_KEY_TTL,
            window="day",
            domain=domain,
            exceeded_verdict="warmup_cap",
        )

    override = settings_obj.get("email_rate_per_hour") if isinstance(settings_obj, dict) else None
    cap = tier_hourly_cap(tier, override=override)
    return await _incr_and_check(
        key=f"ratelimit:email:hour:{domain}:{now.strftime('%Y-%m-%d-%H')}",
        limit=cap,
        ttl=_HOUR_KEY_TTL,
        window="hour",
        domain=domain,
        exceeded_verdict="hour_cap",
    )


async def peek_email_quota(tenant_row: dict[str, Any]) -> RateLimitDecision:
    """Read current quota usage WITHOUT incrementing — used for the
    ``/settings`` reputation card + dashboard debugging. Same shape as
    ``acquire_email_quota`` but never consumes a slot.
    """
    domain = (tenant_row.get("email_from_domain") or "").strip().lower()
    if not domain:
        return RateLimitDecision("no_domain", 0, 0, "none", None)

    tier: TenantTier = tenant_row.get("tier") or "founding"
    settings_obj = tenant_row.get("settings") or {}
    verified_at = _parse_verified_at(tenant_row.get("email_from_domain_verified_at"))
    now = datetime.now(timezone.utc)

    try:
        r = get_redis()
        if is_warming_up(verified_at=verified_at, now=now):
            day_n = _warmup_day_index(verified_at=verified_at, now=now)
            cap = warmup_day_cap(day_n)
            key = f"ratelimit:email:day:{domain}:{now.strftime('%Y-%m-%d')}"
            used = int(await r.get(key) or 0)
            return RateLimitDecision(
                verdict="allowed" if used < cap else "warmup_cap",
                used=used,
                limit=cap,
                window="day",
                domain=domain,
            )
        override = settings_obj.get("email_rate_per_hour") if isinstance(settings_obj, dict) else None
        cap = tier_hourly_cap(tier, override=override)
        key = f"ratelimit:email:hour:{domain}:{now.strftime('%Y-%m-%d-%H')}"
        used = int(await r.get(key) or 0)
        return RateLimitDecision(
            verdict="allowed" if used < cap else "hour_cap",
            used=used,
            limit=cap,
            window="hour",
            domain=domain,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("ratelimit.peek_failed", domain=domain, err=str(exc))
        return RateLimitDecision("allowed", 0, 0, "none", domain)


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable — no I/O)
# ---------------------------------------------------------------------------


def warmup_day_cap(day_n: int) -> int:
    """Return the daily cap for warm-up day ``day_n`` (1-based).

    day_n=1 → 20, day_n=2 → 50, ..., day_n=7 → 2000.
    Out of range → the first cap (defensive; caller should branch on
    ``is_warming_up`` first).
    """
    idx = max(1, min(WARMUP_DAYS, day_n)) - 1
    return WARMUP_DAILY_CURVE[idx]


def tier_hourly_cap(tier: TenantTier, *, override: Any = None) -> int:
    """Hourly cap for steady-state tenants. Override wins when a
    positive integer (or int-coercible)."""
    if isinstance(override, int) and override > 0:
        return override
    if isinstance(override, float) and override > 0:
        return int(override)
    return TIER_HOURLY_CAP.get(tier, TIER_HOURLY_CAP["founding"])


def is_warming_up(
    *,
    verified_at: datetime | None,
    now: datetime,
) -> bool:
    """True iff the domain is in its first ``WARMUP_DAYS`` days.

    Never-verified (``verified_at=None``) → treated as warming (daily
    cap applies at day 1). This is the safe default: we don't know the
    domain is healthy, so we ramp.
    """
    if verified_at is None:
        return True
    delta_days = (now - verified_at).days
    return delta_days < WARMUP_DAYS


def _warmup_day_index(
    *,
    verified_at: datetime | None,
    now: datetime,
) -> int:
    """1-based day index into the warm-up curve."""
    if verified_at is None:
        return 1
    return max(1, min(WARMUP_DAYS, (now - verified_at).days + 1))


# ---------------------------------------------------------------------------
# Per-inbox warm-up helpers (Sprint 6.3)
# ---------------------------------------------------------------------------


def inbox_effective_daily_cap(inbox: dict[str, Any]) -> int:
    """Return the effective daily send cap for this inbox.

    Applies the 21-day per-inbox outreach warm-up curve when:
      1. ``warmup_started_at`` is set (first send has happened), AND
      2. We're still within the first 21 days of warm-up.

    After day 21, or for brand inboxes (email_style='visual_preventivo'),
    returns the configured ``daily_cap`` column value.

    Both ``warmup_phase_day`` (1-21) and ``warmup_completed`` are derived
    from ``warmup_started_at`` on the fly — Postgres can't express them as
    STORED generated columns because CURRENT_DATE/NOW() aren't immutable.
    ``warmup_started_at`` is the single source of truth.

    Args:
        inbox: A ``tenant_inboxes`` row dict (or compatible).

    Returns:
        Effective daily cap (integer ≥ 1).
    """
    from datetime import date, datetime, timezone

    daily_cap = int(inbox.get("daily_cap") or STEADY_STATE_OUTREACH_CAP)
    style = inbox.get("email_style") or "visual_preventivo"

    # Brand inboxes (Resend transactional) skip per-inbox outreach warm-up.
    # They're governed by the domain-level acquire_email_quota() above.
    if style == "visual_preventivo":
        return daily_cap

    warmup_started_at = inbox.get("warmup_started_at")
    if not warmup_started_at:
        # Warm-up not started yet — first send will trigger the start.
        # Return the most conservative cap (day 1 = 10).
        return WARMUP_DAILY_CAPS_OUTREACH[0]

    # Parse warmup_started_at (stringified timestamptz from PostgREST, or
    # an actual datetime if called from Python code).
    try:
        if isinstance(warmup_started_at, datetime):
            started = warmup_started_at.date()
        else:
            # "2026-04-01T..." or "2026-04-01 ..." — only the date matters.
            started = date.fromisoformat(str(warmup_started_at)[:10])
    except Exception:  # noqa: BLE001
        return WARMUP_DAILY_CAPS_OUTREACH[0]

    # Days-into-warmup is 1-indexed: the start day itself is day 1.
    day_delta = (date.today() - started).days + 1

    # Steady state: past day 21.
    if day_delta > WARMUP_OUTREACH_DAYS:
        return daily_cap

    idx = max(1, min(WARMUP_OUTREACH_DAYS, day_delta)) - 1
    curve_cap = WARMUP_DAILY_CAPS_OUTREACH[idx]
    # Never exceed the configured daily_cap — in case ops set it lower.
    return min(curve_cap, daily_cap)


def inbox_warmup_phase_day(inbox: dict[str, Any]) -> int | None:
    """Compute current warm-up day (1-21) for an inbox, or None.

    Substitute for the dropped ``warmup_phase_day`` generated column.
    Returns None when warmup has not started. Clamped to 21 after day 21.
    """
    from datetime import date, datetime

    started_raw = inbox.get("warmup_started_at")
    if not started_raw:
        return None
    try:
        if isinstance(started_raw, datetime):
            started = started_raw.date()
        else:
            started = date.fromisoformat(str(started_raw)[:10])
    except Exception:  # noqa: BLE001
        return None
    return max(1, min(WARMUP_OUTREACH_DAYS, (date.today() - started).days + 1))


def inbox_warmup_completed(inbox: dict[str, Any]) -> bool:
    """Return True iff the inbox finished its 21-day warm-up.

    Substitute for the dropped ``warmup_completed`` generated column.
    """
    phase = inbox_warmup_phase_day(inbox)
    if phase is None:
        return False
    return phase >= WARMUP_OUTREACH_DAYS


# ---------------------------------------------------------------------------
# Internal — Redis ops
# ---------------------------------------------------------------------------


async def _incr_and_check(
    *,
    key: str,
    limit: int,
    ttl: int,
    window: Literal["hour", "day"],
    domain: str,
    exceeded_verdict: Verdict,
) -> RateLimitDecision:
    """Increment the counter then compare to limit.

    We increment *before* checking so concurrent callers can't race
    past the cap (classic "check-then-set" bug). If the post-increment
    value is over the limit we return the exceeded verdict *and*
    leave the counter as-is — the extra increment doesn't matter for
    reputation (we're already at the edge) and decrementing on loss
    is a footgun with TTL races.
    """
    try:
        r = get_redis()
        # Pipeline INCR + EXPIRE atomically — EXPIRE on every call is
        # cheap and covers the "key was created but never expired"
        # case if a previous crash happened between INCR and EXPIRE.
        pipe = r.pipeline()
        pipe.incr(key, amount=1)
        pipe.expire(key, ttl)
        results = await pipe.execute()
        used = int(results[0])
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "ratelimit.incr_failed",
            key=key,
            err=str(exc),
        )
        # Fail open — a Redis outage shouldn't halt outreach. On-call
        # gets the warning and can roll back if needed.
        return RateLimitDecision("allowed", 0, limit, window, domain)

    if used > limit:
        return RateLimitDecision(
            verdict=exceeded_verdict,
            used=used,
            limit=limit,
            window=window,
            domain=domain,
        )
    return RateLimitDecision(
        verdict="allowed",
        used=used,
        limit=limit,
        window=window,
        domain=domain,
    )


def _parse_verified_at(value: Any) -> datetime | None:
    """Supabase returns timestamps as ISO strings — normalize to aware
    datetime. Returns None for null / parse failures."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None
