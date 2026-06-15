"""Opt-in gate for applying the contact-enrichment waterfall to outreach.

Default OFF → sends keep the website email; the automatic waterfall does not run
and never overrides the send recipient until the owner flips the flag.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services import contact_waterfall as cw
from src.services import tenant_module_service as tms
from src.services.tenant_module_service import OutreachConfig
from src.workers import main as wmain


# --------------------------------------------------------------------------- #
# schema
# --------------------------------------------------------------------------- #
def test_outreach_config_flag_default_off():
    assert OutreachConfig().premium_contact_apply_to_send is False


def test_outreach_config_flag_settable():
    assert OutreachConfig(premium_contact_apply_to_send=True).premium_contact_apply_to_send is True


# --------------------------------------------------------------------------- #
# is_premium_contact_apply_to_send
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_flag_reads_false_when_absent(monkeypatch):
    async def _get_module(tid, key):
        assert key == "outreach"
        return SimpleNamespace(config={})  # missing key → default off

    monkeypatch.setattr(tms, "get_module", _get_module)
    assert await tms.is_premium_contact_apply_to_send("t") is False


@pytest.mark.asyncio
async def test_flag_reads_true_when_set(monkeypatch):
    async def _get_module(tid, key):
        return SimpleNamespace(config={"premium_contact_apply_to_send": True})

    monkeypatch.setattr(tms, "get_module", _get_module)
    assert await tms.is_premium_contact_apply_to_send("t") is True


@pytest.mark.asyncio
async def test_flag_fail_closed_on_read_error(monkeypatch):
    async def _get_module(tid, key):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(tms, "get_module", _get_module)
    assert await tms.is_premium_contact_apply_to_send("t") is False


# --------------------------------------------------------------------------- #
# contact_enrichment_task gate (robust chokepoint)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_task_skips_waterfall_when_flag_off(monkeypatch):
    async def _flag(_tenant_id):
        return False

    monkeypatch.setattr(tms, "is_premium_contact_apply_to_send", _flag)

    async def _resolve(**_k):
        raise AssertionError("waterfall must not run when the flag is off")

    monkeypatch.setattr(cw, "resolve_best_contact", _resolve)

    out = await wmain.contact_enrichment_task({}, {"tenant_id": "t", "lead_id": "L1"})
    assert out == {"lead_id": "L1", "status": "skipped", "reason": "apply_to_send_off"}


@pytest.mark.asyncio
async def test_task_runs_waterfall_when_flag_on(monkeypatch):
    async def _flag(_tenant_id):
        return True

    monkeypatch.setattr(tms, "is_premium_contact_apply_to_send", _flag)

    async def _resolve(*, tenant_id, lead_id, name_hint=None, sector=None, force=False):
        return SimpleNamespace(status="done", reason="step1_hunter")

    monkeypatch.setattr(cw, "resolve_best_contact", _resolve)

    out = await wmain.contact_enrichment_task({}, {"tenant_id": "t", "lead_id": "L1"})
    assert out == {"lead_id": "L1", "status": "done", "reason": "step1_hunter"}


# --------------------------------------------------------------------------- #
# Quality send gate: premium_contact_required_to_send + offer completeness
# --------------------------------------------------------------------------- #
def test_outreach_config_required_flag():
    assert OutreachConfig().premium_contact_required_to_send is False
    assert (
        OutreachConfig(premium_contact_required_to_send=True).premium_contact_required_to_send
        is True
    )


@pytest.mark.asyncio
async def test_required_flag_default_false(monkeypatch):
    async def _gm(tid, key):
        return SimpleNamespace(config={})

    monkeypatch.setattr(tms, "get_module", _gm)
    assert await tms.is_premium_contact_required_to_send("t") is False


@pytest.mark.asyncio
async def test_required_flag_true_when_set(monkeypatch):
    async def _gm(tid, key):
        return SimpleNamespace(config={"premium_contact_required_to_send": True})

    monkeypatch.setattr(tms, "get_module", _gm)
    assert await tms.is_premium_contact_required_to_send("t") is True


@pytest.mark.asyncio
async def test_required_flag_fail_closed(monkeypatch):
    async def _gm(tid, key):
        raise RuntimeError("db down")

    monkeypatch.setattr(tms, "get_module", _gm)
    assert await tms.is_premium_contact_required_to_send("t") is False


def test_offer_is_complete():
    from src.agents.outreach import _offer_is_complete

    assert _offer_is_complete({"estimated_kwp": 50, "yearly_savings_eur": 8000}) is True
    assert _offer_is_complete({"system_kwp": 30, "annual_savings_eur": 5000}) is True  # legacy
    assert _offer_is_complete({"estimated_kwp": 50, "realistic_yearly_savings_eur": 6000}) is True
    assert _offer_is_complete({"estimated_kwp": 50}) is False  # no savings
    assert _offer_is_complete({"yearly_savings_eur": 8000}) is False  # no kwp
    assert _offer_is_complete({"estimated_kwp": 0, "yearly_savings_eur": 0}) is False
    assert _offer_is_complete({}) is False
    assert _offer_is_complete(None) is False
