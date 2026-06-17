"""Regression: the NeverBounce send-gate reads `.result.sendable`, not `.sendable`.

Bug (2026-06-17): outreach.py:696 evaluated ``not nb_result.sendable`` but
``sendable`` is a property of the ``VerificationResult`` enum, NOT of the
``EmailVerification`` dataclass (which is ``slots=True`` → has no such attr).
Every send that reached the NeverBounce check (i.e. every lead with a valid
email, sequence_step 1, NB key configured) raised:

    AttributeError: 'EmailVerification' object has no attribute 'sendable'

so the outreach_task crashed and ZERO of the good leads shipped. The bug was
dormant until NeverBounce was configured. Correct access is
``nb_result.result.sendable``. This test locks the contract.
"""

from __future__ import annotations

from src.services.neverbounce_service import EmailVerification, VerificationResult


def _verification(result: VerificationResult) -> EmailVerification:
    return EmailVerification(
        email="info@example.it",
        result=result,
        role_address=False,
        free_email=False,
        disposable=False,
        raw={},
    )


def test_sendable_lives_on_result_enum_not_on_the_object() -> None:
    v = _verification(VerificationResult.VALID)
    # The EmailVerification object itself has NO `.sendable` (slots dataclass).
    assert not hasattr(v, "sendable")
    # The correct path — exactly what outreach.py relies on.
    assert v.result.sendable is True


def test_result_sendable_matches_valid_and_catchall() -> None:
    assert _verification(VerificationResult.VALID).result.sendable is True
    assert _verification(VerificationResult.CATCHALL).result.sendable is True
    assert _verification(VerificationResult.INVALID).result.sendable is False
    assert _verification(VerificationResult.DISPOSABLE).result.sendable is False
    assert _verification(VerificationResult.UNKNOWN).result.sendable is False


def test_outreach_uses_result_sendable_not_object_sendable() -> None:
    """Guard the exact outreach.py expression against re-introducing the bug."""
    import inspect

    from src.agents import outreach

    src = inspect.getsource(outreach)
    # The fixed form must be present; the buggy bare-attribute form must not.
    assert "nb_result.result.sendable" in src
    assert "nb_result.sendable" not in src
