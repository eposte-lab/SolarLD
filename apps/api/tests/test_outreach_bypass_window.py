"""Manual 'Avvia invii ora' bypasses ONLY the send window, never re-sends.

Bug (2026-06-17): the manual send enqueued outreach with force=True, which
bypasses the window AND the already-sent dedupe (outreach.py: `if
outreach_sent_at and not payload.force`). A lead that erroneously sat in
ready_to_send got a duplicate first-touch (Palazzo Caracciolo). The manual
button now uses `bypass_window` — independent of `force` — so the window is
bypassed but the dedupe and GDPR/branding gates stay active.
"""

from __future__ import annotations

import inspect

from src.agents import outreach
from src.agents.outreach import OutreachInput


def test_bypass_window_is_independent_of_force() -> None:
    b = OutreachInput(tenant_id="t", lead_id="l", bypass_window=True)
    assert b.bypass_window is True
    # force stays False → the already-sent dedupe remains active → no re-send.
    assert b.force is False

    f = OutreachInput(tenant_id="t", lead_id="l", force=True)
    assert f.force is True
    assert f.bypass_window is False

    d = OutreachInput(tenant_id="t", lead_id="l")
    assert d.force is False
    assert d.bypass_window is False


def test_window_gate_honours_bypass_window_but_dedupe_keys_on_force() -> None:
    src = inspect.getsource(outreach)
    # The window gate must bypass when bypass_window is set.
    assert "not payload.bypass_window" in src
    # The already-sent dedupe must STILL key on force only (not bypass_window),
    # so a manual (bypass_window) send can never re-send an already-contacted lead.
    assert 'if lead.get("outreach_sent_at") and not payload.force:' in src
