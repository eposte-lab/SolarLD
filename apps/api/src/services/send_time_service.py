"""Smart send-time optimisation (Part B.3).

Goal: instead of firing step-2 / step-3 nudges the moment the nightly
``follow_up_cron`` wakes up (07:30 UTC), send them at the **hour of
day the lead has historically opened email**. Expected lift on open
rate: +20-30% per industry benchmarks — the gaussian of opens around
send time is narrow (3-5h peak), so aligning send-time to the
individual peak shifts the whole distribution.

Data source:
    ``events`` rows where ``event_type`` ∈ {
        lead.email_opened, lead.email_clicked
    } in the last ``LOOKBACK_DAYS`` days.
    (Events are created by ``agents.tracking`` from the Resend
    webhook; ``occurred_at`` carries the open timestamp with 1-second
    resolution.)

Output:
    ``leads.best_send_hour`` — SMALLINT 0..23 UTC, nullable. NULL means
    "no signal yet, use fallback". Written by ``run_send_time_rollup``
    (the nightly cron); read by ``pick_next_send_time`` (called from
    ``follow_up_cron`` for each enqueue).

Fallback ladder (in order):
    1. ``leads.best_send_hour`` — the lead's own mode.
    2. ``tenants.settings.default_send_hour_utc`` — tenant-level
       operator override (set via admin or a future /settings UI).
    3. ``DEFAULT_SEND_HOUR_UTC`` = 9 (≈ 10-11 CET, business AM).

Why mode and not mean/median:
    A lead who opens at 08, 09, 23 has a trimodal distribution. The
    mean (13) and median (9) both distort. The mode (whichever hour
    won the most opens) picks the most likely next-open window —
    which is what we want for scheduling.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client

log = get_logger(__name__)

# Rolling window for the rollup. 180d balances "enough samples to
# trust the mode" against "don't anchor on a 14-month-old habit".
LOOKBACK_DAYS = 180

# Event types that count as "the lead saw the email at this hour".
# Clicks are a stronger signal than opens (Resend sees them only if
# the client fetched the tracked link), so we include both —
# counting a click as one vote on equal footing is fine because the
# mode is rank-order, not weighted.
_SIGNAL_EVENT_TYPES: frozenset[str] = frozenset({
    "lead.email_opened",
    "lead.email_clicked",
})

# Default when no personal / tenant signal exists. 09 UTC ≈ 10-11 CET
# — after standup, before the midday slump. Common default across
# open-rate studies for B2B (HubSpot 2022: 9-11am local peak).
DEFAULT_SEND_HOUR_UTC = 9

# Minimum opens/clicks to trust the per-lead mode. A single open is
# easily noise (the lead might have been on a train). 2 distinct
# signals is still tiny but enough to prefer over the default.
MIN_SAMPLES_FOR_PERSONALIZATION = 2

# "Send now" window: if the computed best time is within this many
# minutes of the present, don't delay the follow-up 23h for a handful
# of minutes of alignment.
IMMEDIATE_WINDOW_MINUTES = 30


def compute_best_hour_from_timestamps(
    timestamps: list[datetime],
    *,
    min_samples: int = MIN_SAMPLES_FOR_PERSONALIZATION,
) -> int | None:
    """Pure helper: list of open timestamps → best UTC hour, or None.

    Factored out so the unit test can feed a hand-crafted list and
    assert the exact mode without touching Supabase.

    Ties broken by smallest hour — not ideal but deterministic, and
    the real-world distribution is rarely a perfect tie.
    """
    if len(timestamps) < min_samples:
        return None
    counter: Counter[int] = Counter()
    for ts in timestamps:
        # Normalize to UTC — events.occurred_at is TIMESTAMPTZ so
        # this is a cheap re-anchoring, not a timezone conversion.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        counter[ts.astimezone(timezone.utc).hour] += 1
    if not counter:
        return None
    # Counter.most_common breaks ties by insertion order; sort by
    # (count desc, hour asc) for deterministic output.
    best_hour, _ = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return int(best_hour)


def pick_next_send_time(
    *,
    lead_row: dict[str, Any],
    tenant_row: dict[str, Any] | None,
    now: datetime,
    immediate_window_minutes: int = IMMEDIATE_WINDOW_MINUTES,
) -> datetime:
    """Resolve the actual UTC datetime to schedule the next send at.

    Rules:
      * Pick ``hour`` via the fallback ladder (lead → tenant → 9).
      * If ``hour`` today is still ``immediate_window_minutes`` in the
        future OR within ``immediate_window_minutes`` in the past,
        schedule for today. Otherwise schedule tomorrow.
      * "Within 30min past" handling: if we're at 09:10 and the best
        hour is 09, sending right now is basically on-target — don't
        delay 23h for 10 minutes of drift.

    Returns a tz-aware UTC datetime strictly in the future (or within
    the immediate window).
    """
    hour = (
        _coerce_hour(lead_row.get("best_send_hour"))
        or _coerce_hour(_tenant_default_hour(tenant_row))
        or DEFAULT_SEND_HOUR_UTC
    )
    now_utc = now.astimezone(timezone.utc)
    target_today = now_utc.replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    window = timedelta(minutes=immediate_window_minutes)

    # Inside the "close enough to now" window → send immediately.
    if abs(target_today - now_utc) <= window:
        return now_utc

    # Target still in the future today → schedule today.
    if target_today > now_utc:
        return target_today

    # Target already passed → schedule tomorrow at the same hour.
    return target_today + timedelta(days=1)


def _coerce_hour(value: Any) -> int | None:
    """Accept int / str / None and return a valid 0..23 hour or None."""
    if value is None:
        return None
    try:
        h = int(value)
    except (TypeError, ValueError):
        return None
    return h if 0 <= h <= 23 else None


def _tenant_default_hour(tenant_row: dict[str, Any] | None) -> int | None:
    """Read ``tenants.settings.default_send_hour_utc`` safely.

    The ``settings`` column is free-form JSONB so this is duck-typed
    on purpose — an admin may or may not have set the key.
    """
    if not tenant_row:
        return None
    settings = tenant_row.get("settings") or {}
    if not isinstance(settings, dict):
        return None
    return _coerce_hour(settings.get("default_send_hour_utc"))


# ---------------------------------------------------------------------------
# Rollup — nightly cron entrypoint
# ---------------------------------------------------------------------------


async def run_send_time_rollup(
    *,
    now: datetime | None = None,
    lookback_days: int = LOOKBACK_DAYS,
) -> dict[str, Any]:
    """Refresh ``leads.best_send_hour`` from historical open events.

    One pass over ``events`` for the window, grouped in Python per
    lead, mode computed with ``MIN_SAMPLES_FOR_PERSONALIZATION``.
    Leads that drop below the minimum have their ``best_send_hour``
    reset to NULL — it's better to fall back to the tenant default
    than to anchor on a stale singleton.

    Returns ``{"leads_updated": N, "leads_cleared": M}`` for logging.
    """
    sb = get_service_client()
    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(days=lookback_days)

    # PostgREST "in" filter keeps this single-shot — the event types
    # are whitelisted so we're not pulling the full events table.
    events_res = (
        sb.table("events")
        .select("lead_id, event_type, occurred_at")
        .in_("event_type", sorted(_SIGNAL_EVENT_TYPES))
        .gte("occurred_at", window_start.isoformat())
        .execute()
    )

    by_lead: dict[str, list[datetime]] = {}
    for row in events_res.data or []:
        lid = row.get("lead_id")
        occ = row.get("occurred_at")
        if not lid or not occ:
            continue
        try:
            # Supabase returns ISO-8601 with +00:00 / Z — fromisoformat
            # handles +00:00 natively on 3.11+; replace the "Z" just
            # in case the SDK still normalises to that suffix.
            ts = datetime.fromisoformat(occ.replace("Z", "+00:00"))
        except ValueError:
            continue
        by_lead.setdefault(lid, []).append(ts)

    # Also find leads that used to have a best_send_hour so we can
    # clear stale ones (below the sample threshold after attrition).
    stale_res = (
        sb.table("leads")
        .select("id")
        .not_.is_("best_send_hour", "null")
        .execute()
    )
    previously_set = {row["id"] for row in (stale_res.data or [])}

    updated = 0
    cleared = 0
    for lid, timestamps in by_lead.items():
        best = compute_best_hour_from_timestamps(timestamps)
        try:
            sb.table("leads").update(
                {"best_send_hour": best}
            ).eq("id", lid).execute()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "send_time.rollup.update_failed",
                lead_id=lid,
                err=str(exc),
            )
            continue
        if best is None:
            cleared += 1
        else:
            updated += 1
        previously_set.discard(lid)

    # Any lead that had a value but no longer meets the threshold
    # (all of its opens fell out of the 180d window) → clear.
    for lid in previously_set:
        try:
            sb.table("leads").update(
                {"best_send_hour": None}
            ).eq("id", lid).execute()
            cleared += 1
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "send_time.rollup.clear_failed",
                lead_id=lid,
                err=str(exc),
            )

    log.info(
        "send_time.rollup.done",
        leads_updated=updated,
        leads_cleared=cleared,
        window_start=window_start.isoformat(),
        events_scanned=len(events_res.data or []),
    )
    return {
        "leads_updated": updated,
        "leads_cleared": cleared,
        "events_scanned": len(events_res.data or []),
    }
