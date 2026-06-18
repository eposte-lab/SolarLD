"""Transient rate-limit skip re-enqueues instead of stranding the lead.

Regression for the 2026-06-18 zero-send incident: a backlog of overdue sends
draining at once collided on the per-inbox 180s floor and every loser was left
in ``picked`` forever (``_record_skip`` with no retry, no status change). The
agent now re-enqueues a deferred ``outreach_task`` (bounded by
``settings.outreach_retry_max``) so the lead rides out the window and still
sends today; daily/campaign caps instead un-pick back to ``ready_to_send``.
"""

from __future__ import annotations

from typing import Any

from src.agents import outreach
from src.agents.outreach import OutreachAgent, OutreachInput
from src.core.config import settings


class _Res:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    def __init__(self, store: dict, table: str) -> None:
        self._store = store
        self._table = table
        self._is_update = False

    def update(self, payload: dict, *_a: Any, **_k: Any) -> _Query:
        self._is_update = True
        self._store["updates"].append((self._table, payload))
        return self

    def insert(self, payload: dict, *_a: Any, **_k: Any) -> _Query:
        self._store["inserts"].append((self._table, payload))
        return self

    def eq(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def execute(self) -> _Res:
        return _Res([])


class _Sb:
    def __init__(self, store: dict) -> None:
        self._store = store

    def table(self, name: str) -> _Query:
        return _Query(self._store, name)


def _agent(monkeypatch: Any, store: dict) -> OutreachAgent:
    monkeypatch.setattr(outreach, "get_service_client", lambda: _Sb(store))

    async def _fake_enqueue(
        function: str,
        payload: dict,
        *,
        job_id: str | None = None,
        defer_until: Any = None,
    ) -> dict:
        store["enqueues"].append(
            {
                "function": function,
                "payload": payload,
                "job_id": job_id,
                "defer_until": defer_until,
            }
        )
        return {"job_id": job_id, "status": "queued"}

    monkeypatch.setattr(outreach, "enqueue", _fake_enqueue)

    agent = OutreachAgent()

    async def _noop_emit(**_k: Any) -> None:
        return None

    monkeypatch.setattr(agent, "_emit_event", _noop_emit)
    return agent


def _store() -> dict[str, list]:
    return {"inserts": [], "updates": [], "enqueues": []}


async def test_ratelimit_reenqueues_deferred(monkeypatch: Any) -> None:
    store = _store()
    agent = _agent(monkeypatch, store)
    payload = OutreachInput(tenant_id="t", lead_id="L1")  # retry defaults to 0

    out = await agent._reenqueue_after_ratelimit(
        payload=payload,
        reason="rate_limited_hour",
        event_extra={"window": "hour"},
    )

    assert out.skipped is True
    assert out.reason == "rate_limited_hour_retry_scheduled"
    assert len(store["enqueues"]) == 1
    job = store["enqueues"][0]
    assert job["function"] == "outreach_task"
    assert job["job_id"] == "outreach:t:L1:email:r1"
    assert job["payload"]["retry"] == 1
    assert job["defer_until"] is not None
    # A transient skip must NOT strand the lead with a status flip.
    assert not any(t == "leads" for (t, _p) in store["updates"])


async def test_ratelimit_retry_budget_exhausted(monkeypatch: Any) -> None:
    store = _store()
    agent = _agent(monkeypatch, store)
    payload = OutreachInput(tenant_id="t", lead_id="L1", retry=settings.outreach_retry_max)

    out = await agent._reenqueue_after_ratelimit(payload=payload, reason="rate_limited_hour")

    assert out.skipped is True
    assert out.reason == "rate_limited_hour_retry_exhausted"
    # Budget spent → stop looping, no further enqueue.
    assert store["enqueues"] == []


async def test_retry_counter_increments_across_attempts(monkeypatch: Any) -> None:
    store = _store()
    agent = _agent(monkeypatch, store)
    payload = OutreachInput(tenant_id="t", lead_id="L1", retry=3)

    out = await agent._reenqueue_after_ratelimit(payload=payload, reason="rate_limited_warmup")

    assert out.reason == "rate_limited_warmup_retry_scheduled"
    job = store["enqueues"][0]
    assert job["payload"]["retry"] == 4
    assert job["job_id"] == "outreach:t:L1:email:r4"


async def test_daily_cap_unpicks_to_ready(monkeypatch: Any) -> None:
    """The daily/campaign cap path un-picks rather than stranding in picked."""
    store = _store()
    agent = _agent(monkeypatch, store)
    payload = OutreachInput(tenant_id="t", lead_id="L1")

    out = await agent._record_skip(
        payload=payload,
        lead={},
        reason="daily_target_cap_reached",
        pipeline_status="ready_to_send",
        event_type="lead.outreach_ratelimited",
    )

    assert out.skipped is True
    assert any(
        t == "leads" and p.get("pipeline_status") == "ready_to_send" for (t, p) in store["updates"]
    )
