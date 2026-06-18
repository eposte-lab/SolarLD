"""Event-loop watchdog — decision logic + heartbeat (no real os._exit)."""

from __future__ import annotations

import asyncio
from typing import Any

from src.services import worker_watchdog as wd


def test_should_exit_only_past_threshold() -> None:
    assert wd.should_exit(stale_seconds=200, threshold_seconds=180) is True
    assert wd.should_exit(stale_seconds=180, threshold_seconds=180) is False
    assert wd.should_exit(stale_seconds=5, threshold_seconds=180) is False


def test_should_exit_disabled_when_threshold_zero() -> None:
    # threshold 0 → watchdog disabled, never exits even if very stale.
    assert wd.should_exit(stale_seconds=10_000, threshold_seconds=0) is False


def test_beat_resets_staleness() -> None:
    wd._beat()
    assert wd.seconds_since_beat() < 1.0


def test_seconds_since_beat_grows_when_loop_stalls() -> None:
    wd._beat()
    # Simulate a wedged loop: the heartbeat stamp is far in the past.
    wd._last_beat -= 500
    assert wd.seconds_since_beat() > 400


async def test_heartbeat_loop_keeps_beat_fresh() -> None:
    wd._last_beat -= 500  # pretend it went stale
    task = asyncio.create_task(wd._heartbeat_loop(0.01))
    try:
        await asyncio.sleep(0.05)  # let the loop beat a few times
        assert wd.seconds_since_beat() < 1.0  # fresh again
    finally:
        task.cancel()


async def test_heartbeat_rearms_faulthandler_when_threshold_set(monkeypatch: Any) -> None:
    # The GIL-proof path: every beat must re-arm faulthandler's C-level timer
    # so a GIL-holding wedge still gets killed on schedule (2026-06-18).
    armed: list[tuple[float, bool]] = []

    def _fake_later(timeout: float, *, exit: bool = False) -> None:
        armed.append((timeout, exit))

    monkeypatch.setattr(wd.faulthandler, "dump_traceback_later", _fake_later)
    task = asyncio.create_task(wd._heartbeat_loop(0.01, watchdog_threshold=180.0))
    try:
        await asyncio.sleep(0.05)
    finally:
        task.cancel()
    assert armed, "faulthandler timer was never armed"
    assert all(t == 180.0 and ex is True for (t, ex) in armed)


async def test_heartbeat_does_not_arm_faulthandler_without_threshold(monkeypatch: Any) -> None:
    # No threshold (e.g. the existing single-arg test path) → never arm the
    # process-killing timer, so unit tests can run the loop safely.
    armed: list[Any] = []
    monkeypatch.setattr(
        wd.faulthandler, "dump_traceback_later", lambda *a, **k: armed.append((a, k))
    )
    task = asyncio.create_task(wd._heartbeat_loop(0.01))
    try:
        await asyncio.sleep(0.03)
    finally:
        task.cancel()
    assert armed == []


def test_start_watchdog_disabled_with_zero_threshold() -> None:
    wd._started = False
    assert wd.start_watchdog(threshold_seconds=0) is False
    assert wd._started is False
