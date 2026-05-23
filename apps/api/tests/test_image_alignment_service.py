"""Tests for the before/after ECC alignment service.

We synthesise a structured "aerial" scene, apply a known geometric drift
(translation / scale) to mimic nano-banana's framing wobble, then assert
the alignment pulls the drifted frame back onto the reference. The metric
is mean-absolute-difference (MAD) over the region OUTSIDE a simulated
"roof change" patch — alignment must lower it substantially without
degrading an already-aligned pair.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from src.services.image_alignment_service import align_after_to_before

cv2 = pytest.importorskip("cv2")

W, H = 1536, 864
# Simulated roof patch that DIFFERS between before/after (panels). Excluded
# from the MAD so we measure context alignment, not the intended change.
ROOF = (slice(220, 380), slice(320, 680))


def _scene() -> np.ndarray:
    """A structured, blurred pseudo-aerial: buildings, roads, a field."""
    img = Image.new("RGB", (W, H), (70, 90, 75))
    d = ImageDraw.Draw(img)
    for x0, y0, x1, y1, col in [
        (300, 200, 700, 400, (150, 140, 130)),
        (0, 600, W, 650, (110, 110, 115)),
        (800, 100, 860, 760, (120, 120, 125)),
        (1000, 300, 1300, 520, (160, 150, 140)),
        (150, 700, 400, 820, (90, 130, 90)),
    ]:
        d.rectangle([x0, y0, x1, y1], fill=col)
    return np.asarray(img.filter(ImageFilter.GaussianBlur(2)))


def _png(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, format="PNG")
    return buf.getvalue()


def _drift(base: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    out = cv2.warpAffine(base, matrix, (W, H), borderMode=cv2.BORDER_REPLICATE).copy()
    out[ROOF] = (30, 30, 35)  # paint the "panels"
    return out


def _mad_outside_roof(a: np.ndarray, b: np.ndarray) -> float:
    mask = np.ones((H, W), bool)
    mask[ROOF] = False
    return float(np.abs(a.astype(int)[mask] - b.astype(int)[mask]).mean())


@pytest.mark.parametrize(
    "matrix",
    [
        np.float32([[1, 0, 18], [0, 1, 11]]),  # translation right+down
        np.float32([[1, 0, -9], [0, 1, 6]]),  # translation left+down
        np.float32([[1.04, 0, 5], [0, 1.04, -7]]),  # scale + translation
    ],
)
def test_alignment_recovers_known_drift(matrix: np.ndarray) -> None:
    base = _scene()
    drifted = _drift(base, matrix)

    aligned_bytes = align_after_to_before(_png(base), _png(drifted))
    aligned = np.asarray(Image.open(io.BytesIO(aligned_bytes)).convert("RGB"))

    mad_before = _mad_outside_roof(drifted, base)
    mad_after = _mad_outside_roof(aligned, base)
    # Alignment must cut the residual misalignment by at least half.
    assert mad_after < mad_before * 0.5, (mad_before, mad_after)


def test_identity_pair_not_degraded() -> None:
    """An already-aligned pair must come back essentially unchanged."""
    base = _scene()
    aligned_bytes = align_after_to_before(_png(base), _png(base))
    aligned = np.asarray(Image.open(io.BytesIO(aligned_bytes)).convert("RGB"))
    assert _mad_outside_roof(aligned, base) < 0.5


def test_bad_input_returns_after_unchanged() -> None:
    """Garbage bytes must never raise — the after is returned as-is."""
    after = b"not-a-png"
    assert align_after_to_before(b"also-not-a-png", after) == after


def test_mismatched_sizes_handled() -> None:
    """Different-sized inputs are resized defensively, not crashed."""
    base = _scene()
    small = Image.fromarray(base, "RGB").resize((1280, 720), Image.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, format="PNG")
    out = align_after_to_before(_png(base), buf.getvalue())
    # Output decodes as a valid image at the reference size.
    img = Image.open(io.BytesIO(out)).convert("RGB")
    assert img.size == (W, H)
