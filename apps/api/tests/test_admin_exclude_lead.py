"""One-click exclude-lead: a complete removal from every active surface.

Blacklisting the pipeline_status alone left a lead surfacing as a hot
recontact (operator_released_at / appointment_requested_at / engagement_score
still set — observed with Hotel Terme Gran Paradiso). The exclude action must
clear ALL of those, reject the inbound request, optionally flag existing PV,
and emit an audit event. Super-admin only.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from src.routes import admin


class _Ctx:
    role = "super_admin"
    user_id = "u1"


class _Res:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    def __init__(self, store: dict, table: str, select_data: list) -> None:
        self._store = store
        self._table = table
        self._select_data = select_data
        self._mutated = False

    def select(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def eq(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def neq(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def limit(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def update(self, payload: dict, *_a: Any, **_k: Any) -> _Query:
        self._mutated = True
        self._store["updates"].append((self._table, payload))
        return self

    def insert(self, payload: dict, *_a: Any, **_k: Any) -> _Query:
        self._mutated = True
        self._store["inserts"].append((self._table, payload))
        return self

    def execute(self) -> _Res:
        return _Res([] if self._mutated else self._select_data)


class _Sb:
    def __init__(self, store: dict, lead_row: dict | None) -> None:
        self._store = store
        self._lead_row = lead_row

    def table(self, name: str) -> _Query:
        if name == "leads":
            return _Query(self._store, "leads", [self._lead_row] if self._lead_row else [])
        return _Query(self._store, name, [])


async def test_exclude_lead_clears_all_hot_signals(monkeypatch: Any) -> None:
    store: dict[str, list] = {"updates": [], "inserts": []}
    lead_row = {"id": "L1", "tenant_id": "T1", "roof_id": "R1"}
    monkeypatch.setattr(admin, "get_service_client", lambda: _Sb(store, lead_row))

    res = await admin.admin_exclude_lead(
        ctx=_Ctx(), lead_id="L1", reason="has_pv", set_existing_pv=True
    )
    assert res["excluded"] is True
    assert res["reason"] == "has_pv"

    lead_upd = next(p for (t, p) in store["updates"] if t == "leads")
    assert lead_upd["pipeline_status"] == "blacklisted"
    assert lead_upd["operator_released_at"] is None
    assert lead_upd["operator_review_status"] == "held"
    assert lead_upd["appointment_requested_at"] is None
    assert lead_upd["engagement_score"] == 0

    # roof flagged, inbound rejected, audit event emitted
    assert any(t == "roofs" and p.get("has_existing_pv") is True for (t, p) in store["updates"])
    assert any(
        t == "pending_inbound_requests" and p.get("status") == "rejected"
        for (t, p) in store["updates"]
    )
    assert any(
        t == "events" and p.get("event_type") == "moderation.lead.excluded"
        for (t, p) in store["inserts"]
    )


async def test_exclude_lead_skips_roof_when_flag_off(monkeypatch: Any) -> None:
    store: dict[str, list] = {"updates": [], "inserts": []}
    lead_row = {"id": "L1", "tenant_id": "T1", "roof_id": "R1"}
    monkeypatch.setattr(admin, "get_service_client", lambda: _Sb(store, lead_row))

    await admin.admin_exclude_lead(
        ctx=_Ctx(), lead_id="L1", reason="not_in_target", set_existing_pv=False
    )
    assert not any(t == "roofs" for (t, _p) in store["updates"])


async def test_exclude_lead_404_when_missing(monkeypatch: Any) -> None:
    store: dict[str, list] = {"updates": [], "inserts": []}
    monkeypatch.setattr(admin, "get_service_client", lambda: _Sb(store, None))
    with pytest.raises(HTTPException) as exc:
        await admin.admin_exclude_lead(
            ctx=_Ctx(), lead_id="nope", reason="other", set_existing_pv=False
        )
    assert exc.value.status_code == 404


async def test_exclude_lead_requires_super_admin() -> None:
    class _NotAdmin:
        role = "admin"
        user_id = "u2"

    with pytest.raises(HTTPException) as exc:
        await admin.admin_exclude_lead(
            ctx=_NotAdmin(), lead_id="L1", reason="other", set_existing_pv=False
        )
    assert exc.value.status_code == 403
