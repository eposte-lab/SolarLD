"""Tests for the contact-enrichment waterfall (Phase 1b) — fully mocked.

Covers STEP 0 guards (no-MX, PEC, already-resolved), STEP 1 (Hunter win),
STEP 3 role ladder (catch-all gate, first-valid, exhausted, budget cap, probe).
Hunter / NeverBounce / the budget RPC / MX / get_service_client are monkeypatched
— no network, no DB. ``commerciale@`` is never in the ladder by construction.
"""

from __future__ import annotations

import pytest

from src.services import contact_waterfall as cw
from src.services.decision_maker_finder import DecisionMakerUpgrade


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _Res:
    def __init__(self, data) -> None:  # noqa: ANN001
        self.data = data


class _RpcRes:
    def __init__(self, ok: bool) -> None:
        self._ok = ok

    def execute(self) -> _Res:
        return _Res(self._ok)


class _Q:
    def __init__(self, sb: _FakeSb, table: str) -> None:
        self.sb = sb
        self.table = table
        self._single = False
        self._op = "select"

    def select(self, *_a, **_k):  # noqa: ANN201
        return self

    def eq(self, *_a, **_k):  # noqa: ANN201
        return self

    def limit(self, *_a, **_k):  # noqa: ANN201
        return self

    def maybe_single(self):  # noqa: ANN201
        self._single = True
        return self

    def update(self, payload):  # noqa: ANN001, ANN201
        self._op = "update"
        self.sb.updates.append((self.table, payload))
        return self

    def upsert(self, payload, **_k):  # noqa: ANN001, ANN201
        self._op = "upsert"
        self.sb.upserts.append((self.table, payload))
        return self

    def execute(self) -> _Res:
        if self._op in {"update", "upsert"}:
            return _Res([])
        data = self.sb.data.get(self.table)
        if self._single:
            return _Res(data)
        return _Res(data if isinstance(data, list) else ([data] if data else []))


class _FakeSb:
    def __init__(self, *, lead, subject, domain_intel=None, budget_ok: bool = True) -> None:  # noqa: ANN001
        self.data = {"leads": lead, "subjects": subject, "domain_intel": domain_intel}
        self.budget_ok = budget_ok
        self.updates: list[tuple[str, dict]] = []
        self.upserts: list[tuple[str, dict]] = []
        self.rpc_calls = 0

    def table(self, name: str) -> _Q:
        return _Q(self, name)

    def rpc(self, name: str, params: dict):  # noqa: ANN201
        assert name == "reserve_premium_budget"
        self.rpc_calls += 1
        return _RpcRes(self.budget_ok)


def _lead(*, contact_outcome=None):  # noqa: ANN001
    return {"id": "L1", "subject_id": "S1", "contact_outcome": contact_outcome}


def _subject(*, email="info@azienda.it", source=None):  # noqa: ANN001
    return {"id": "S1", "decision_maker_email": email, "decision_maker_email_source": source}


def _wire(monkeypatch, sb, *, mx=True, neverbounce=""):  # noqa: ANN001
    monkeypatch.setattr(cw, "get_service_client", lambda: sb)
    monkeypatch.setattr(cw, "_has_mx_record", lambda domain, **_k: mx)
    monkeypatch.setattr(cw.settings, "neverbounce_api_key", neverbounce)
    monkeypatch.setattr(cw.settings, "max_verifications_per_lead", 6)


def _last_subject_update(sb):  # noqa: ANN001
    for tbl, payload in reversed(sb.updates):
        if tbl == "subjects":
            return payload
    return None


# --------------------------------------------------------------------------- #
# STEP 0 guards
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_already_resolved_is_noop(monkeypatch):
    sb = _FakeSb(lead=_lead(contact_outcome="done"), subject=_subject())
    _wire(monkeypatch, sb)
    out = await cw.resolve_best_contact(tenant_id="t", lead_id="L1")
    assert out.status == "done"
    assert out.reason == "already_resolved"
    assert sb.rpc_calls == 0  # no spend


