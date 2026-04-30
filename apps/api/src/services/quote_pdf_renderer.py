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

from src.services.jinja_filters import register_italian_filters

# Templates live next to the code, not in /tmp or a config-driven path —
# keeps the renderer self-contained and easy to test.
_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "quote_templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

# Italian-locale filters (money/decimal_it/int_it/date_it) shared with
# the practice_pdf_renderer — see services/jinja_filters.py.
register_italian_filters(_env)


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
