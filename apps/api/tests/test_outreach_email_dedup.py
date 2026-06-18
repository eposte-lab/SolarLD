"""_email_already_contacted — dedup safety net for shared chain mailboxes.

A chain whose stores share one central inbox (e.g. info@pro7.it on four
locations) must be pitched at most once. The helper returns True when ANOTHER
lead for the tenant already sent to the same address.
"""

from __future__ import annotations

from typing import Any

from src.agents.outreach import OutreachAgent


class _Res:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def select(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def eq(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def ilike(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def in_(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def neq(self, *_a: Any, **_k: Any) -> _Query:
        return self

    @property
    def not_(self) -> _Query:
        return self

    def is_(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def limit(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def execute(self) -> _Res:
        return _Res(self._rows)


class _Sb:
    """Returns configured rows per table; 'leads' may raise to test fail-open."""

    def __init__(self, subjects: list[dict], leads: list[dict] | Exception) -> None:
        self._subjects = subjects
        self._leads = leads

    def table(self, name: str) -> _Query:
        if name == "subjects":
            return _Query(self._subjects)
        if isinstance(self._leads, Exception):
            raise self._leads
        return _Query(self._leads)


async def _call(subjects: list[dict], leads: list[dict] | Exception) -> bool:
    agent = OutreachAgent()
    return await agent._email_already_contacted(
        _Sb(subjects, leads), tenant_id="t", recipient="info@pro7.it", lead_id="L1"
    )


async def test_single_subject_is_not_a_duplicate() -> None:
    # Only this lead's subject uses the address → not shared, send it.
    assert await _call([{"id": "s1"}], []) is False


async def test_shared_mailbox_with_a_sent_sibling_is_duplicate() -> None:
    # Four stores share the email and one already sent → block this one.
    assert await _call([{"id": "s1"}, {"id": "s2"}, {"id": "s3"}], [{"id": "L9"}]) is True


async def test_shared_mailbox_but_nobody_sent_yet_is_allowed() -> None:
    assert await _call([{"id": "s1"}, {"id": "s2"}], []) is False


async def test_query_error_fails_open() -> None:
    # A dedup query error must never block a real send.
    assert await _call([{"id": "s1"}, {"id": "s2"}], RuntimeError("boom")) is False