@pytest.mark.asyncio
async def test_no_mx_needs_manual(monkeypatch):
    sb = _FakeSb(lead=_lead(), subject=_subject())
    _wire(monkeypatch, sb, mx=False)

    async def _au(**_k):
        raise AssertionError("STEP 1 must not run when MX is missing")

    monkeypatch.setattr(cw, "_attempt_upgrade", _au)

    out = await cw.resolve_best_contact(tenant_id="t", lead_id="L1")
    assert out.status == "needs_manual"
    assert out.reason == "no_mx"


@pytest.mark.asyncio
async def test_pec_domain_needs_manual(monkeypatch):
    sb = _FakeSb(lead=_lead(), subject=_subject(email="azienda@pec.it"))
    _wire(monkeypatch, sb)
    out = await cw.resolve_best_contact(tenant_id="t", lead_id="L1")
    assert out.status == "needs_manual"
    assert out.reason == "non_business_or_pec"


# --------------------------------------------------------------------------- #
# STEP 1 — Hunter-first
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_step1_hunter_win_mirrors_subject(monkeypatch):
    sb = _FakeSb(lead=_lead(), subject=_subject())
    _wire(monkeypatch, sb)

    async def _au(**_k):
        return (
            DecisionMakerUpgrade(
                email="mario.rossi@azienda.it",
                name="Mario Rossi",
                role="Direttore",
                confidence="alta",
                fallback_email="info@azienda.it",
            ),
            "ok",
        )

    monkeypatch.setattr(cw, "_attempt_upgrade", _au)

    out = await cw.resolve_best_contact(tenant_id="t", lead_id="L1")
    assert out.status == "done"
    assert out.email == "mario.rossi@azienda.it"
    assert out.kind == "decision_maker"
    su = _last_subject_update(sb)
    assert su["decision_maker_email"] == "mario.rossi@azienda.it"
    assert su["decision_maker_email_source"] == "premium_finder"


@pytest.mark.asyncio
async def test_step1_no_api_key_needs_manual(monkeypatch):
    sb = _FakeSb(lead=_lead(), subject=_subject())
    _wire(monkeypatch, sb)

    async def _au(**_k):
        return (None, "no_api_key")

    monkeypatch.setattr(cw, "_attempt_upgrade", _au)

    out = await cw.resolve_best_contact(tenant_id="t", lead_id="L1")
    assert out.status == "needs_manual"
    assert out.reason == "no_api_key"


# --------------------------------------------------------------------------- #
# STEP 3 — role ladder
# --------------------------------------------------------------------------- #
async def _au_miss(**_k):
    # STEP 1 found nothing (Hunter empty); proceed to STEP 3.
    return (None, "no_named_candidate")


@pytest.mark.asyncio
async def test_step3_catch_all_routes_to_phone_queue(monkeypatch):
    # domain_intel says catch-all → ladder must NOT verify anything.
    sb = _FakeSb(lead=_lead(), subject=_subject(), domain_intel={"catch_all": True})
    _wire(monkeypatch, sb)
    monkeypatch.setattr(cw, "_attempt_upgrade", _au_miss)

    out = await cw.resolve_best_contact(tenant_id="t", lead_id="L1")
    assert out.status == "phone_queue"
    assert out.reason == "catch_all"
    assert sb.rpc_calls == 0  # no blind blasting


