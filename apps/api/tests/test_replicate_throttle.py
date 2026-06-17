"""Tests for the shared Replicate prediction-create throttle.

The throttle is what stops the render pipeline from self-inflicting HTTP 429s:
it serialises + spaces every ``POST /predictions`` to stay under the
per-account "creating predictions" limit (~6/min, burst 1). These tests assert
the spacing math without actually sleeping (``asyncio.sleep`` is stubbed).
"""

from __future__ import annotations

import pytest

from src.core.config import settings
from src.services import replicate_throttle


@pytest.fixture(autouse=True)
def _reset_throttle():
    # Each test starts with a clean last-create timestamp.
    replicate_throttle._last_create_at = 0.0
    yield
    replicate_throttle._last_create_at = 0.0


def _record_sleeps(monkeypatch) -> list[float]:
    slept: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(replicate_throttle.asyncio, "sleep", _fake_sleep)
    return slept


async def test_first_create_proceeds_immediately(monkeypatch):
    slept = _record_sleeps(monkeypatch)
    monkeypatch.setattr(settings, "replicate_creates_per_min", 6)

    await replicate_throttle.acquire_create_slot()

    assert slept == []  # the very first create never waits


async def test_second_create_is_spaced(monkeypatch):
    slept = _record_sleeps(monkeypatch)
    monkeypatch.setattr(settings, "replicate_creates_per_min", 6)  # 60/6 = 10s

    await replicate_throttle.acquire_create_slot()
    await replicate_throttle.acquire_create_slot()

    assert len(slept) == 1
    # ~10s minus the microseconds elapsed between the two back-to-back calls.
    assert 9.0 <= slept[0] <= 10.0


async def test_nonpositive_rate_falls_back_to_six_per_min(monkeypatch):
    slept = _record_sleeps(monkeypatch)
    monkeypatch.setattr(settings, "replicate_creates_per_min", 0)

    await replicate_throttle.acquire_create_slot()
    await replicate_throttle.acquire_create_slot()

    assert len(slept) == 1
    assert 9.0 <= slept[0] <= 10.0  # fallback 6/min → 10s spacing


async def test_higher_rate_shortens_spacing(monkeypatch):
    slept = _record_sleeps(monkeypatch)
    monkeypatch.setattr(settings, "replicate_creates_per_min", 60)  # 1s spacing

    await replicate_throttle.acquire_create_slot()
    await replicate_throttle.acquire_create_slot()

    assert len(slept) == 1
    assert 0.5 <= slept[0] <= 1.0
