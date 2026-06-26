"""Tests for the funnel-stall recovery service.

Covers the detector logic (work available + no recent consumption ⇒ re-enqueue;
recent consumption ⇒ skip; no work ⇒ skip) and the orphan-candidate cleanup.
NeverBounce/network/DB are stubbed; the re-enqueue is captured, never run.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.services import funnel_stall_service as fss


class _Q:
    def __init__(self, table: str, store: dict) -> None:
        self.table = table
        self.store = store
        self.flags: set = set()
        self._update: dict | None = None

    def select(self, *a: Any, **k: Any) -> _Q:
        return self

    def in_(self, *a: Any, **k: Any) -> _Q:
        return self

    def eq(self, *a: Any, **k: Any) -> _Q:
        return self

    def limit(self, *a: Any, **k: Any) -> _Q:
        return self

    def order(self, *a: Any, **k: Any) -> _Q:
        return self

    def is_(self, col: str, val: str) -> _Q:
        self.flags.add("is")
        return self

    def gt(self, col: str, val: str) -> _Q:
        self.flags.add("gt")
        return self

    def lt(self, col: str, val: str) -> _Q:
        self.flags.add("lt")
        return self

    @property
    def not_(self) -> _Q:
        return self

    def update(self, payload: dict) -> _Q:
        self._update = payload
        return self

    def insert(self, payload: dict) -> _Q:
        self.store.setdefault("events.insert", []).append(payload)
        return self

    def execute(self) -> SimpleNamespace:
        if self._update is not None:
            self.store.setdefault("update", []).append(self._update)
            return SimpleNamespace(data=self.store.get("update_returns", []))
        if self.table == "scan_jobs":
            return SimpleNamespace(data=self.store.get("scan_jobs", []), count=None)
        if self.table == "scan_candidates":
            if "lt" in self.flags:  # orphan select (created_at < cutoff)
                return SimpleNamespace(data=self.store.get("orphan_ids", []), count=None)
            if "gt" in self.flags:  # recently-processed count
                return SimpleNamespace(data=[], count=self.store.get("recent", 0))
            if "is" in self.flags:  # un-processed consumable backlog count
                return SimpleNamespace(data=[], count=self.store.get("unproc", 0))
        return SimpleNamespace(data=[], count=0)


class _SB:
    def __init__(self, store: dict) -> None:
        self.store = store

    def table(self, name: str) -> _Q:
        return _Q(name, self.store)


@pytest.fixture
def _enqueued(monkeypatch):  # noqa: ANN001
    calls: list = []

    async def _fake(function, payload, **k):  # noqa: ANN001, ANN202
        calls.append((function, payload, k))
        return {"job_id": k.get("job_id"), "status": "queued"}

    monkeypatch.setattr(fss, "enqueue", _fake)
    return calls


def _job(tid: str = "t1") -> dict:
    return {
        "id": "j1",
        "tenant_id": tid,
        "status": "in_progress",
        "priority": 1,
        "daily_validated_cap": 90,
    }


@pytest.mark.asyncio
async def test_stall_detected_reenqueues(monkeypatch, _enqueued):  # noqa: ANN001
    store = {"scan_jobs": [_job()], "unproc": 50, "recent": 0}
    monkeypatch.setattr(fss, "get_service_client", lambda: _SB(store))

    res = await fss.run_funnel_stall_recovery()

    assert res == {"checked": 1, "stalled": 1, "recovered": 1}
    assert len(_enqueued) == 1
    fn, payload, kw = _enqueued[0]
    assert fn == "hunter_funnel_v3_task"
    assert payload["scan_job_id"] == "j1"
    assert kw["job_id"].startswith("funnel_v3_stall_recovery:t1:")  # unique → job_try=1
    assert store.get("events.insert")  # alert event emitted


@pytest.mark.asyncio
async def test_recent_consumption_not_stalled(monkeypatch, _enqueued):  # noqa: ANN001
    store = {"scan_jobs": [_job()], "unproc": 50, "recent": 7}
    monkeypatch.setattr(fss, "get_service_client", lambda: _SB(store))

    res = await fss.run_funnel_stall_recovery()

    assert res == {"checked": 1, "stalled": 0, "recovered": 0}
    assert _enqueued == []


@pytest.mark.asyncio
async def test_no_work_skipped(monkeypatch, _enqueued):  # noqa: ANN001
    store = {"scan_jobs": [_job()], "unproc": 0, "recent": 0}
    monkeypatch.setattr(fss, "get_service_client", lambda: _SB(store))

    res = await fss.run_funnel_stall_recovery()

    assert res == {"checked": 0, "stalled": 0, "recovered": 0}
    assert _enqueued == []


@pytest.mark.asyncio
async def test_orphan_cleanup_counts_updates(monkeypatch):  # noqa: ANN001
    store = {"orphan_ids": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
    monkeypatch.setattr(fss, "get_service_client", lambda: _SB(store))

    res = await fss.run_orphan_candidate_cleanup()

    assert res == {"cleared": 3, "errored": 0}
    assert store.get("update", [])[0]["processed_at"]  # stamped processed_at on the IN(ids) update
