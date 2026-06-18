"""Regression: the daily send PICK must run even if warehouse telemetry fails.

Bug (2026-06-16/17): `process_tenant_daily_send` read `warehouse_health`
and emitted in-app alerts BEFORE the pick, both unguarded. When the alert
write raised, the exception bubbled to `run_daily_orchestrator`, which
caught it per-tenant and skipped the tenant — so the pick never ran and
zero outreach went out for two days while 150 leads sat in `ready_to_send`.

The telemetry block is now wrapped so a failure there can never prevent
today's pick. These tests lock that in.
"""

from __future__ import annotations

from typing import Any

from src.services import daily_pipeline_orchestrator as dp


class _FakeResult:
    def __init__(self, data: Any) -> None:
        self.data = data


class _FakeQuery:
    """Minimal stand-in for the supabase query builder chain."""

    def __init__(self, data: Any) -> None:
        self._data = data

    def select(self, *_a: Any, **_k: Any) -> _FakeQuery:
        return self

    def eq(self, *_a: Any, **_k: Any) -> _FakeQuery:
        return self

    def limit(self, *_a: Any, **_k: Any) -> _FakeQuery:
        return self

    def execute(self) -> _FakeResult:
        return _FakeResult(self._data)


class _FakeSb:
    def __init__(self, health_row: dict[str, Any]) -> None:
        self._health_row = health_row

    def table(self, _name: str) -> _FakeQuery:
        return _FakeQuery([self._health_row])


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


def _wire_common_spies(monkeypatch: Any) -> dict[str, Any]:
    """Patch pick + enqueue and return the dict the pick spy records into."""
    picked_with: dict[str, Any] = {}

    def _spy_pick(*, tenant_id: str, n: int) -> list[str]:
        picked_with["tenant_id"] = tenant_id
        picked_with["n"] = n
        return []

    async def _noop_enqueue(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(dp, "pick_from_warehouse", _spy_pick)
    monkeypatch.setattr(dp, "enqueue", _noop_enqueue)
    return picked_with


async def test_pick_runs_even_when_alert_emission_raises(monkeypatch: Any) -> None:
    """A throwing alert write must NOT skip the pick (the core bug)."""
    monkeypatch.setattr(
        dp,
        "get_service_client",
        lambda: _FakeSb(
            {"ready_to_send_count": 150, "expiring_within_3d": 0, "needs_refill": False}
        ),
    )

    async def _boom(**_k: Any) -> None:
        raise RuntimeError("alerts table write failed")

    monkeypatch.setattr(dp, "emit_warehouse_state_alerts", _boom)
    picked_with = _wire_common_spies(monkeypatch)

    out = await dp.process_tenant_daily_send(_TENANT)

    # The pick ran despite the alert exception — that is the whole fix.
    assert picked_with == {"tenant_id": "tenant-1", "n": 50}
    assert out["picked"] == 0


async def test_pick_runs_even_when_health_read_raises(monkeypatch: Any) -> None:
    """A throwing warehouse_health read must NOT skip the pick either."""

    class _ExplodingSb:
        def table(self, _name: str) -> Any:
            raise RuntimeError("warehouse_health view error")

    monkeypatch.setattr(dp, "get_service_client", lambda: _ExplodingSb())

    async def _noop_alerts(**_k: Any) -> None:
        return None

    monkeypatch.setattr(dp, "emit_warehouse_state_alerts", _noop_alerts)
    picked_with = _wire_common_spies(monkeypatch)

    out = await dp.process_tenant_daily_send(_TENANT)

    assert picked_with == {"tenant_id": "tenant-1", "n": 50}
    assert out["picked"] == 0
    # Telemetry failed → no refill attempted on this tick (safe default).
    assert out["needed_refill"] is False