@pytest.mark.asyncio
async def test_step3_ladder_first_valid_wins(monkeypatch):
    # Not catch-all; the ladder verifies and the first deliverable wins. With
    # current email info@, the ladder tries ufficiotecnico@ first.
    sb = _FakeSb(lead=_lead(), subject=_subject(), domain_intel={"catch_all": False})
    _wire(monkeypatch, sb)
    monkeypatch.setattr(cw, "_attempt_upgrade", _au_miss)

    async def _vh(email, *, client=None):
        return (
            email.startswith("ufficiotecnico@"),
            "valid" if email.startswith("uffic") else "invalid",
        )

    monkeypatch.setattr(cw, "verify_email_hunter", _vh)

    out = await cw.resolve_best_contact(tenant_id="t", lead_id="L1")
    assert out.status == "done"
    assert out.email == "ufficiotecnico@azienda.it"
    assert out.kind == "role"
    su = _last_subject_update(sb)
    assert su["decision_maker_email"] == "ufficiotecnico@azienda.it"
    assert su["decision_maker_email_source"] == "premium_finder"


@pytest.mark.asyncio
async def test_step3_ladder_exhausted_phone_queue(monkeypatch):
    sb = _FakeSb(lead=_lead(), subject=_subject(), domain_intel={"catch_all": False})
    _wire(monkeypatch, sb)
    monkeypatch.setattr(cw, "_attempt_upgrade", _au_miss)

    async def _vh(email, *, client=None):
        return (False, "invalid")  # nothing deliverable

    monkeypatch.setattr(cw, "verify_email_hunter", _vh)

    out = await cw.resolve_best_contact(tenant_id="t", lead_id="L1")
    assert out.status == "phone_queue"
    assert out.reason == "ladder_exhausted"
    assert "commerciale@azienda.it" not in out.candidates  # never tried


@pytest.mark.asyncio
async def test_step3_unknown_status_is_not_written(monkeypatch):
    # Guessed role@domain that verifies as 'unknown' (Hunter deliverable flag is
    # permissive) must NOT be written/sent — only a strict 'valid' counts.
    sb = _FakeSb(lead=_lead(), subject=_subject(), domain_intel={"catch_all": False})
    _wire(monkeypatch, sb)
    monkeypatch.setattr(cw, "_attempt_upgrade", _au_miss)

    async def _vh(email, *, client=None):
        return (True, "unknown")  # deliverable=True but undecided

    monkeypatch.setattr(cw, "verify_email_hunter", _vh)

    out = await cw.resolve_best_contact(tenant_id="t", lead_id="L1")
    assert out.status == "phone_queue"
    assert out.reason == "ladder_exhausted"
    assert _last_subject_update(sb) is None  # nothing fabricated into subjects


@pytest.mark.asyncio
async def test_step3_budget_cap_stops_ladder(monkeypatch):
    sb = _FakeSb(
        lead=_lead(), subject=_subject(), domain_intel={"catch_all": False}, budget_ok=False
    )
    _wire(monkeypatch, sb)
    monkeypatch.setattr(cw, "_attempt_upgrade", _au_miss)

    async def _vh(email, *, client=None):
        raise AssertionError("verify must not run when budget reserve fails")

    monkeypatch.setattr(cw, "verify_email_hunter", _vh)

    out = await cw.resolve_best_contact(tenant_id="t", lead_id="L1")
    assert out.status == "needs_manual"
    assert out.reason == "budget_exhausted"


@pytest.mark.asyncio
async def test_step3_probe_detects_catch_all(monkeypatch):
    # catch_all unknown (no domain_intel) → 1-probe says accept_all → phone_queue.
    sb = _FakeSb(lead=_lead(), subject=_subject(), domain_intel=None)
    _wire(monkeypatch, sb)
    monkeypatch.setattr(cw, "_attempt_upgrade", _au_miss)

    async def _vh(email, *, client=None):
        # the random probe address verifies as accept_all → catch-all domain
        return (True, "accept_all")

    monkeypatch.setattr(cw, "verify_email_hunter", _vh)

    out = await cw.resolve_best_contact(tenant_id="t", lead_id="L1")
    assert out.status == "phone_queue"
    assert out.reason == "catch_all"
    # one probe charged, then no ladder blasting
    assert sb.rpc_calls == 1
