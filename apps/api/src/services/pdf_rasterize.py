"""Pure helper to rasterize PDF pages to PNG bytes.

Used by both bolletta OCR (`bolletta_ocr_service.py`) and practice
document extraction (`practice_extraction_service.py`) — Claude Vision
only accepts raster image MIME types, not PDF.

`pypdfium2` is already pinned in `pyproject.toml`. We render at 200 DPI
which is the sweet spot between text legibility (Vision parses small
fonts on Italian utility bills cleanly at this resolution) and memory
footprint (~5-10 MB PNG for an A4 page).
"""

from __future__ import annotations

import io

import pypdfium2 as pdfium

DEFAULT_DPI = 200
DEFAULT_SCALE = DEFAULT_DPI / 72  # pdfium uses 72 DPI as the unit baseline


def rasterise_pdf_first_page(
    pdf_bytes: bytes, *, scale: float = DEFAULT_SCALE
) -> bytes | None:
    """Render page 1 of a PDF to PNG bytes.

    Returns None on:
      - corrupted PDF (pdfium constructor raises)
      - empty PDF (0 pages)
      - PIL/save failure
    Caller should fall back to a manual-entry path on None.
    """
    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
    except Exception:  # noqa: BLE001 — anything pdfium throws → unreadable
        return None

    try:
        if len(pdf) == 0:
            return None
        page = pdf[0]
        try:
            bitmap = page.render(scale=scale)
            pil = bitmap.to_pil()
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:  # noqa: BLE001
            return None
    finally:
        try:
            pdf.close()
        except Exception:  # noqa: BLE001
            pass
