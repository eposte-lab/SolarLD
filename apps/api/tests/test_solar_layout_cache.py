"""Tests for the solar-layout endpoint's cache-existence helper.

The endpoint regenerates the deterministic Solar-API panel overlay only on a
cache MISS, so this helper deciding hit/miss correctly is what keeps repeat
opens free. It must also fail-safe to "miss" (regenerate) on any storage error
rather than raising into the request.
"""

from __future__ import annotations

from src.routes.leads import _renderings_object_exists


class _FakeBucket:
    def __init__(self, listing):
        self._listing = listing

    def list(self, folder):  # noqa: ARG002 — folder unused by the fake
        if isinstance(self._listing, Exception):
            raise self._listing
        return self._listing


class _FakeStorage:
    def __init__(self, listing):
        self._listing = listing

    def from_(self, bucket):  # noqa: ARG002 — bucket unused by the fake
        return _FakeBucket(self._listing)


class _FakeSb:
    def __init__(self, listing):
        self.storage = _FakeStorage(listing)


def test_hit_when_object_present():
    sb = _FakeSb([{"name": "before.png"}, {"name": "solar_layout.png"}])
    assert _renderings_object_exists(sb, "t/l", "solar_layout.png") is True


def test_miss_when_object_absent():
    sb = _FakeSb([{"name": "before.png"}, {"name": "after.png"}])
    assert _renderings_object_exists(sb, "t/l", "solar_layout.png") is False


def test_miss_on_empty_folder():
    sb = _FakeSb([])
    assert _renderings_object_exists(sb, "t/l", "solar_layout.png") is False


def test_failsafe_to_miss_on_storage_error():
    # A storage error must NOT raise into the request — treat as a miss so the
    # overlay regenerates rather than 500ing.
    sb = _FakeSb(RuntimeError("storage down"))
    assert _renderings_object_exists(sb, "t/l", "solar_layout.png") is False
