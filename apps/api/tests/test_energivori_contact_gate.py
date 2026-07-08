"""Energivori Delta 2 — the contact GATE pure logic (Changes C+D)."""

from __future__ import annotations

from src.services.decision_maker_name import PersonName
from src.services.energivori_contact_gate import build_candidates, evaluate_gate
from src.services.neverbounce_service import EmailVerification, VerificationResult


def _v(email: str, result: VerificationResult) -> EmailVerification:
    return EmailVerification(
        email=email,
        result=result,
        role_address=False,
        free_email=False,
        disposable=False,
        raw={},
    )


def _cands() -> list[tuple[str, str]]:
    return build_candidates(PersonName(first="Dante", last="Mele"), "x.it", "{first}.{last}")


# --- build_candidates ---------------------------------------------------------


def test_build_candidates_pattern_first_then_permutations() -> None:
    cands = _cands()
    assert cands[0] == ("dante.mele@x.it", "pattern")  # Hunter pattern wins the top slot
    emails = [e for e, _ in cands]
    assert "dmele@x.it" in emails or "d.mele@x.it" in emails  # permutations present
    assert len(cands) <= 6
    assert len(emails) == len(set(emails))  # deduped


def test_build_candidates_no_pattern_uses_permutations() -> None:
    cands = build_candidates(PersonName(first="Dante", last="Mele"), "x.it", None)
    assert all(src == "permutation" for _, src in cands)
    assert cands  # non-empty


# --- evaluate_gate ------------------------------------------------------------


def test_gate_drop_when_no_decision_maker() -> None:
    r = evaluate_gate(
        decision_maker=None,
        dm_source=None,
        domain="x.it",
        candidates=[],
        verifications={},
        accept_all=False,
        acceptall_as_medium=True,
    )
    assert r.passed is False
    assert r.excluded_reason == "no_decision_maker"


def test_gate_drop_when_no_domain() -> None:
    r = evaluate_gate(
        decision_maker="Dante Mele",
        dm_source="registro",
        domain=None,
        candidates=[],
        verifications={},
        accept_all=False,
        acceptall_as_medium=True,
    )
    assert r.excluded_reason == "no_domain"


def test_gate_pass_on_valid_personal_email() -> None:
    cands = _cands()
    verifs = {cands[0][0]: _v(cands[0][0], VerificationResult.VALID)}
    r = evaluate_gate(
        decision_maker="Dante Mele",
        dm_source="registro",
        domain="x.it",
        candidates=cands,
        verifications=verifs,
        accept_all=False,
        acceptall_as_medium=True,
    )
    assert r.passed is True
    assert r.email == cands[0][0]
    assert r.email_status == "valid"
    assert r.email_confidence == "alta"
    assert r.email_source == "verified"


def test_gate_pass_medium_on_accept_all_domain() -> None:
    cands = _cands()
    # nothing valid, but the domain is accept-all → most-probable permutation passes at media
    verifs = {e: _v(e, VerificationResult.CATCHALL) for e, _ in cands}
    r = evaluate_gate(
        decision_maker="Dante Mele",
        dm_source="registro",
        domain="x.it",
        candidates=cands,
        verifications=verifs,
        accept_all=True,
        acceptall_as_medium=True,
    )
    assert r.passed is True
    assert r.email == cands[0][0]
    assert r.email_status == "accept_all"
    assert r.email_confidence == "media"
    assert r.email_source == "pattern"


def test_gate_accept_all_but_all_invalid_drops() -> None:
    # Hunter says accept-all, but NeverBounce marked EVERY candidate invalid →
    # the domain isn't really catch-all → DROP (don't send to bouncing addrs).
    cands = _cands()
    verifs = {e: _v(e, VerificationResult.INVALID) for e, _ in cands}
    r = evaluate_gate(
        decision_maker="Dante Mele",
        dm_source="registro",
        domain="x.it",
        candidates=cands,
        verifications=verifs,
        accept_all=True,
        acceptall_as_medium=True,
    )
    assert r.passed is False
    assert r.excluded_reason == "generic_email_only"


def test_gate_accept_all_skips_invalid_picks_catchall() -> None:
    # First candidate invalid, second catch-all → pass the second at media.
    cands = _cands()
    verifs = {cands[0][0]: _v(cands[0][0], VerificationResult.INVALID)}
    verifs[cands[1][0]] = _v(cands[1][0], VerificationResult.CATCHALL)
    r = evaluate_gate(
        decision_maker="Dante Mele",
        dm_source="registro",
        domain="x.it",
        candidates=cands,
        verifications=verifs,
        accept_all=True,
        acceptall_as_medium=True,
    )
    assert r.passed is True
    assert r.email == cands[1][0]  # skipped the invalid one
    assert r.email_confidence == "media"


def test_gate_drop_generic_when_nothing_valid_smart() -> None:
    cands = _cands()
    verifs = {e: _v(e, VerificationResult.INVALID) for e, _ in cands}
    r = evaluate_gate(
        decision_maker="Dante Mele",
        dm_source="registro",
        domain="x.it",
        candidates=cands,
        verifications=verifs,
        accept_all=False,
        acceptall_as_medium=True,
    )
    assert r.passed is False
    assert r.excluded_reason == "generic_email_only"


def test_gate_drop_strict_mode() -> None:
    cands = _cands()
    # strict mode: even an accept-all domain is not enough — only 'valid' passes
    verifs = {e: _v(e, VerificationResult.CATCHALL) for e, _ in cands}
    r = evaluate_gate(
        decision_maker="Dante Mele",
        dm_source="registro",
        domain="x.it",
        candidates=cands,
        verifications=verifs,
        accept_all=True,
        acceptall_as_medium=False,
    )
    assert r.passed is False
    assert r.excluded_reason == "unverifiable_strict"
