"""The daily pick is cap-aware so the 14:30 afternoon catch-up only tops up.

`process_tenant_daily_send` runs twice a day (08:30 morning primary + 14:30
afternoon catch-up). The cap (`daily_target_send_cap`) is a DAILY ceiling, not
a per-run quota, so the second pass must subtract what was already picked today
and request only the remainder — otherwise it would silently double the day's
volume. These tests lock that behaviour.
"""

from __future__ import annotations

from typing import Any

from src.services import daily_pipeline_orchestrator as dp


class _Res:
    def __init__(self, data: Any = None, count: int | None = None) -> None:
        self.data = data
        self.count = count


class _Query:
    def __init__(self, res: _Res) -> None:
        self._res = res

    def select(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def eq(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def gte(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def limit(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def execute(self) -> _Res:
        return self._res


class _Sb:
    """Routes by table: `leads` → picked-today count, `warehouse_health` → row."""

    def __init__(self, picked_today: int) -> None:
        self._picked_today = picked_today

    def table(self, name: str) -> _Query:
        if name == "leads":
            return _Query(_Res(count=self._picked_today))
        if name == "warehouse_health":
            return _Query(
                _Res(
                    data=[
                        {"ready_to_send_count": 400, "expiring_within_3d": 0, "needs_refill": False}
                    ]
                )
            )
        return _Query(_Res(data=[]))


_TENANT = {
    "id": "tenant-1",
    "status": "active",
    "daily_target_send_cap": 50,
    "daily_send_cap_min": 20,
    "daily_send_cap_max": 60,
    "warehouse_buffer_days": 7,
    "lead_expiration_days": 21,
    "atoka_survival_target": 0.2,
}


def _wire(monkeypatch: Any, picked_today: int) -> dict[str, Any]:
    monkeypatch.setattr(dp, "get_service_client", lambda: _Sb(picked_today))

    async def _noop_alerts(**_k: Any) -> None:
        return None

    monkeypatch.setattr(dp, "emit_warehouse_state_alerts", _noop_alerts)

    captured: dict[str, Any] = {}

    def _spy_pick(*, tenant_id: str, n: int) -> list[str]:
        captured["n"] = n
        return [f"lead-{i}" for i in range(n)]

    monkeypatch.setattr(dp, "pick_from_warehouse", _spy_pick)

    async def _noop_enqueue(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(dp, "enqueue", _noop_enqueue)
    return captured


async def test_morning_fresh_day_picks_full_cap(monkeypatch: Any) -> None:
    captured = _wire(monkeypatch, picked_today=0)
    out = await dp.process_tenant_daily_send(_TENANT)
    assert captured["n"] == 50
    assert out["remaining_cap"] == 50


async def test_afternoon_tops_up_to_cap(monkeypatch: Any) -> None:
    # Morning under-delivered: only 30 picked today → afternoon tops up 20.
    captured = _wire(monkeypatch, picked_today=30)
    out = await dp.process_tenant_daily_send(_TENANT)
    assert captured["n"] == 20
    assert out["remaining_cap"] == 20
    assert out["picked_today_before"] == 30


async def test_afternoon_noop_when_cap_already_reached(monkeypatch: Any) -> None:
    # Morning shipped the full cap → afternoon picks nothing (no double batch).
    captured = _wire(monkeypatch, picked_today=50)
    out = await dp.process_tenant_daily_send(_TENANT)
    assert captured["n"] == 0
    assert out["remaining_cap"] == 0
    assert out["picked"] == 0


async def test_never_negative_when_over_cap(monkeypatch: Any) -> None:
    # Defensive: if somehow more than the cap was picked, never go negative.
    captured = _wire(monkeypatch, picked_today=63)
    await dp.process_tenant_daily_send(_TENANT)
    assert captured["n"] == 0
