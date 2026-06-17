"""No-MX (dead/junk email domain) routes the lead to the phone queue.

Junk/wrong scraped emails (a@a.it, mismatched domains) fail the pre-send MX
check and used to leave a dead 'Fallito' email row. They now route the lead to
`to_call` so the operator calls it instead — the email is dead, the lead may
not be.
"""

from __future__ import annotations

from typing import Any

from src.agents import outreach
from src.agents.outreach import OutreachAgent, OutreachInput


class _Res:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    def __init__(self, store: dict, table: str, insert_result: list) -> None:
        self._store = store
        self._table = table
        self._insert_result = insert_result
        self._is_update = False

    def insert(self, payload: dict, *_a: Any, **_k: Any) -> _Query:
        self._store["inserts"].append((self._table, payload))
        return self

    def update(self, payload: dict, *_a: Any, **_k: Any) -> _Query:
        self._is_update = True
        self._store["updates"].append((self._table, payload))
        return self

    def eq(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def execute(self) -> _Res:
        if self._table == "outreach_sends" and not self._is_update:
            return _Res(self._insert_result)
        return _Res([])


class _Sb:
    def __init__(self, store: dict) -> None:
        self._store = store

    def table(self, name: str) -> _Query:
        return _Query(self._store, name, [{"id": "c1"}])


def _agent(monkeypatch: Any, store: dict) -> OutreachAgent:
    monkeypatch.setattr(outreach, "get_service_client", lambda: _Sb(store))
    agent = OutreachAgent()

    async def _noop_emit(**_k: Any) -> None:
        return None

    monkeypatch.setattr(agent, "_emit_event", _noop_emit)
    return agent


async def test_no_mx_routes_lead_to_phone_queue(monkeypatch: Any) -> None:
    store: dict[str, list] = {"inserts": [], "updates": []}
    agent = _agent(monkeypatch, store)
    payload = OutreachInput(tenant_id="t", lead_id="L1")

    out = await agent._record_failure(
        payload=payload,
        lead={},
        tenant_row={},
        subject={},
        failure_reason="no_mx_record",
        route_to_phone=True,
    )
    assert out.reason == "no_mx_record"
    assert any(
        t == "leads" and p.get("pipeline_status") == "to_call" for (t, p) in store["updates"]
    )


async def test_failure_without_route_does_not_touch_status(monkeypatch: Any) -> None:
    store: dict[str, list] = {"inserts": [], "updates": []}
    agent = _agent(monkeypatch, store)
    payload = OutreachInput(tenant_id="t", lead_id="L1")

    await agent._record_failure(
        payload=payload,
        lead={},
        tenant_row={},
        subject={},
        failure_reason="provider_error",
    )
    assert not any(t == "leads" for (t, _p) in store["updates"])
