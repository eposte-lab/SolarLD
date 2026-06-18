"""``rescue_stranded_picked`` re-fires outreach for worker-abandoned picks.

Regression for the 2026-06-18 incident: when the worker died mid-batch the
deferred ``outreach_task`` for each ``picked`` lead never ran and the lead sat
in ``picked`` indefinitely. The rescue cron re-issues a fresh, staggered
``outreach_task`` per stranded lead (idempotent via the agent's already-sent
dedup; render-less / already-sent leads are excluded at the query).
"""

from __future__ import annotations

from typing import Any

from src.services import daily_pipeline_orchestrator as orch


class _Res:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    """Minimal chainable PostgREST stub — every filter returns self."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def select(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def eq(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def is_(self, *_a: Any, **_k: Any) -> _Query:
        return self

    @property
    def not_(self) -> _Query:
        return self

    def lt(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def order(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def limit(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def execute(self) -> _Res:
        return _Res(self._rows)


class _Sb:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def table(self, _name: str) -> _Query:
        return _Query(self._rows)


def _patch(monkeypatch: Any, rows: list[dict]) -> list[dict]:
    calls: list[dict] = []

    async def _fake_enqueue(
        function: str,
        payload: dict,
        *,
        job_id: str | None = None,
        defer_until: Any = None,
    ) -> dict:
        calls.append(
            {
                "function": function,
                "payload": payload,
                "job_id": job_id,
                "defer_until": defer_until,
            }
        )
        return {"job_id": job_id, "status": "queued"}

    monkeypatch.setattr(orch, "get_service_client", lambda: _Sb(rows))
    monkeypatch.setattr(orch, "enqueue", _fake_enqueue)
    return calls


async def test_rescue_reenqueues_each_stranded_lead(monkeypatch: Any) -> None:
    rows = [
        {"id": "L1", "tenant_id": "t1"},
        {"id": "L2", "tenant_id": "t1"},
        {"id": "L3", "tenant_id": "t1"},
    ]
    calls = _patch(monkeypatch, rows)

    result = await orch.rescue_stranded_picked()

    assert result == {"ok": True, "rescued": 3, "tenants": 1}
    assert [c["function"] for c in calls] == ["outreach_task"] * 3
    # Re-uses the daily pipeline's own job_id so arq dedups against a still-
    # pending original send (no double-email), rather than a distinct rescue: id.
    assert {c["job_id"] for c in calls} == {
        "outreach:t1:L1:email",
        "outreach:t1:L2:email",
        "outreach:t1:L3:email",
    }
    # Per-tenant stagger: the deferral instants must be strictly increasing so
    # the inboxes don't collide on the 180s floor.
    defers = [c["defer_until"] for c in calls]
    assert defers[0] < defers[1] < defers[2]


async def test_rescue_noop_when_nothing_stranded(monkeypatch: Any) -> None:
    calls = _patch(monkeypatch, [])
    result = await orch.rescue_stranded_picked()
    assert result == {"ok": True, "rescued": 0}
    assert calls == []


async def test_rescue_staggers_per_tenant_independently(monkeypatch: Any) -> None:
    rows = [
        {"id": "L1", "tenant_id": "t1"},
        {"id": "L2", "tenant_id": "t2"},
        {"id": "L3", "tenant_id": "t1"},
    ]
    calls = _patch(monkeypatch, rows)

    result = await orch.rescue_stranded_picked()

    assert result["rescued"] == 3
    assert result["tenants"] == 2
    by_lead = {c["payload"]["lead_id"]: c["defer_until"] for c in calls}
    # First lead of each tenant shares the same base instant (idx 0); the
    # second lead of t1 is staggered one slot later.
    assert by_lead["L1"] == by_lead["L2"]
    assert by_lead["L3"] > by_lead["L1"]
