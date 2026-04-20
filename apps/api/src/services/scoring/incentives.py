"""Incentives subscore — how favorable is the subject's region right now?

Inputs: the list of `regional_incentives` rows for the subject's region
(filtered by active=true in the caller) plus the subject's B2B/B2C flag.
The scorer ignores rows whose `target` doesn't match (e.g. a B2B-only
incentive on a B2C subject).

Score philosophy:
  * zero applicable incentives → low baseline (20)
  * one  → 50
  * two  → 75
  * three+ → 90
  * +10 urgency bonus if any incentive expires within 90 days

The real-world effect of incentives is rarely linear, but the weight
knob in `scoring_weights.weights.incentives` lets the operator dampen it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

_URGENCY_WINDOW = timedelta(days=90)


def incentives_score(
    incentives: list[dict[str, Any]],
    subject_type: str,
    *,
    today: date | None = None,
) -> int:
    """Return 0..100.

    ``incentives`` should already be filtered to the subject's region
    with ``active=true``. This function applies the target-type filter +
    deadline urgency bonus.
    """
    if today is None:
        today = datetime.utcnow().date()

    subject_type = (subject_type or "unknown").lower()
    applicable: list[dict[str, Any]] = []
    for inc in incentives or []:
        target = (inc.get("target") or "both").lower()
        if target not in ("both", subject_type):
            continue
        applicable.append(inc)

    count = len(applicable)
    if count == 0:
        return 20
    if count == 1:
        base = 50
    elif count == 2:
        base = 75
    else:
        base = 90

    # Urgency bonus if any applicable incentive expires soon.
    for inc in applicable:
        deadline = _parse_date(inc.get("deadline"))
        if deadline and today <= deadline <= today + _URGENCY_WINDOW:
            base = min(100, base + 10)
            break

    return base


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None
