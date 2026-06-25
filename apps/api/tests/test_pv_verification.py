"""Tests for the fail-closed existing-PV gate logic.

Covers the two correctness-critical pure pieces:
  * ``verify_existing_pv`` — the tri-state verdict (checked / has_pv / confidence)
    that decides accept vs reject vs HOLD.
  * ``roof_pv_verified_clean`` — the "may this lead send?" predicate.

The vision + Mapbox calls are monkeypatched so no network is touched.
"""

from __future__ import annotations

import pytest

from src.services import claude_vision_service as cvs
from src.services.pv_verification_service import roof_pv_verified_clean

# ---------------------------------------------------------------------------
# roof_pv_verified_clean — the send/promote predicate
# ---------------------------------------------------------------------------


def test_verified_clean_true_when_checked_and_no_panels() -> None:
    roof = {"existing_pv_checked_at": "2026-06-25T08:00:00+00:00", "has_existing_pv": False}
    assert roof_pv_verified_clean(roof) is True


def test_verified_clean_false_when_panels() -> None:
    roof = {"existing_pv_checked_at": "2026-06-25T08:00:00+00:00", "has_existing_pv": True}
    assert roof_pv_verified_clean(roof) is False


def test_verified_clean_false_when_never_checked() -> None:
    # The Olimpico failure mode: has_existing_pv=false BUT never confidently
    # verified (checked_at NULL) must NOT count as clean.
    roof = {"existing_pv_checked_at": None, "has_existing_pv": False}
    assert roof_pv_verified_clean(roof) is False


def test_verified_clean_false_on_missing_or_empty_roof() -> None:
    assert roof_pv_verified_clean({}) is False
    assert roof_pv_verified_clean(None) is False


# ---------------------------------------------------------------------------
# verify_existing_pv — the tri-state verdict (FAIL-CLOSED)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stub_mapbox(monkeypatch) -> None:
    # Avoid needing a Mapbox token / building a real URL.
    monkeypatch.setattr(
        cvs.mapbox_service,
        "build_static_satellite_url",
        lambda lat, lng, **kw: "https://example/tile.png",
    )


def _stub_detect(monkeypatch, result) -> None:
    async def _fake(image_url, lat, lng, *, model=None):  # noqa: ANN001
        return result

    monkeypatch.setattr(cvs, "detect_existing_pv", _fake)


@pytest.mark.asyncio
async def test_verdict_confident_panels(monkeypatch) -> None:
    _stub_detect(monkeypatch, {"has_existing_pv": True, "confidence": 0.9})
    v = await cvs.verify_existing_pv(40.0, 14.0, area_sqm=500)
    assert (v.checked, v.has_pv) == (True, True)


@pytest.mark.asyncio
async def test_verdict_confident_clean(monkeypatch) -> None:
    _stub_detect(monkeypatch, {"has_existing_pv": False, "confidence": 0.85})
    v = await cvs.verify_existing_pv(40.0, 14.0, area_sqm=500)
    assert (v.checked, v.has_pv) == (True, False)


@pytest.mark.asyncio
async def test_verdict_low_confidence_is_unverified(monkeypatch) -> None:
    # Below EXISTING_PV_MIN_CONFIDENCE → NOT trusted → checked=False → HELD.
    _stub_detect(monkeypatch, {"has_existing_pv": False, "confidence": 0.3})
    v = await cvs.verify_existing_pv(40.0, 14.0, area_sqm=500)
    assert v.checked is False


@pytest.mark.asyncio
async def test_verdict_low_confidence_panels_also_unverified(monkeypatch) -> None:
    # A low-confidence "panels" is also untrusted → held (not auto-rejected).
    _stub_detect(monkeypatch, {"has_existing_pv": True, "confidence": 0.4})
    v = await cvs.verify_existing_pv(40.0, 14.0, area_sqm=500)
    assert (v.checked, v.has_pv) == (False, False)


@pytest.mark.asyncio
async def test_verdict_vision_none_is_unverified(monkeypatch) -> None:
    # Vision couldn't run / unparseable → UNVERIFIED (fail closed → hold).
    _stub_detect(monkeypatch, None)
    v = await cvs.verify_existing_pv(40.0, 14.0, area_sqm=500)
    assert v.checked is False


@pytest.mark.asyncio
async def test_building_has_existing_pv_facade(monkeypatch) -> None:
    # Legacy bool|None facade: True only on a confident panels verdict, None
    # when not confidently decided (so legacy fail-open callers keep the lead).
    _stub_detect(monkeypatch, {"has_existing_pv": True, "confidence": 0.9})
    assert await cvs.building_has_existing_pv(40.0, 14.0) is True
    _stub_detect(monkeypatch, {"has_existing_pv": False, "confidence": 0.9})
    assert await cvs.building_has_existing_pv(40.0, 14.0) is False
    _stub_detect(monkeypatch, {"has_existing_pv": True, "confidence": 0.3})
    assert await cvs.building_has_existing_pv(40.0, 14.0) is None
    _stub_detect(monkeypatch, None)
    assert await cvs.building_has_existing_pv(40.0, 14.0) is None
