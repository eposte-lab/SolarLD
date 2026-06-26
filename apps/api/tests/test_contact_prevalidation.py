"""Tests for the contact pre-validation service.

Verifies the send-policy mirror: only INVALID/DISPOSABLE are excluded
(→ blacklisted); VALID/CATCHALL/UNKNOWN stay sendable; every scanned lead is
logged to api_usage_log (the "validated" marker); and dry_run mutates nothing.
NeverBounce + the Supabase client are stubbed so no network/DB is touched.
"""

from __future__ import annotations

import types

import pytest

from src.services import contact_prevalidation_service as cps
from src.services.neverbounce_service import VerificationResult


class _Q:
    """Minimal fluent stub of a PostgREST query for one table."""

    def __init__(self, name: str, rec: dict, rows: dict) -> None:
        self.name = name
        self.rec = rec
        self.rows = rows
        self._update: dict | None = None

    def select(self, *a, **k):  # noqa: ANN002, ANN003
        return self

    in_ = is_ = eq = order = limit = filter = select  # type: ignore[assignment]

    def insert(self, payload):  # noqa: ANN001
        self.rec.setdefault(f"{self.name}.insert", []).append(payload)
        return self

    def update(self, payload):  # noqa: ANN001
        self._update = payload
        return self

    def execute(self):
        if self._update is not None:
            self.rec.setdefault(f"{self.name}.update", []).append(self._update)
            return types.SimpleNamespace(data=[])
        return types.SimpleNamespace(data=self.rows.get(self.name, []))


class _FakeSB:
    def __init__(self, rows: dict) -> None:
        self.rows = rows
        self.rec: dict = {}

    def table(self, name: str) -> _Q:
        return _Q(name, self.rec, self.rows)


def _lead(lid: str, email: str, biz: str) -> dict:
    return {
        "id": lid,
        "tenant_id": "t1",
        "pipeline_status": "ready_to_send",
        "outreach_sent_at": None,
        "subjects": {"business_name": biz, "decision_maker_email": email},
    }


@pytest.fixture
def _verdicts(monkeypatch):  # noqa: ANN001
    mapping: dict[str, VerificationResult] = {}

    async def _fake(email: str):  # noqa: ANN202
        return types.SimpleNamespace(result=mapping[email])

    monkeypatch.setattr(cps, "verify_email", _fake)
    return mapping


@pytest.mark.asyncio
async def test_invalid_excluded_valid_and_unknown_kept(monkeypatch, _verdicts):  # noqa: ANN001
    rows = {
        "leads": [
            _lead("L1", "good@a.it", "A"),
            _lead("L2", "bad@b.it", "B"),
            _lead("L3", "maybe@c.it", "C"),
        ]
    }
    _verdicts.update(
        {
            "good@a.it": VerificationResult.VALID,
            "bad@b.it": VerificationResult.INVALID,
            "maybe@c.it": VerificationResult.UNKNOWN,
        }
    )
    sb = _FakeSB(rows)
    monkeypatch.setattr(cps, "get_service_client", lambda: sb)

    res = await cps.run_contact_prevalidation(lead_ids=["L1", "L2", "L3"])

    assert res["scanned"] == 3
    assert res["valid"] == 1
    assert res["unknown"] == 1
    assert res["excluded_invalid"] == 1
    # Only the INVALID lead is blacklisted.
    updates = sb.rec.get("leads.update", [])
    assert len(updates) == 1
    assert updates[0]["pipeline_status"] == "blacklisted"
    # One exclusion event, and every scanned lead logged (= marked validated).
    assert len(sb.rec.get("events.insert", [])) == 1
    assert len(sb.rec.get("api_usage_log.insert", [])) == 3


@pytest.mark.asyncio
async def test_dry_run_mutates_nothing(monkeypatch, _verdicts):  # noqa: ANN001
    rows = {"leads": [_lead("L1", "bad@b.it", "B")]}
    _verdicts["bad@b.it"] = VerificationResult.INVALID
    sb = _FakeSB(rows)
    monkeypatch.setattr(cps, "get_service_client", lambda: sb)

    res = await cps.run_contact_prevalidation(lead_ids=["L1"], dry_run=True)

    assert res["excluded_invalid"] == 1
    assert sb.rec.get("leads.update", []) == []
    assert sb.rec.get("api_usage_log.insert", []) == []
    assert sb.rec.get("events.insert", []) == []
