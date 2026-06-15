"""Tests for the premium decision-maker finder — gates only, fully mocked.

Verifies: the already-personal skip (no spend), the budget gate, the
named-person selection, NeverBounce fail-closed, and the role-only no-op.
Hunter / NeverBounce / the budget RPC are all monkeypatched — no network, no DB.
"""

from __future__ import annotations

import pytest

from src.services import decision_maker_finder as dmf
from src.services.hunter_io_service import HunterEmailResult, HunterIoError
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
        self.rpc_called = False

    def rpc(self, name: str, params: dict):  # noqa: ANN201 - test stub
        assert name == "reserve_premium_budget"
        self.rpc_called = True
        return _FakeRpc(self._budget_ok)


@pytest.fixture(autouse=True)
def _hunter_key_present(monkeypatch):
    """Default: a Hunter key IS configured, so the finder reaches the lookup.
    Tests that exercise the no-key path override this in-body."""
    monkeypatch.setattr(dmf.settings, "hunter_api_key", "hunter-test-key")


def _hunter(
    email: str,
    *,
    first: str | None = "Mario",
    last: str | None = "Rossi",
    pos: str | None = "Amministratore",
    conf: int = 92,
    verified: bool = True,
) -> HunterEmailResult:
    return HunterEmailResult(
        email=email,
        first_name=first,
        last_name=last,
        position=pos,
        linkedin_url=None,
        confidence_score=conf,
        sources_count=3,
        verified=verified,
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
async def test_no_hunter_key_skips_without_budget_charge(monkeypatch):
    # No Hunter key → no-op that NEVER reserves budget or calls Hunter, so the
    # spend/lookups counters stay clean until the key is configured.
    monkeypatch.setattr(dmf.settings, "hunter_api_key", "")
    sb = _FakeSb(budget_ok=True)
    monkeypatch.setattr(dmf, "get_service_client", lambda: sb)

    called = {"hunter": False}

    async def _ds(*_a, **_k):
        called["hunter"] = True
        return []

    monkeypatch.setattr(dmf, "domain_search", _ds)

    out = await dmf.upgrade_to_decision_maker(
        company_domain="azienda.it", current_email="info@azienda.it", tenant_id="t"
    )
    assert out is None
    assert called["hunter"] is False
    assert sb.rpc_called is False  # budget counter untouched


@pytest.mark.asyncio
async def test_attempt_upgrade_reason_hunter_error(monkeypatch):
    # Hunter API rejects the call (e.g. bad key → 401) → diagnostic reason
    # "hunter_error", surfaced so the operator knows it's not "no contact found".
    monkeypatch.setattr(dmf, "get_service_client", lambda: _FakeSb(budget_ok=True))

    async def _ds(*_a, **_k):
        raise HunterIoError("status=401 body=unauthorized")

    monkeypatch.setattr(dmf, "domain_search", _ds)

    out, reason = await dmf._attempt_upgrade(
        company_domain="samocar.it", current_email="info@samocar.it", tenant_id="t"
    )
    assert out is None
    assert reason == "hunter_error"


@pytest.mark.asyncio
async def test_attempt_upgrade_reason_no_named_candidate(monkeypatch):
    # Hunter responds OK but returns no named person for the domain → reason
    # "no_named_candidate" (a real credit was spent; the company just has none).
    monkeypatch.setattr(dmf, "get_service_client", lambda: _FakeSb(budget_ok=True))

    async def _ds(domain, *, client=None, **_k):
        return [_hunter("info@samocar.it", first=None, last=None, pos=None)]

    monkeypatch.setattr(dmf, "domain_search", _ds)

    out, reason = await dmf._attempt_upgrade(
        company_domain="samocar.it", current_email="info@samocar.it", tenant_id="t"
    )
    assert out is None
    assert reason == "no_named_candidate"


@pytest.mark.asyncio
async def test_attempt_upgrade_accepts_targeted_alias(monkeypatch):
    # No named person, but a non-generic TARGETED mailbox (Hilton-style) → accept
    # it as an upgrade (better than info@). Generic boxes stay excluded.
    monkeypatch.setattr(dmf.settings, "neverbounce_api_key", "")
    monkeypatch.setattr(dmf, "get_service_client", lambda: _FakeSb(budget_ok=True))

    async def _ds(domain, *, client=None, **_k):
        return [
            _hunter("info@azienda.it", first=None, last=None, pos=None),  # generic → excluded
            _hunter(
                "g.esposito@azienda.it", first=None, last=None, pos=None, verified=True, conf=90
            ),
        ]

    monkeypatch.setattr(dmf, "domain_search", _ds)

    out, reason = await dmf._attempt_upgrade(
        company_domain="azienda.it", current_email="info@azienda.it", tenant_id="t"
    )
    assert reason == "ok"
    assert out is not None
    assert out.email == "g.esposito@azienda.it"
    assert out.name is None  # a mailbox, no parsed person name


@pytest.mark.asyncio
async def test_attempt_upgrade_prefers_named_over_alias(monkeypatch):
    # A real named person beats a higher-confidence non-generic alias.
    monkeypatch.setattr(dmf.settings, "neverbounce_api_key", "")
    monkeypatch.setattr(dmf, "get_service_client", lambda: _FakeSb(budget_ok=True))

    async def _ds(domain, *, client=None, **_k):
        return [
            _hunter(
                "g.esposito@azienda.it", first=None, last=None, pos=None, verified=True, conf=95
            ),
            _hunter("mario.rossi@azienda.it", first="Mario", last="Rossi", verified=True, conf=80),
        ]

    monkeypatch.setattr(dmf, "domain_search", _ds)

    out, reason = await dmf._attempt_upgrade(
        company_domain="azienda.it", current_email="info@azienda.it", tenant_id="t"
    )
    assert reason == "ok"
    assert out is not None
    assert out.email == "mario.rossi@azienda.it"
    assert out.name == "Mario Rossi"


@pytest.mark.asyncio
async def test_weak_email_upgraded(monkeypatch):
    # NeverBounce configured → the authoritative-validation path.
    monkeypatch.setattr(dmf.settings, "neverbounce_api_key", "nb-test-key")
    monkeypatch.setattr(dmf, "get_service_client", lambda: _FakeSb(budget_ok=True))

    async def _ds(domain, *, client=None, **_k):
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
    monkeypatch.setattr(dmf.settings, "neverbounce_api_key", "nb-test-key")
    monkeypatch.setattr(dmf, "get_service_client", lambda: _FakeSb(budget_ok=True))

    async def _ds(domain, *, client=None, **_k):
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
async def test_hunter_fallback_upgrades_without_neverbounce(monkeypatch):
    # No NeverBounce key → fall back to Hunter's own verification. A
    # high-confidence (or Hunter-"valid") named result is promoted, and
    # NeverBounce is never called.
    monkeypatch.setattr(dmf.settings, "neverbounce_api_key", "")
    monkeypatch.setattr(dmf, "get_service_client", lambda: _FakeSb(budget_ok=True))

    async def _ds(domain, *, client=None, **_k):
        return [_hunter("mario.rossi@azienda.it", verified=False, conf=90)]

    monkeypatch.setattr(dmf, "domain_search", _ds)

    async def _verify(email, *, client=None):
        raise AssertionError("NeverBounce must not be called when its key is unset")

    monkeypatch.setattr(dmf, "verify_email", _verify)

    out = await dmf.upgrade_to_decision_maker(
        company_domain="azienda.it", current_email="info@azienda.it", tenant_id="t"
    )
    assert out is not None
    assert out.email == "mario.rossi@azienda.it"


@pytest.mark.asyncio
async def test_hunter_fallback_weak_signal_not_promoted(monkeypatch):
    # No NeverBounce key, Hunter neither "valid" nor high-confidence → skip
    # (fail closed). Keeps the website email.
    monkeypatch.setattr(dmf.settings, "neverbounce_api_key", "")
    monkeypatch.setattr(dmf, "get_service_client", lambda: _FakeSb(budget_ok=True))

    async def _ds(domain, *, client=None, **_k):
        return [_hunter("mario.rossi@azienda.it", verified=False, conf=40)]

    monkeypatch.setattr(dmf, "domain_search", _ds)

    out = await dmf.upgrade_to_decision_maker(
        company_domain="azienda.it", current_email="info@azienda.it", tenant_id="t"
    )
    assert out is None


# ---------------------------------------------------------------------------
# §D — batch_reenrich_and_resend: eligibility/exclusions + dry-run safety
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, data: list) -> None:
        self.data = data


