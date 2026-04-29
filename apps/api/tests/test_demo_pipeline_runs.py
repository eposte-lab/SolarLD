"""Unit tests for the demo_pipeline_runs helpers in routes/demo.py.

These cover the two pure-helper code paths that the dashboard polling
loop depends on:

  * ``_create_run`` — must always return a string id (or empty string
    on insert failure) and write a 'scoring' status, so the dialog
    polling loop has something to poll for.
  * ``_update_run`` — must be a no-op when the run_id is empty, must
    truncate over-long error / notes fields to avoid bloating the
    row, and must collapse ``status='failed'`` + an error_message
    into the same UPDATE so the dialog sees a coherent "failed with
    reason X" snapshot rather than two staggered states.
  * ``_lookup_mock_enrichment`` — must return None for un-seeded
    VATs without raising, so demos for VATs not in the mock table
    fall through cleanly to the standard "leave nulls" path.

Supabase is stubbed; every mock call records its arguments so the
test can assert payload shape without standing up a real client.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Stub Supabase client that records calls for assertion
# ---------------------------------------------------------------------------


class _StubExecute:
    def __init__(self, data: Any) -> None:
        self.data = data


class _StubQuery:
    """Captures the chain ``.insert(payload).execute()`` /
    ``.update(payload).eq(col, val).execute()`` /
    ``.select(cols).eq(col, val).maybe_single().execute()``."""

    def __init__(self, parent: "_StubTable") -> None:
        self._parent = parent

    def insert(self, payload: dict[str, Any]) -> "_StubQuery":
        self._parent.last_insert = payload
        # demo_pipeline_runs INSERT returns the new row.
        self._parent._next_data = [{"id": "RUN-UUID-123", **payload}]
        return self

    def update(self, payload: dict[str, Any]) -> "_StubQuery":
        self._parent.last_update = payload
        return self

    def select(self, _cols: str) -> "_StubQuery":
        return self

    def eq(self, _col: str, _val: Any) -> "_StubQuery":
        return self

    def limit(self, _n: int) -> "_StubQuery":
        return self

    def maybe_single(self) -> "_StubQuery":
        return self

    def execute(self) -> _StubExecute:
        return _StubExecute(self._parent._next_data)


class _StubTable:
    def __init__(self, name: str) -> None:
        self.name = name
        self.last_insert: dict[str, Any] | None = None
        self.last_update: dict[str, Any] | None = None
        self._next_data: Any = None

    def query(self) -> _StubQuery:
        return _StubQuery(self)

    # Supabase chains start with table().insert/select/update — we
    # forward the chain straight onto the stub query.
    def insert(self, payload):
        return self.query().insert(payload)

    def update(self, payload):
        return self.query().update(payload)

    def select(self, cols):
        return self.query().select(cols)


class _StubSupabase:
    def __init__(self) -> None:
        self.tables: dict[str, _StubTable] = {}

    def table(self, name: str) -> _StubTable:
        if name not in self.tables:
            self.tables[name] = _StubTable(name)
        return self.tables[name]


# ---------------------------------------------------------------------------
# _create_run
# ---------------------------------------------------------------------------


def test_create_run_writes_scoring_status_and_returns_id() -> None:
    from src.routes import demo as demo_route

    stub = _StubSupabase()
    with patch.object(demo_route, "get_service_client", return_value=stub):
        run_id = demo_route._create_run("tenant-abc")

    assert run_id == "RUN-UUID-123"
    runs = stub.tables["demo_pipeline_runs"]
    assert runs.last_insert == {"tenant_id": "tenant-abc", "status": "scoring"}


def test_create_run_returns_empty_string_when_insert_yields_no_rows() -> None:
    from src.routes import demo as demo_route

    stub = _StubSupabase()
    # Pre-empt the auto-populated INSERT result with an empty list to
    # simulate a row-level RLS reject or constraint silent-fail.
    table = stub.table("demo_pipeline_runs")
    original_insert = table.insert

    def _empty_insert(payload):
        chain = original_insert(payload)
        table._next_data = []  # override the stub's auto-fill
        return chain

    table.insert = _empty_insert  # type: ignore[assignment]

    with patch.object(demo_route, "get_service_client", return_value=stub):
        run_id = demo_route._create_run("tenant-abc")

    assert run_id == ""


# ---------------------------------------------------------------------------
# _update_run
# ---------------------------------------------------------------------------


def test_update_run_no_op_when_run_id_empty() -> None:
    """Empty run_id must short-circuit BEFORE we try to call Supabase."""
    from src.routes import demo as demo_route

    stub = _StubSupabase()
    with patch.object(demo_route, "get_service_client", return_value=stub):
        demo_route._update_run("", status="failed", error_message="boom")

    # No table operations should have been recorded.
    assert "demo_pipeline_runs" not in stub.tables


def test_update_run_truncates_error_message_at_500_chars() -> None:
    from src.routes import demo as demo_route

    stub = _StubSupabase()
    long_err = "x" * 1000
    with patch.object(demo_route, "get_service_client", return_value=stub):
        demo_route._update_run(
            "RUN-UUID-123",
            status="failed",
            failed_step="creative",
            error_message=long_err,
        )

    update = stub.tables["demo_pipeline_runs"].last_update
    assert update is not None
    assert update["status"] == "failed"
    assert update["failed_step"] == "creative"
    # Error message truncated to 500 chars.
    assert len(update["error_message"]) == 500


def test_update_run_skips_request_when_no_payload() -> None:
    """Calling with all kwargs None should NOT issue a Supabase update."""
    from src.routes import demo as demo_route

    stub = _StubSupabase()
    with patch.object(demo_route, "get_service_client", return_value=stub):
        demo_route._update_run("RUN-UUID-123")

    # Table is created lazily on first access; helper should bail out
    # before that happens.
    assert "demo_pipeline_runs" not in stub.tables


def test_update_run_truncates_notes_field() -> None:
    from src.routes import demo as demo_route

    stub = _StubSupabase()
    long_note = "n" * 800
    with patch.object(demo_route, "get_service_client", return_value=stub):
        demo_route._update_run("RUN-UUID-123", notes=long_note)

    update = stub.tables["demo_pipeline_runs"].last_update
    assert update is not None
    assert "notes" in update
    assert len(update["notes"]) == 500


# ---------------------------------------------------------------------------
# _lookup_mock_enrichment
# ---------------------------------------------------------------------------


class _MockEnrichmentTable(_StubTable):
    """Stub that returns a canned dict for one VAT and None for others."""

    def __init__(self, name: str, vat_to_data: dict[str, dict[str, Any]]) -> None:
        super().__init__(name)
        self._map = vat_to_data
        self._pending_vat: str | None = None

    def select(self, cols: str) -> "_StubQuery":
        return _MockEnrichmentQuery(self)


class _MockEnrichmentQuery(_StubQuery):
    def eq(self, col: str, val: Any) -> "_MockEnrichmentQuery":
        if col == "vat_number":
            assert isinstance(self._parent, _MockEnrichmentTable)
            data = self._parent._map.get(str(val))
            self._parent._next_data = data
        return self


def test_lookup_mock_enrichment_returns_none_for_unseeded_vat() -> None:
    from src.routes import demo as demo_route

    stub = _StubSupabase()
    stub.tables["demo_mock_enrichment"] = _MockEnrichmentTable(
        "demo_mock_enrichment", vat_to_data={"09881610019": {"foo": "bar"}}
    )

    with patch.object(demo_route, "get_service_client", return_value=stub):
        # Un-seeded VAT — the function must NOT raise.
        result = demo_route._lookup_mock_enrichment("00000000000")

    assert result is None
