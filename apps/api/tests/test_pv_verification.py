"""Tests for the fail-closed existing-PV gate logic.

Covers the two correctness-critical pure pieces:
  * ``verify_existing_pv`` — the tri-state verdict (checked / has_pv / confidence)
    that decides accept vs reject vs HOLD.
  * ``roof_pv_verified_clean`` — the "may this lead send?" predicate.

The vision + Mapbox calls are monkeypatched so no network is touched.
"""

from __future__ import annotations

from types import SimpleNamespace

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


def test_existing_pv_threshold_was_raised() -> None:
    # Locked at the post-incident value: the vision confidently mis-read big
    # commercial roofs as panelled and auto-blacklisted the best leads.
    assert cvs.EXISTING_PV_MIN_CONFIDENCE >= 0.85


@pytest.mark.asyncio
async def test_mid_confidence_panels_now_held_not_acted(monkeypatch) -> None:
    # 0.7 "has panels" was ACTED on under the old 0.6 threshold (→ blacklist);
    # after raising to 0.85 it is NOT trusted → checked=False → held UNVERIFIED,
    # never blacklisted.
    _stub_detect(monkeypatch, {"has_existing_pv": True, "confidence": 0.7})
    v = await cvs.verify_existing_pv(40.0, 14.0, area_sqm=500)
    assert v.checked is False


@pytest.mark.asyncio
async def test_reverify_excludes_already_escalated_leads(monkeypatch) -> None:
    """Regression (2026-08 token blowout): the candidate query MUST exclude
    leads already escalated to a human (operator_review_status='held'), while
    keeping never-escalated (NULL) ones. Without the exclusion the ~20 held
    roofs monopolised every 20-min tick and got a fresh Claude vision call
    each time, forever — never resolving, just burning tokens."""
    from src.services import pv_verification_service as pv

    seen: dict[str, object] = {}

    class _Q:
        def table(self, name):  # noqa: ANN001
            return self

        def select(self, *a, **k):  # noqa: ANN002, ANN003
            return self

        def eq(self, col, val):  # noqa: ANN001
            seen.setdefault("eq", []).append((col, val))
            return self

        def or_(self, expr):  # noqa: ANN001
            seen["or_"] = expr
            return self

        def order(self, *a, **k):  # noqa: ANN002, ANN003
            return self

        def limit(self, *a, **k):  # noqa: ANN002, ANN003
            return self

        def execute(self):
            return SimpleNamespace(data=[])  # no leads → no vision calls

    monkeypatch.setattr(pv, "get_service_client", lambda: _Q())

    result = await pv.run_pv_reverification()

    assert result["escalated"] == 0  # empty batch, nothing processed
    or_expr = seen.get("or_", "")
    assert "operator_review_status.neq.held" in or_expr  # 'held' excluded
    assert "operator_review_status.is.null" in or_expr  # never-escalated eligible


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
