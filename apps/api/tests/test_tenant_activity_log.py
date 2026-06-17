"""Super-admin tenant activity-log: detailed read-only chronology.

The endpoint returns the tenant's `events` in time order, enriched with each
lead's business name + render image (so the operator reviews the visual
analysis inline). It replaces the moderation approval queue. Super-admin only.
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
    def __init__(self, data: Any, count: int | None = None) -> None:
        self.data = data
        self.count = count


class _Query:
    def __init__(self, res: _Res) -> None:
        self._res = res

    def select(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def eq(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def in_(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def gte(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def order(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def limit(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def offset(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def execute(self) -> _Res:
        return self._res


class _Sb:
    def __init__(self, events: list[dict], count: int, leads: list[dict]) -> None:
        self._events = events
        self._count = count
        self._leads = leads

    def table(self, name: str) -> _Query:
        if name == "events":
            return _Query(_Res(self._events, self._count))
        if name == "leads":
            return _Query(_Res(self._leads))
        return _Query(_Res([]))


async def test_activity_log_enriches_with_business_and_render(monkeypatch: Any) -> None:
    events = [
        {
            "event_type": "lead.outreach_sent",
            "event_source": "agent.outreach",
            "occurred_at": "2026-06-17T10:00:00Z",
            "payload": {"channel": "email"},
            "lead_id": "L1",
        },
        {
            "event_type": "scan.completed",
            "event_source": "funnel",
            "occurred_at": "2026-06-17T09:00:00Z",
            "payload": {"count": 5},
            "lead_id": None,
        },
    ]
    leads = [
        {
            "id": "L1",
            "rendering_image_url": "http://img/after.png",
            "subjects": {"business_name": "Eté"},
        }
    ]
    monkeypatch.setattr(admin, "get_service_client", lambda: _Sb(events, 2, leads))

    # Pass params explicitly: a direct call doesn't resolve FastAPI Query()
    # defaults (FastAPI does that at request time).
    res = await admin.tenant_activity_log(
        ctx=_Ctx(),
        tenant_id="t1",
        lead_id=None,
        event_type=None,
        since=None,
        limit=200,
        offset=0,
    )

    assert res.total == 2
    assert len(res.items) == 2

    e0 = res.items[0]
    assert e0.event_type == "lead.outreach_sent"
    assert e0.business_name == "Eté"
    assert e0.rendering_image_url == "http://img/after.png"
    assert e0.payload == {"channel": "email"}

    # Tenant-level event (no lead_id) → no enrichment, no crash.
    e1 = res.items[1]
    assert e1.lead_id is None
    assert e1.business_name is None
    assert e1.rendering_image_url is None


async def test_activity_log_requires_super_admin() -> None:
    class _NotAdmin:
        role = "admin"
        user_id = "u2"

    with pytest.raises(HTTPException) as exc:
        await admin.tenant_activity_log(ctx=_NotAdmin(), tenant_id="t1")
    assert exc.value.status_code == 403
