"""Render/offer gates UN-PICK a stranded first-touch lead instead of leaving
it dead in ``picked``.

Bug (2026-06-19): with Google Solar billing 403 the render never landed, so the
render-readiness gate returned a bare skip and ~22 leads sat forever in
``picked`` — clogging the warehouse pick and stalling the day's sends. The
gate's own comment promises the lead is "re-picked next cycle", which only works
if it is first released to ``ready_to_send``. Both the render gate and the
offer-completeness gate now un-pick first-touch leads; follow-ups (not in
``picked``) keep a plain skip so a contacted lead's status is never rewritten.
"""

from __future__ import annotations

import inspect
from typing import Any

from src.agents import outreach
from src.agents.outreach import OutreachAgent, OutreachInput


def test_render_and_offer_gates_unpick_when_picked() -> None:
    src = inspect.getsource(outreach)
    # Both stranding gates must release a stranded first-touch lead.
    for reason in ("render_not_ready", "offer_incomplete"):
        assert f'reason="{reason}"' in src
    # The un-pick is guarded on the lead actually being in ``picked`` so a
    # follow-up's status is never rewritten to ready_to_send.
    assert 'lead.get("pipeline_status") == "picked"' in src
    assert 'pipeline_status="ready_to_send"' in src


# --- Behavioral: the skip helper the gates now call really un-picks the lead ---


class _Res:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    def __init__(self, store: dict, table: str) -> None:
        self._store = store
        self._table = table

    def update(self, payload: dict, *_a: Any, **_k: Any) -> _Query:
        self._store["updates"].append((self._table, payload))
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
    agent = OutreachAgent()

    async def _noop_emit(**_k: Any) -> None:
        return None

    monkeypatch.setattr(agent, "_emit_event", _noop_emit)
    return agent


async def test_render_not_ready_unpicks_to_ready(monkeypatch: Any) -> None:
    store: dict[str, list] = {"updates": []}
    agent = _agent(monkeypatch, store)

    out = await agent._record_skip(
        payload=OutreachInput(tenant_id="t", lead_id="L1"),
        lead={"pipeline_status": "picked"},
        reason="render_not_ready",
        pipeline_status="ready_to_send",
        event_type="lead.outreach_skipped",
    )

    assert out.skipped is True
    assert out.reason == "render_not_ready"
    # The stranded lead is released to the warehouse, not left in ``picked``.
    assert any(
        t == "leads" and p.get("pipeline_status") == "ready_to_send" for (t, p) in store["updates"]
    )
