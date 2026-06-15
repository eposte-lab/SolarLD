"""Tests for the premium decision-maker finder — gates only, fully mocked.

Verifies: the already-personal skip (no spend), the budget gate, the
named-person selection, NeverBounce fail-closed, and the role-only no-op.
Hunter / NeverBounce / the budget RPC are all monkeypatched — no network, no DB.
"""

from __future__ import annotations

import pytest

from src.services import decision_maker_finder as dmf
from src.services.hunter_io_service import HunterEmailResult
from src.services.neverbounce_service import EmailVerification, VerificationResult


class _FakeRpc:
    def __init__(self, value: bool) -> None:
        self._v = value

    def execute(self):  # noqa: ANN201 - test stub
        class _R:
            data = self._v

        return _R()


class _FakeSb:
    """Minimal Supabase stub: rpc('reserve_premium_budget') -> budget bool."""

    def __init__(self, budget_ok: bool = True) -> None:
        self._budget_ok = budget_ok

    def rpc(self, name: str, params: dict):  # noqa: ANN201 - test stub
        assert name == "reserve_premium_budget"
        return _FakeRpc(self._budget_ok)


def _hunter(
    email: str,
    *,
    first: str | None = "Mario",
    last: str | None = "Rossi",
    pos: str | None = "Amministratore",
    conf: int = 92,
) -> HunterEmailResult:
    return HunterEmailResult(
        email=email,
        first_name=first,
        last_name=last,
        position=pos,
        linkedin_url=None,
        confidence_score=conf,
        sources_count=3,
        verified=True,
        raw={},
    )


def _nb(
    email: str,
    *,
    result: VerificationResult = VerificationResult.VALID,
    role: bool = False,
) -> EmailVerification:
    return EmailVerification(
        email=email, result=result, role_address=role, free_email=False, disposable=False, raw={}
    )


@pytest.mark.asyncio
async def test_already_personal_email_skips_lookup(monkeypatch):
    called = {"hunter": False}

    async def _ds(*_a, **_k):
        called["hunter"] = True
        return []

    monkeypatch.setattr(dmf, "domain_search", _ds)
    monkeypatch.setattr(dmf, "get_service_client", lambda: _FakeSb())

    out = await dmf.upgrade_to_decision_maker(
        company_domain="azienda.it", current_email="mario.rossi@azienda.it", tenant_id="t"
    )
    assert out is None
    assert called["hunter"] is False  # never spent budget on an already-named contact


@pytest.mark.asyncio
async def test_weak_email_upgraded(monkeypatch):
    monkeypatch.setattr(dmf, "get_service_client", lambda: _FakeSb(budget_ok=True))

    async def _ds(domain, *, client=None):
        return [_hunter("mario.rossi@azienda.it")]

    monkeypatch.setattr(dmf, "domain_search", _ds)

    async def _verify(email, *, client=None):
        return _nb(email)

    monkeypatch.setattr(dmf, "verify_email", _verify)

    out = await dmf.upgrade_to_decision_maker(
        company_domain="www.azienda.it", current_email="info@azienda.it", tenant_id="t"
    )
    assert out is not None
    assert out.email == "mario.rossi@azienda.it"
    assert out.name == "Mario Rossi"
    assert out.role == "Amministratore"
    assert out.fallback_email == "info@azienda.it"


@pytest.mark.asyncio
async def test_budget_exhausted_skips_without_calling_hunter(monkeypatch):
    called = {"hunter": False}

    async def _ds(*_a, **_k):
        called["hunter"] = True
        return []

    monkeypatch.setattr(dmf, "domain_search", _ds)
    monkeypatch.setattr(dmf, "get_service_client", lambda: _FakeSb(budget_ok=False))

    out = await dmf.upgrade_to_decision_maker(
        company_domain="azienda.it", current_email="info@azienda.it", tenant_id="t"
    )
    assert out is None
    assert called["hunter"] is False


@pytest.mark.asyncio
async def test_neverbounce_invalid_not_promoted(monkeypatch):
    monkeypatch.setattr(dmf, "get_service_client", lambda: _FakeSb(budget_ok=True))

    async def _ds(domain, *, client=None):
        return [_hunter("mario.rossi@azienda.it")]

    monkeypatch.setattr(dmf, "domain_search", _ds)

    async def _verify(email, *, client=None):
        return _nb(email, result=VerificationResult.INVALID)

    monkeypatch.setattr(dmf, "verify_email", _verify)

    out = await dmf.upgrade_to_decision_maker(
        company_domain="azienda.it", current_email="info@azienda.it", tenant_id="t"
    )
    assert out is None


@pytest.mark.asyncio
async def test_role_only_results_no_upgrade(monkeypatch):
    monkeypatch.setattr(dmf, "get_service_client", lambda: _FakeSb(budget_ok=True))

    async def _ds(domain, *, client=None):
        # Hunter returns only a role inbox with no person name → not a decision maker.
        return [_hunter("info@azienda.it", first=None, last=None, pos=None)]

    monkeypatch.setattr(dmf, "domain_search", _ds)

    async def _verify(email, *, client=None):
        return _nb(email)

    monkeypatch.setattr(dmf, "verify_email", _verify)

    out = await dmf.upgrade_to_decision_maker(
        company_domain="azienda.it", current_email="info@azienda.it", tenant_id="t"
    )
    assert out is None
