"""Event-loop watchdog — decision logic + heartbeat (no real os._exit)."""

from __future__ import annotations

import asyncio

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


def test_start_watchdog_disabled_with_zero_threshold() -> None:
    wd._started = False
    assert wd.start_watchdog(threshold_seconds=0) is False
    assert wd._started is False
