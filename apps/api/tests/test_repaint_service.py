"""Repaint reuses the stored aerial — no Google Solar — and updates the render.

The second render button: re-paints panels on the bare ``before.png`` already in
Storage (reusing ``roofs.derivations``), so it works even with Solar billing 403
and is cheap. These tests mock the paint/storage/HTTP so no network is touched.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.services import repaint_service
from src.services.repaint_service import RepaintError, repaint_rendering


class _Res:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    def __init__(self, store: dict, table: str, rows: list) -> None:
        self._store = store
        self._table = table
        self._rows = rows

    def select(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def eq(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def limit(self, *_a: Any, **_k: Any) -> _Query:
        return self

    def update(self, payload: dict, *_a: Any, **_k: Any) -> _Query:
        self._store["updates"].append((self._table, payload))
        return self

    def execute(self) -> _Res:
        return _Res(self._rows)


class _Storage:
    def from_(self, _bucket: str) -> _Storage:
        return self

    def get_public_url(self, path: str) -> str:
        return f"https://store.example/{path}"


class _Sb:
    def __init__(self, rows_by_table: dict[str, list], store: dict) -> None:
        self._rows = rows_by_table
        self._store = store

    def table(self, name: str) -> _Query:
        return _Query(self._store, name, self._rows.get(name, []))

    @property
    def storage(self) -> _Storage:
        return _Storage()


class _FakeResp:
    def __init__(self, content: bytes, ok: bool = True) -> None:
        self.content = content
        self._ok = ok

    def raise_for_status(self) -> None:
        if not self._ok:
            raise httpx.HTTPError("404")


def _fake_httpx(resp: _FakeResp) -> Any:
    class _Client:
        def __init__(self, *_a: Any, **_k: Any) -> None: ...
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_a: Any) -> bool:
            return False

        async def get(self, _url: str) -> _FakeResp:
            return resp

    return _Client


def _wire(monkeypatch: Any, *, rows: dict, store: dict, before_ok: bool = True) -> None:
    monkeypatch.setattr(repaint_service, "get_service_client", lambda: _Sb(rows, store))
    monkeypatch.setattr(
        repaint_service.httpx, "AsyncClient", _fake_httpx(_FakeResp(b"AERIAL", before_ok))
    )

    async def _paint(**_k: Any) -> bytes:
        store["painted"] = True
        return b"AFTER"

    monkeypatch.setattr(repaint_service, "generate_after_with_panels", _paint)
    monkeypatch.setattr(repaint_service, "normalize_to_output_dimensions", lambda b: b)
    monkeypatch.setattr(repaint_service, "align_after_to_before", lambda _bef, aft: aft)
    monkeypatch.setattr(repaint_service, "bake_savings_strip", lambda b, **_k: b + b"+STRIP")
    monkeypatch.setattr(
        repaint_service, "upload_bytes", lambda **_k: "https://store.example/after.png"
    )


def _rows() -> dict[str, list]:
    return {
        "leads": [{"id": "L1", "roof_id": "R1"}],
        "roofs": [
            {
                "derivations": {
                    "panel_count": 42,
                    "estimated_kwp": 16.8,
                    "realistic_yearly_savings_eur": 3633,
                }
            }
        ],
        "tenants": [{"brand_primary_color": "#0F766E"}],
    }


async def test_repaint_reuses_aerial_and_updates_render(monkeypatch: Any) -> None:
    store: dict[str, Any] = {"updates": []}
    _wire(monkeypatch, rows=_rows(), store=store)

    out = await repaint_rendering(tenant_id="T1", lead_id="L1")

    assert out["ok"] is True
    assert out["after_url"] == "https://store.example/after.png"
    assert out["panel_count"] == 42
    # Re-painted via Replicate, and the lead's render URL was updated.
    assert store["painted"] is True
    lead_updates = [p for (t, p) in store["updates"] if t == "leads"]
    assert any(
        p.get("rendering_image_url") == "https://store.example/after.png" for p in lead_updates
    )
    # The stale GIF/MP4 (baked from the previous render) must be nulled so the
    # dashboard / email / dossier fall back to the fresh static image, not the
    # old animation. And the cache-bust counter bumps AFTER the upload.
    final = next(p for p in lead_updates if "rendering_image_url" in p)
    assert final["rendering_gif_url"] is None
    assert final["rendering_gif_cdn_url"] is None
    assert final["rendering_video_url"] is None
    assert final["rendering_video_cdn_url"] is None
    assert final["rendering_regen_count"] == 1
    # A successful repaint clears any stale creative failure reason so the lead
    # page stops showing the misleading "Video non generato" chip.
    assert final["creative_skipped_reason"] is None


async def test_repaint_raises_when_no_stored_aerial(monkeypatch: Any) -> None:
    store: dict[str, Any] = {"updates": []}
    _wire(monkeypatch, rows=_rows(), store=store, before_ok=False)

    with pytest.raises(RepaintError):
        await repaint_rendering(tenant_id="T1", lead_id="L1")
    # Never painted and never touched the lead when the aerial is missing.
    assert "painted" not in store
    assert store["updates"] == []


async def test_repaint_unknown_lead_raises(monkeypatch: Any) -> None:
    store: dict[str, Any] = {"updates": []}
    rows = _rows()
    rows["leads"] = []  # lead not found
    _wire(monkeypatch, rows=rows, store=store)

    with pytest.raises(RepaintError):
        await repaint_rendering(tenant_id="T1", lead_id="MISSING")