class _FakeNot:
    """Stub for PostgREST's ``.not_`` negation proxy."""

    def __init__(self, q) -> None:  # noqa: ANN001 - test stub (forward ref to _FakeQuery)
        self._q = q

    def is_(self, *_a, **_k):  # noqa: ANN001, ANN201 - test stub
        return self._q

    def in_(self, *_a, **_k):  # noqa: ANN001, ANN201 - test stub
        return self._q


class _FakeQuery:
    """Filter methods are no-ops; ``execute`` returns the table's preset rows.
    Python-side exclusions (audit_log + outreach_sends) are what we assert."""

    def __init__(self, data: list) -> None:
        self._data = data

    def select(self, *_a, **_k):  # noqa: ANN001, ANN201 - test stub
        return self

    def update(self, *_a, **_k):  # noqa: ANN001, ANN201 - test stub
        return self

    def eq(self, *_a, **_k):  # noqa: ANN001, ANN201 - test stub
        return self

    def is_(self, *_a, **_k):  # noqa: ANN001, ANN201 - test stub
        return self

    def in_(self, *_a, **_k):  # noqa: ANN001, ANN201 - test stub
        return self

    def gte(self, *_a, **_k):  # noqa: ANN001, ANN201 - test stub
        return self

    def order(self, *_a, **_k):  # noqa: ANN001, ANN201 - test stub
        return self

    def limit(self, *_a, **_k):  # noqa: ANN001, ANN201 - test stub
        return self

    @property
    def not_(self) -> _FakeNot:
        return _FakeNot(self)

    def execute(self) -> _FakeResult:
        return _FakeResult(self._data)


