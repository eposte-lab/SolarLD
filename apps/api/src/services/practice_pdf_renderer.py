"""WeasyPrint-backed renderer for GSE practice documents.

Same shape as ``quote_pdf_renderer`` — different templates dir. We
use one Jinja ``Environment`` per renderer rather than a shared one
because the loader path differs and Jinja caches templates by name
inside the env. Sharing would mean ambiguous template lookups when
both modules ship a ``base.html.j2`` (likely once we expand to more
documents).

Italian-locale filters live in ``jinja_filters.py`` and are registered
on both envs — keep money/decimal formatting identical across PDFs.

Sync-only, CPU-heavy: callers MUST offload via ``asyncio.to_thread``.
The arq render task does this.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.services.jinja_filters import register_italian_filters

# Templates live next to the code, mirroring the quote_templates layout.
_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "practice_templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
register_italian_filters(_env)


# Supported template codes.
#
# Sprint 1 (live): dichiarazione di conformità DM 37/08 + comunicazione
# fine lavori al Comune.
#
# Sprint 2 additions:
#   - modello_unico_p1     Modello Unico per la realizzazione,
#                          connessione ed esercizio di impianti FV
#                          ≤200 kW, Parte I (pre-lavori).
#   - modello_unico_p2     Modello Unico, Parte II (as-built).
#   - schema_unifilare     Schema elettrico unifilare CEI 0-21 (SVG
#                          inline, allegato obbligatorio al MU e TICA).
#   - attestazione_titolo  Modulo ATR — Attestazione titolo richiedente
#                          la connessione (allegato MU per IRETI/Unareti).
#   - tica_areti           Allegato 1 della delibera 109/2021/R/eel —
#                          istanza di accesso (Areti S.p.A. — Roma).
#   - transizione_50_ex_ante     Cert. ex-ante credito d'imposta T.5.0
#                                (Allegato VIII al DM Transizione 5.0).
#   - transizione_50_ex_post     Cert. ex-post (Allegato X).
#   - transizione_50_attestazione  Att. perizia + cert. contabile
#                                  (Allegato V).
SUPPORTED_TEMPLATE_CODES = frozenset(
    {
        "dm_37_08",
        "comunicazione_comune",
        "modello_unico_p1",
        "modello_unico_p2",
        "schema_unifilare",
        "attestazione_titolo",
        "tica_areti",
        "transizione_50_ex_ante",
        "transizione_50_ex_post",
        "transizione_50_attestazione",
    }
)


def render_practice_pdf(template_code: str, context: dict[str, Any]) -> bytes:
    """Render ``{template_code}.html.j2`` with ``context`` to PDF bytes.

    Raises ``ValueError`` for unknown template codes — keeps typos from
    silently rendering an empty PDF (Jinja would produce a blank page
    if the template file simply doesn't exist).
    """
    if template_code not in SUPPORTED_TEMPLATE_CODES:
        raise ValueError(
            f"unsupported practice template_code: {template_code!r} "
            f"(supported: {sorted(SUPPORTED_TEMPLATE_CODES)})"
        )

    # Lazy import — keeps the module importable in test environments
    # that don't have the WeasyPrint native deps installed (Pango,
    # Cairo, GDK-Pixbuf). The actual render-path test importskips when
    # weasyprint is missing.
    from weasyprint import HTML  # type: ignore[import-not-found]

    template = _env.get_template(f"{template_code}.html.j2")
    html_str = template.render(**context)
    return HTML(string=html_str, base_url=str(_TEMPLATES_DIR)).write_pdf()
