"""L6 synchronous contact-validation gate (#3).

The territory scan's validated cap must count only leads that come out of
the full contact waterfall with a DELIVERABLE address. `_contact_is_deliverable`
is the gate predicate applied to the `ContactOutcome`: every fail-open
terminal state (premium win, or preserved website email on an MX-valid
domain) counts; only no-MX / no-domain / PEC / lead-not-found do not.
"""

from __future__ import annotations

import pytest

from src.agents.hunter_funnel.level6_promote_to_leads import _contact_is_deliverable
from src.services.contact_waterfall import ContactOutcome


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("done", "hunter_decision_maker"),
        ("done", "already_resolved"),
        ("done", "already_premium"),
        ("done_unverified", "catch_all"),
        ("phone_queue", "ladder_exhausted"),  # keeps website email → sendable
        ("needs_manual", "budget_exhausted"),  # MX passed before budget check
        ("needs_manual", "budget_check_failed"),
        ("needs_manual", "no_api_key"),
    ],
)
def test_deliverable_states_count(status, reason) -> None:
    outcome = ContactOutcome(status=status, reason=reason, email="info@azienda.it")
    assert _contact_is_deliverable(outcome) is True


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("needs_manual", "no_mx"),  # domain can't receive mail
        ("needs_manual", "no_domain"),  # no email at all
        ("needs_manual", "non_business_or_pec"),  # PEC / non-business → never mail
        ("failed", "lead_not_found"),
        ("failed", "subject_not_found"),
    ],
)
def test_non_deliverable_states_do_not_count(status, reason) -> None:
    outcome = ContactOutcome(status=status, reason=reason)
    assert _contact_is_deliverable(outcome) is False


def test_national_chain_needs_manual_is_not_special_cased() -> None:
    # chain leads are already excluded at _is_perfect, but if one reached
    # the waterfall and tripped national_chain, it keeps the website email
    # (not in the non-deliverable set) → treated as deliverable. This is
    # intentional: the chain exclusion lives upstream, not here.
    outcome = ContactOutcome(status="needs_manual", reason="national_chain", email="info@x.it")
    assert _contact_is_deliverable(outcome) is True
