"""WeasyPrint-backed HTML→PDF renderer for the preventivo template.

Why WeasyPrint and not headless Chromium / wkhtmltopdf:
  * Pure Python, runs in the same process as the API → no subprocess
    fork tax, no extra container, no Chromium binary to ship.
  * CSS Paged Media support is first-class — `@page`, page-breaks,
    headers/footers via running elements all work out of the box. The
    preventivo template uses these heavily.
  * Deterministic output — same HTML in, same PDF bytes out (modulo
    embedded fonts). Nice for snapshot testing.

Why NOT premailer (the email pipeline uses it): premailer inlines CSS
into ``style="..."`` attributes, which is necessary for some email
clients but actively harmful here — WeasyPrint resolves stylesheets
natively and benefits from the proper cascade. Inlined styles also
break ``@page``-level rules.

Sync-only: WeasyPrint has no async API and is CPU-heavy. Callers MUST
offload it via ``asyncio.to_thread`` (the quote_service does this).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

# Templates live next to the code, not in /tmp or a config-driven path —
# keeps the renderer self-contained and easy to test.
_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "quote_templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

# Format helpers exposed as Jinja filters so the template doesn't have
# to import Python — they keep the .j2 file readable.

def _format_money(value: object) -> str:
    """Render an EUR amount with thousand separators and no decimals.

    7531 → "7.531"; 1234567 → "1.234.567". Caller prepends "€".
    """
    try:
        n = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "0"
    return f"{int(round(n)):,}".replace(",", ".")


def _format_decimal(value: object, ndigits: int = 1) -> str:
    """Italian decimal: comma separator, configurable precision."""
    try:
        n = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "0"
    s = f"{n:.{ndigits}f}"
    return s.replace(".", ",")


def _format_int(value: object) -> str:
    try:
        return f"{int(round(float(value))):,}".replace(",", ".")  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "0"


_env.filters["money"] = _format_money
_env.filters["decimal_it"] = _format_decimal
_env.filters["int_it"] = _format_int


def render_quote_pdf(context: dict[str, Any]) -> bytes:
    """Render ``preventivo.html.j2`` with ``context`` to PDF bytes.

    ``base_url`` is set so relative ``<img src="...">`` paths resolve
    against the templates dir (where any bundled assets live), while
    absolute https URLs continue to resolve normally.
    """
    # Lazy import: keeps non-PDF code paths importable even if the
    # native dependencies aren't installed (e.g. in unit tests that
    # mock the renderer).
    from weasyprint import HTML  # type: ignore[import-not-found]

    template = _env.get_template("preventivo.html.j2")
    html_str = template.render(**context)
    return HTML(string=html_str, base_url=str(_TEMPLATES_DIR)).write_pdf()
