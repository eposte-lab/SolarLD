"""The panel-paint prompt locks the building geometry.

Operator feedback (2026-06-19): flat industrial/warehouse roofs are always
perfect; the model only "reinvents" the building on COMPLEX pitched roofs
(houses, villas, apartment blocks). The prompt now frames the task as a 2D
overlay on a LOCKED photo and declares the roof geometry FINAL — these tests
guard that wording against regressions.
"""

from __future__ import annotations

from src.services.ai_panel_paint_service import build_paint_prompt


def test_prompt_frames_a_2d_overlay_on_a_locked_photo() -> None:
    low = build_paint_prompt(panel_count=42, kwp=16.8).lower()
    assert "2d overlay" in low
    assert "locked" in low
    # The roof geometry must be declared final / not redrawn.
    assert "geometry is final" in low
    for word in ("redraw", "reshape", "simplify"):
        assert word in low, word
    # The complex-roof failure mode is named explicitly.
    assert "villas" in low
    assert "apartment" in low


def test_prompt_still_anchors_count_and_scale() -> None:
    # Do NOT regress the working constraints (panel count + physical scale)
    # that keep the easy flat-roof case perfect.
    p = build_paint_prompt(panel_count=50, kwp=20)
    assert "~50" in p
    assert "PANEL SCALE" in p
    assert "kWp" in p
