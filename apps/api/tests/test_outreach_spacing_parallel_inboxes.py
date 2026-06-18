"""Per-lead outreach stagger scales with the number of active inboxes.

Operator request (2026-06-18): start at 08:00 and split the daily batch across
the two inboxes in PARALLEL so the 50/day cap clears in ~half the time. The
stagger is the per-inbox 180s floor divided by N inboxes, clamped to a 60s
minimum so a large fleet can't burst.
"""

from __future__ import annotations

from typing import Any

from src.services import inbox_service
from src.services.daily_pipeline_orchestrator import (
    _OUTREACH_MIN_SPACING_SECONDS,
    _OUTREACH_SPACING_SECONDS,
    _outreach_spacing_for,
)


def test_single_inbox_keeps_full_per_inbox_floor() -> None:
    assert _outreach_spacing_for(1) == _OUTREACH_SPACING_SECONDS  # 190


def test_two_inboxes_halve_the_stagger() -> None:
    # The whole point: 2 inboxes → ~95s apart → both send in parallel.
    assert _outreach_spacing_for(2) == 95
    assert _outreach_spacing_for(2) < _OUTREACH_SPACING_SECONDS


def test_three_inboxes_ceil_divide() -> None:
    assert _outreach_spacing_for(3) == 64  # ceil(190/3)


def test_many_inboxes_clamped_to_floor() -> None:
    assert _outreach_spacing_for(10) == _OUTREACH_MIN_SPACING_SECONDS  # 60, not 19


def test_zero_or_negative_is_safe() -> None:
    # Never divide by zero — degrade to the single-inbox cadence.
    assert _outreach_spacing_for(0) == _OUTREACH_SPACING_SECONDS
    assert _outreach_spacing_for(-1) == _OUTREACH_SPACING_SECONDS


class _Res:
    def __init__(self, count: int) -> None:
        self.count = count
        self.data: list[Any] = []


class _Q:
    def __init__(self, count: int) -> None:
        self._count = count

    def select(self, *_a: Any, **_k: Any) -> _Q:
        return self

    def eq(self, *_a: Any, **_k: Any) -> _Q:
        return self

    def execute(self) -> _Res:
        return _Res(self._count)


class _Sb:
    def __init__(self, count: int | Exception) -> None:
        self._count = count

    def table(self, _n: str) -> _Q:
        if isinstance(self._count, Exception):
            raise self._count
        return _Q(self._count)


async def test_count_active_inboxes_returns_count() -> None:
    assert await inbox_service.count_active_inboxes(_Sb(2), "t") == 2


async def test_count_active_inboxes_min_one() -> None:
    assert await inbox_service.count_active_inboxes(_Sb(0), "t") == 1


async def test_count_active_inboxes_fails_safe_to_one() -> None:
    assert await inbox_service.count_active_inboxes(_Sb(RuntimeError("boom")), "t") == 1
