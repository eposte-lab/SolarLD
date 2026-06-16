"""Regression tests for CreativeAgent silent-failure handling.

The render pipeline must NEVER leave a lead in the NULL/NULL state
(``rendering_image_url IS NULL`` AND ``creative_skipped_reason IS NULL``).
That state is invisible to the operator, stuck behind the ``render_not_ready``
send gate, and excluded from the regenerate-failed-renders route. Any
unexpected render/upload failure must instead record a ``creative_skipped_reason``
so the lead stays visible and re-renderable.

Everything (Supabase, Google Solar, Mapbox, Storage upload, paint) is
monkeypatched — no network, no DB.
"""

from __future__ import annotations

import pytest

from src.agents import creative as creative_mod
from src.agents.creative import CreativeAgent, CreativeInput


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _Res:
    def __init__(self, data) -> None:  # noqa: ANN001
        self.data = data


class _Q:
    def __init__(self, sb: _FakeSb, table: str) -> None:
        self.sb = sb
        self.table = table
        self._op = "select"
        self._single = False

    def select(self, *_a, **_k):  # noqa: ANN201
        return self

    def eq(self, *_a, **_k):  # noqa: ANN201
        return self

    def is_(self, *_a, **_k):  # noqa: ANN201
        return self

    def not_in(self, *_a, **_k):  # noqa: ANN201
        return self

    def limit(self, *_a, **_k):  # noqa: ANN201
        return self

    def single(self):  # noqa: ANN201
        self._single = True
        return self

    def maybe_single(self):  # noqa: ANN201
        self._single = True
        return self

    def insert(self, payload):  # noqa: ANN001, ANN201
        self._op = "insert"
        self.sb.inserts.append((self.table, payload))
        return self

    def update(self, payload):  # noqa: ANN001, ANN201
        self._op = "update"
        self.sb.updates.append((self.table, payload))
        return self

    def execute(self) -> _Res:
        if self._op in {"update", "insert"}:
            return _Res([])
        data = self.sb.data.get(self.table)
        if self._single:
            return _Res(data)
        return _Res(data if isinstance(data, list) else ([data] if data else []))


class _FakeSb:
    def __init__(self, *, lead, roof, subject, tenant) -> None:  # noqa: ANN001
        # _load_single does .select().eq().eq().limit().execute() → expects a list
        self.data = {
            "leads": [lead],
            "roofs": [roof],
            "subjects": [subject],
            "tenants": tenant,  # tenant fetch uses .single() → bare dict
        }
        self.updates: list[tuple[str, dict]] = []
        self.inserts: list[tuple[str, dict]] = []

    def table(self, name: str) -> _Q:
        return _Q(self, name)


def _lead():  # noqa: ANN201
    return {
        "id": "L1",
        "roof_id": "R1",
        "subject_id": "S1",
        "rendering_image_url": None,
        "roi_data": None,
    }


def _roof():  # noqa: ANN201
    return {
        "id": "R1",
        "lat": 40.85,
        "lng": 14.27,
        "estimated_kwp": 50.0,
        "estimated_yearly_kwh": 60000.0,
        "status": "scored",
    }


def _subject():  # noqa: ANN201
    # high-confidence, trusted source → passes the operating-site gate
    return {
        "id": "S1",
        "type": "business",
        "sede_operativa_confidence": "high",
        "sede_operativa_source": "user_confirmed",
    }


def _wire(monkeypatch, sb) -> None:  # noqa: ANN001
    monkeypatch.setattr(creative_mod, "get_service_client", lambda: sb)
    # Pass the pre-render gates: Solar key + Replicate token present.
    monkeypatch.setattr(creative_mod.settings, "google_solar_api_key", "test-key")
    monkeypatch.setattr(creative_mod.settings, "google_solar_mock_mode", False)
    monkeypatch.setattr(creative_mod.settings, "replicate_api_token", "test-token")
    monkeypatch.setattr(creative_mod.settings, "creative_skip_replicate", False)

    class _Insight:
        panels: list = []
        max_panel_count = 100
        estimated_kwp = 50.0
        estimated_yearly_kwh = 60000.0
        area_sqm = 400.0
        panel_capacity_w = 400
        panel_width_m = 1.0
        panel_height_m = 1.7
        dominant_exposure = "S"
        pitch_degrees = 15.0
        shading_score = 0.1

    async def _fake_insight(_lat, _lng, *, client=None):  # noqa: ANN001, ANN202
        return _Insight()

    async def _fake_before(_lat, _lng, _insight, *, api_key=None):  # noqa: ANN001, ANN202
        return b"before-bytes"

    monkeypatch.setattr(creative_mod, "fetch_building_insight", _fake_insight)
    monkeypatch.setattr(creative_mod, "render_before_only", _fake_before)


def _last_lead_update(sb: _FakeSb) -> dict | None:
    for tbl, payload in reversed(sb.updates):
        if tbl == "leads":
            return payload
    return None


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_storage_upload_failure_records_reason_not_crash(monkeypatch) -> None:
    """upload_bytes raising a non-classified error must become a recorded skip.

    This is the exact silent-failure mode that stranded the trial's leads:
    the Supabase Storage upload raised a generic error that none of the
    inner ``except`` clauses caught. Before the fix it propagated and left
    the lead NULL/NULL; now it must persist a ``creative_skipped_reason``.
    """
    sb = _FakeSb(lead=_lead(), roof=_roof(), subject=_subject(), tenant={"id": "T1"})
    _wire(monkeypatch, sb)

    def _boom_upload(**_k):  # noqa: ANN003, ANN202
        raise RuntimeError("supabase storage 503")

    monkeypatch.setattr(creative_mod, "upload_bytes", _boom_upload)

    # Must NOT raise.
    out = await CreativeAgent().execute(CreativeInput(tenant_id="T1", lead_id="L1", force=True))

    assert out.skipped is True
    upd = _last_lead_update(sb)
    assert upd is not None, "expected a leads UPDATE persisting the skip"
    assert upd.get("rendering_image_url") is None
    reason = upd.get("creative_skipped_reason")
    assert reason, "creative_skipped_reason must be set, not left NULL (silent failure)"
    # Graceful inner catch-all classifies the upload fault.
    assert "RuntimeError" in reason or reason.startswith("render_")


@pytest.mark.asyncio
async def test_load_failure_backstop_marks_lead(monkeypatch) -> None:
    """A failure in the un-wrapped load region hits the method-wide backstop.

    The tenant ``.single()`` fetch (and the loads) run outside every inner
    try. A raise there used to propagate to a silent NULL/NULL; the
    ``execute`` wrapper must convert it to a recorded skip.
    """
    sb = _FakeSb(lead=_lead(), roof=_roof(), subject=_subject(), tenant={"id": "T1"})
    _wire(monkeypatch, sb)

    # Make the tenant fetch explode by having .single() raise via a bad table.
    real_table = sb.table

    def _table(name: str):  # noqa: ANN202
        q = real_table(name)
        if name == "tenants":

            def _raise_single():  # noqa: ANN202
                raise RuntimeError("PostgREST: multiple rows returned")

            q.single = _raise_single  # type: ignore[method-assign]
        return q

    monkeypatch.setattr(sb, "table", _table)

    out = await CreativeAgent().execute(CreativeInput(tenant_id="T1", lead_id="L1", force=True))

    assert out.skipped is True
    assert out.reason is not None
    assert out.reason.startswith("render_error:")
    upd = _last_lead_update(sb)
    assert upd is not None
    assert upd.get("creative_skipped_reason", "").startswith("render_error:")
