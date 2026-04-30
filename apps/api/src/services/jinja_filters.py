"""Italian-locale Jinja2 filters shared by the quote and practice renderers.

Both ``quote_pdf_renderer`` and ``practice_pdf_renderer`` need the same
EUR/decimal/integer formatting (Italian locale: ``.`` for thousands, ``,``
for decimals). Living in one place avoids drift: a fix to ``money`` shows
up in every PDF the system emits.

Use ``register_italian_filters(env)`` to attach them to a Jinja
``Environment``; the renderers call it once at module load.
"""

from __future__ import annotations

from typing import Any

from jinja2 import Environment


def format_money(value: Any) -> str:
    """Render an EUR amount with thousand separators and no decimals.

    7531 → "7.531"; 1234567 → "1.234.567". The template prepends "€".
    """
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "0"
    return f"{int(round(n)):,}".replace(",", ".")


def format_decimal(value: Any, ndigits: int = 1) -> str:
    """Italian decimal: comma separator, configurable precision."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "0"
    s = f"{n:.{ndigits}f}"
    return s.replace(".", ",")


def format_int(value: Any) -> str:
    """Integer with Italian thousand separators."""
    try:
        return f"{int(round(float(value))):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "0"


def format_date_it(value: Any) -> str:
    """Render a date/datetime/ISO string as ``DD/MM/YYYY``.

    Accepts ``date``, ``datetime``, or ISO-8601 strings. Returns an
    empty string for None/unparseable input — the template always
    handles missing dates gracefully.
    """
    from datetime import date, datetime

    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    try:
        # Try ISO-8601 with optional T separator and timezone.
        s = str(value)
        if "T" in s:
            s = s.split("T", 1)[0]
        parsed = datetime.strptime(s[:10], "%Y-%m-%d")
        return parsed.strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return str(value)


def register_italian_filters(env: Environment) -> None:
    """Attach the Italian-locale filters to ``env``.

    Idempotent — re-registering overwrites with the same callable.
    """
    env.filters["money"] = format_money
    env.filters["decimal_it"] = format_decimal
    env.filters["int_it"] = format_int
    env.filters["date_it"] = format_date_it
