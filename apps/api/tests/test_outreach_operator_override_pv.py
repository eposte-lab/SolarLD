"""Operator manual sends bypass the existing-PV gates; the automated funnel
and the public exit-intent do NOT.

The dashboard 'Re-invia (force)', 'Reinvia a un altro indirizzo' and 'Invia
test' buttons set ``operator_override=True`` so a send goes out immediately
even when the roof's PV state is unverified — the Metro Salerno block
(reason='pv_unverified'). The flag is operator-only: the daily pipeline and
the public exit-intent never set it, so they stay fully fail-closed on PV.
"""

from __future__ import annotations

import inspect

from src.agents import outreach
from src.agents.outreach import OutreachInput


def test_operator_override_defaults_false_and_is_its_own_axis() -> None:
    d = OutreachInput(tenant_id="t", lead_id="l")
    assert d.operator_override is False

    o = OutreachInput(tenant_id="t", lead_id="l", operator_override=True)
    assert o.operator_override is True
    # Independent of force — settable on its own.
    assert o.force is False


def test_both_pv_gates_bypass_on_operator_override() -> None:
    src = inspect.getsource(outreach)
    # Confirmed-PV stop bypasses when the operator overrides...
    assert 'if roof.get("has_existing_pv") and not payload.operator_override:' in src
    # ...and the UNVERIFIED stop only BLOCKS when it is NOT an operator override.
    assert "if not payload.operator_override:" in src


def test_unverified_path_always_queues_reverification_even_on_override() -> None:
    src = inspect.getsource(outreach)
    block = src.split('if not roof.get("existing_pv_checked_at"):', 1)[1][:700]
    # The re-verification enqueue precedes the operator-override guard → an
    # override skips only the BLOCK, never the verification hygiene.
    assert block.index("enqueue_pv_reverification(sb") < block.index(
        "if not payload.operator_override:"
    )


def test_operator_endpoints_set_override_but_public_exit_intent_does_not() -> None:
    from src.routes import leads as leads_routes
    from src.routes import public as public_routes

    leads_src = inspect.getsource(leads_routes)
    # 'Reinvia a un altro indirizzo' + 'Invia test' mark the send as operator.
    assert leads_src.count('"operator_override": True') >= 2
    # 'Re-invia (force)' ties the bypass to force → the initial send stays gated.
    assert '"operator_override": force' in leads_src

    # The PUBLIC exit-intent proposal-resend must NOT set operator_override —
    # it stays PV-gated exactly like the automated funnel.
    assert "operator_override" not in inspect.getsource(public_routes)
