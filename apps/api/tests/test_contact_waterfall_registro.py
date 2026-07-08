"""Contact waterfall — registro-first mirror behaviour (Modifica 1).

The registro step persists the decision-maker NAME up front; a later role-only
win (STEP 3, name=None) must NOT wipe it. These lock that + the registro write.
"""

from __future__ import annotations

from typing import Any

from src.services.contact_waterfall import _mirror_registro_name, _mirror_to_subject
from src.services.openapi_company_service import RegistroDecisionMaker


class _FakeTable:
    def __init__(self, sink: dict[str, Any]) -> None:
        self._sink = sink

    def update(self, payload: dict[str, Any]) -> _FakeTable:
        self._sink["payload"] = payload
        return self

    def eq(self, *_: Any) -> _FakeTable:
        return self

    def execute(self) -> None:
        return None


class _FakeSb:
    def __init__(self) -> None:
        self.sink: dict[str, Any] = {}

    def table(self, _name: str) -> _FakeTable:
        return _FakeTable(self.sink)


def test_mirror_to_subject_keeps_existing_name_on_role_only_win() -> None:
    sb = _FakeSb()
    # STEP 3 role-ladder win passes name=None → must not null decision_maker_name
    _mirror_to_subject(
        sb, "sid", email="acquisti@x.it", name=None, role="acquisti", fallback="i@x.it"
    )
    payload = sb.sink["payload"]
    assert "decision_maker_name" not in payload  # preserved (not overwritten with None)
    assert payload["decision_maker_email"] == "acquisti@x.it"
    assert payload["decision_maker_email_source"] == "premium_finder"  # warehouse premium ordering
    assert payload["decision_maker_role"] == "acquisti"


def test_mirror_to_subject_sets_name_when_present() -> None:
    sb = _FakeSb()
    _mirror_to_subject(
        sb, "sid", email="d.mele@x.it", name="Dante Mele", role=None, fallback="i@x.it"
    )
    assert sb.sink["payload"]["decision_maker_name"] == "Dante Mele"


def test_mirror_registro_name_writes_provenance_without_touching_email() -> None:
    sb = _FakeSb()
    dm = RegistroDecisionMaker(
        full_name="Dante Mele",
        first_name="Dante",
        last_name="Mele",
        role="Amministratore unico",
        role_code="AUN",
        confidence="alta",
        is_legal_rep=True,
    )
    _mirror_registro_name(sb, "sid", dm)
    payload = sb.sink["payload"]
    assert payload["decision_maker_name"] == "Dante Mele"
    assert payload["decision_maker_role"] == "Amministratore unico"
    assert payload["decision_maker_source"] == "registro"
    assert payload["decision_maker_confidence"] == "alta"
    # never touches the email fields → the send recipient is unchanged
    assert "decision_maker_email" not in payload
    assert "decision_maker_email_source" not in payload
