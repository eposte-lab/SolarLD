"""hunter_funnel_v3_task bails fast on an archived/exhausted scan job.

Regression for the 2026-06-18 incident: a stuck scan job was archived, but its
``hunter_funnel_v3_task`` retries kept sitting in the arq queue and re-ran the
heavy ``run_funnel_v3`` consume on every worker boot — wedging the single event
loop and starving the time-sensitive outreach sends. The task now checks the
scan job's status first and returns a fast no-op when it is no longer active.
"""

from __future__ import annotations

from typing import Any

from src.workers import main as worker_main


class _Res:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    def __init__(self, row: dict | None) -> None:
        self._row = row

    def select(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def eq(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def limit(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def maybe_single(self) -> _Query:
        return self

    def execute(self) -> _Res:
        return _Res(self._row)


class _Sb:
    def __init__(self, row: dict | None) -> None:
        self._row = row

    def table(self, _name: str) -> _Query:
        return _Query(self._row)


def _patch(monkeypatch: Any, scan_row: dict | None) -> dict[str, bool]:
    called = {"run_funnel": False, "tenant_config": False}

    monkeypatch.setattr(worker_main, "get_service_client", lambda: _Sb(scan_row))

    async def _fake_config(_tenant_id: str) -> dict:
        called["tenant_config"] = True
        return {}

    async def _fake_run_funnel(**_k: Any) -> dict:
        called["run_funnel"] = True
        return {}

    monkeypatch.setattr(worker_main, "get_tenant_config", _fake_config)
    monkeypatch.setattr(worker_main, "run_funnel_v3", _fake_run_funnel)
    return called


async def test_archived_scan_job_bails_without_running_funnel(monkeypatch: Any) -> None:
    called = _patch(monkeypatch, {"status": "archived", "province_codes": ["NA"]})

    out = await worker_main.hunter_funnel_v3_task({}, {"tenant_id": "t", "scan_job_id": "s1"})

    assert out == {"ok": True, "skipped": True, "reason": "scan_job_archived"}
    # The heavy consume must NOT run for an inactive scan job.
    assert called["run_funnel"] is False
    assert called["tenant_config"] is False


async def test_active_scan_job_runs_funnel(monkeypatch: Any) -> None:
    called = _patch(monkeypatch, {"status": "in_progress", "province_codes": ["NA"]})

    await worker_main.hunter_funnel_v3_task({}, {"tenant_id": "t", "scan_job_id": "s1"})

    assert called["run_funnel"] is True


async def test_missing_status_is_treated_as_active(monkeypatch: Any) -> None:
    # Defensive: a row without a status (or a missing row) must NOT block the
    # funnel — only an explicit inactive status bails.
    called = _patch(monkeypatch, {"province_codes": []})

    await worker_main.hunter_funnel_v3_task({}, {"tenant_id": "t", "scan_job_id": "s1"})

    assert called["run_funnel"] is True