class _FakeBatchSb:
    def __init__(self, *, leads=None, audit=None, outreach_sends=None) -> None:
        self._by_table = {
            "leads": leads or [],
            "audit_log": audit or [],
            "outreach_sends": outreach_sends or [],
            "subjects": [],
        }

    def table(self, name: str) -> _FakeQuery:
        return _FakeQuery(self._by_table.get(name, []))


def _wire_batch(monkeypatch, sb, *, upgraded: set[str]):
    """Patch reenrich/enqueue/log_action; return (reenriched, enqueued) sinks."""
    reenriched: list[str] = []
    enqueued: list[dict] = []

    async def _reenrich(*, tenant_id: str, lead_id: str):
        reenriched.append(lead_id)
        return {"ok": True, "upgraded": lead_id in upgraded}

    async def _enqueue(function: str, payload: dict, *, job_id=None, defer_until=None):
        enqueued.append({"function": function, "payload": payload, "job_id": job_id})
        return {"job_id": job_id, "status": "queued"}

    async def _log(*_a, **_k):
        return None

    monkeypatch.setattr(dmf, "get_service_client", lambda: sb)
    monkeypatch.setattr(dmf, "reenrich_lead_contact", _reenrich)
    monkeypatch.setattr(dmf, "enqueue", _enqueue)
    monkeypatch.setattr(dmf, "log_action", _log)
    return reenriched, enqueued


@pytest.mark.asyncio
async def test_batch_dry_run_never_sends(monkeypatch):
    sb = _FakeBatchSb(leads=[{"id": "L1"}, {"id": "L2"}])
    reenriched, enqueued = _wire_batch(monkeypatch, sb, upgraded={"L1", "L2"})

    out = await dmf.batch_reenrich_and_resend(tenant_id="t", dry_run=True)

    assert sorted(reenriched) == ["L1", "L2"]
    assert enqueued == []  # dry-run: nothing sent
    assert out["upgraded"] == 2
    assert out["resends_queued"] == 0
    assert out["dry_run"] is True


@pytest.mark.asyncio
async def test_batch_excludes_alt_address_and_followups(monkeypatch):
    # L1 was resent to an alternate address (Hilton/Sigma); L2 already got a
    # cron follow-up (sequence_step>=2); only L3 is eligible.
    sb = _FakeBatchSb(
        leads=[{"id": "L1"}, {"id": "L2"}, {"id": "L3"}],
        audit=[{"target_id": "L1"}],
        outreach_sends=[{"lead_id": "L2", "sequence_step": 2}],
    )
    reenriched, enqueued = _wire_batch(monkeypatch, sb, upgraded={"L3"})

    out = await dmf.batch_reenrich_and_resend(tenant_id="t", dry_run=True)

    assert reenriched == ["L3"]
    assert out["eligible"] == 1
    assert out["upgraded"] == 1


@pytest.mark.asyncio
async def test_batch_send_mode_enqueues_official_resend(monkeypatch):
    sb = _FakeBatchSb(leads=[{"id": "L1"}, {"id": "L2"}])
    # Only L1 yields a better contact → only L1 is re-sent.
    reenriched, enqueued = _wire_batch(monkeypatch, sb, upgraded={"L1"})

    out = await dmf.batch_reenrich_and_resend(tenant_id="t", dry_run=False)

    assert sorted(reenriched) == ["L1", "L2"]
    assert len(enqueued) == 1
    job = enqueued[0]
    assert job["function"] == "outreach_task"
    assert job["payload"]["lead_id"] == "L1"
    assert job["payload"]["force"] is True
    assert job["payload"]["sequence_step"] == 1  # official copy, not a follow-up
    assert out["resends_queued"] == 1


@pytest.mark.asyncio
async def test_role_only_results_no_upgrade(monkeypatch):
    monkeypatch.setattr(dmf, "get_service_client", lambda: _FakeSb(budget_ok=True))

    async def _ds(domain, *, client=None, **_k):
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
