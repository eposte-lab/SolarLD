"""Task 18 + 19 — Send windows and human-like inter-send delay.

Send windows (Europe/Rome local time):
  Mon–Fri  08:00 – 12:00  (morning block)
  Mon–Fri  14:00 – 18:00  (afternoon block)

Rationale: cold B2B emails sent during office hours have higher open rates
and feel less robotic. Weekend sends are not just ineffective — they also
look automated to Gmail's spam classifiers, so skipping them improves the
inbox's sender reputation over time.

Human-like inter-send delay (Task 19):
Each sending inbox must sit idle for at least ``MIN_INTER_SEND_SECONDS`` (180 s)
between consecutive sends from the same address. This prevents the
"machine-gun" pattern (10 emails from the same inbox within 60 seconds)
that Gmail's ML flags as bulk-automated.

With 12 shadow-domain inboxes and the LRU rotation already implemented by
``inbox_service.pick_and_claim`` (ORDER BY last_sent_at ASC NULLS FIRST),
the fleet distributes sends naturally at 3-5-minute intervals at steady-state
load. The ``MIN_INTER_SEND_SECONDS`` floor is an explicit safety net for
bursts.

Integration points
------------------
* ``outreach.py`` calls ``is_within_send_window()`` right after channel
  routing, before the rate-limit checks. If outside the window the send is
  skipped gracefully; the follow-up cron re-evaluates the next day.

* ``inbox_service.pick_and_claim()`` calls ``is_inbox_human_delay_ok(inbox)``
  inside the Python-side availability filter. Inboxes that last sent within
  ``MIN_INTER_SEND_SECONDS`` are temporarily excluded and the next LRU inbox
  is tried instead.

Both functions accept an optional ``now`` parameter for deterministic testing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..core.logging import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SEND_WINDOW_TZ = "Europe/Rome"

# Send windows in *local* time (inclusive start, exclusive end).
# Each tuple is (hour_start, hour_end).
SEND_WINDOWS_LOCAL: tuple[tuple[int, int], ...] = (
    (8, 12),   # 08:00–12:00 morning block
    (14, 18),  # 14:00–18:00 afternoon block
)

# Minimum seconds an inbox must be idle before being used again.
# This is the floor; with many inboxes the natural LRU gap is larger.
MIN_INTER_SEND_SECONDS: int = 180  # 3 minutes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_within_send_window(now: datetime | None = None) -> bool:
    """Return True iff the current moment falls within a valid send window.

    The check is performed in Europe/Rome local time.  Weekends (Sat/Sun)
    always return False.  If the ``zoneinfo`` module is unavailable (Python
    < 3.9 without backport), falls back to a fixed UTC+1 offset (CET winter
    time). This is acceptable: a 1-hour drift on DST changeover is not
    business-critical.

    Args:
        now: UTC-aware datetime to test. Defaults to ``datetime.now(timezone.utc)``.

    Returns:
        True when Europe/Rome local hour is Monday–Friday 08:00–12:00 or
        14:00–18:00; False otherwise.

    Examples::

        >>> from datetime import datetime, timezone
        >>> # Monday 10:30 UTC → 11:30 CET → inside window
        >>> is_within_send_window(datetime(2026, 4, 27, 8, 30, tzinfo=timezone.utc))
        True
        >>> # Saturday, any time
        >>> is_within_send_window(datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc))
        False
    """
    try:
        import zoneinfo  # stdlib Python 3.9+
        tz = zoneinfo.ZoneInfo(SEND_WINDOW_TZ)
    except (ImportError, KeyError):
        # Fallback: CET = UTC+1.  Rough but safe for our use case.
        from datetime import timedelta
        tz = timezone(timedelta(hours=1))

    if now is None:
        now = datetime.now(timezone.utc)

    local = now.astimezone(tz)

    # Weekday: 0 = Monday … 6 = Sunday.  Reject weekends.
    if local.weekday() >= 5:
        return False

    hour = local.hour
    for start, end in SEND_WINDOWS_LOCAL:
        if start <= hour < end:
            return True

    return False


def is_inbox_human_delay_ok(
    inbox: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    """Return True if the inbox has been idle long enough to send again.

    Reads ``inbox["last_sent_at"]`` (ISO-8601 string, set atomically by
    ``pick_and_claim`` on every successful claim). Returns True when:

    * The inbox has never sent before (``last_sent_at`` is None / empty).
    * At least ``MIN_INTER_SEND_SECONDS`` (180 s) have elapsed since the
      last send.

    This prevents back-to-back sends from the same inbox which look robotic
    to Gmail's spam classifier.  The function is a pure predicate with no
    side effects — safe to call in the filter loop of ``pick_and_claim``.

    Args:
        inbox: An inbox row dict from ``tenant_inboxes`` (must contain
               ``last_sent_at``).
        now:   UTC-aware datetime. Defaults to ``datetime.now(timezone.utc)``.

    Returns:
        True when the inbox may be used for the next send; False when it
        is still in the human-delay cooldown window.
    """
    last_sent_str = inbox.get("last_sent_at")
    if not last_sent_str:
        return True  # Brand-new inbox: no prior send → no delay needed.

    if now is None:
        now = datetime.now(timezone.utc)

    try:
        last_sent = datetime.fromisoformat(
            str(last_sent_str).replace("Z", "+00:00")
        )
        if last_sent.tzinfo is None:
            last_sent = last_sent.replace(tzinfo=timezone.utc)
        elapsed = (now - last_sent).total_seconds()
        return elapsed >= MIN_INTER_SEND_SECONDS
    except (ValueError, TypeError):
        # Unparseable timestamp → fail-open (allow send) so a corrupted
        # last_sent_at in a single inbox doesn't block the entire fleet.
        log.debug(
            "send_window.unparseable_last_sent_at",
            inbox_id=inbox.get("id"),
            last_sent_at=last_sent_str,
        )
        return True
