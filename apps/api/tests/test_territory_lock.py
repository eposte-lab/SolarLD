"""Territory lock — unit + route tests.

Covers the exclusivity-lock semantics introduced in Sprint 1:

  1. ``territory_lock_service`` primitives
     - ``is_locked`` reads ``tenants.territory_locked_at``.
     - ``require_unlocked`` raises 423 when locked, 200 when not.
     - ``reject_geo_change`` fires only for the three frozen fields
       (``regioni``/``province``/``cap``); ATECO / employees / revenue
       edits remain free post-lock.

  2. ``POST /v1/territories`` and ``DELETE /v1/territories/:id``
     return 423 once the tenant is locked; the underlying DB op never
     fires (verified via insertion/deletion counters on the fake SB).

  3. ``PUT /v1/modules/sorgente`` returns 423 if the caller tries to
     change a frozen field; the same call mutating only ATECO codes
     succeeds with 200 (though we don't assert the full upsert path —
     the integration suite owns that).

  4. ``POST /v1/admin/tenants/:id/territory-unlock`` requires
     ``role == 'super_admin'``; a plain tenant member receives 403.

  5. ``POST /v1/onboarding/territory-confirm`` writes
     ``territory_locked_at`` via ``lock()`` and is idempotent.

The tests pair a small ``_LockFakeSupabase`` with ``TestClient`` +
``app.dependency_overrides[get_current_user]``.  Every call site that
reads ``tenants.territory_locked_at`` is monkeypatched to go through
this fake rather than hit the real DB.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.core.security import AuthContext, get_current_user
from src.main import app

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TENANT = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
USER = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"
TERRITORY_ID = "cccccccc-cccc-4ccc-cccc-cccccccccccc"


# ---------------------------------------------------------------------------
# Fake Supabase — minimal chain that honours the handful of table ops the
# lock code path exercises: select(tenants), update(tenants), insert/delete
# (territories), select/upsert (tenant_modules).
# ---------------------------------------------------------------------------


@dataclass
class _Result:
    data: list[dict[str, Any]] = field(default_factory=list)
    count: int | None = None


class _Chain:
    def __init__(self, sb: "_LockFakeSupabase", table: str) -> None:
        self._sb = sb
        self._table = table
        self._op = "select"
        self._payload: Any = None
        self._filters: dict[str, Any] = {}
        self._is_maybe_single = False

    # terminal ops
    def select(self, *_a: Any, **_k: Any) -> "_Chain":
        self._op = "select"
        return self

    def insert(self, row: dict[str, Any]) -> "_Chain":
        self._op = "insert"
        self._payload = row
        return self

    def update(self, row: dict[str, Any]) -> "_Chain":
        self._op = "update"
        self._payload = row
        return self

    def upsert(self, row: dict[str, Any], **_k: Any) -> "_Chain":
        self._op = "upsert"
        self._payload = row
        return self

    def delete(self) -> "_Chain":
        self._op = "delete"
        return self

    # filters — eq tracked, rest no-op
    def eq(self, field_name: str, value: Any, *_a: Any, **_k: Any) -> "_Chain":
        self._filters[field_name] = value
        return self

    def neq(self, *_a: Any, **_k: Any) -> "_Chain":
        return self

    def in_(self, *_a: Any, **_k: Any) -> "_Chain":
        return self

    def is_(self, *_a: Any, **_k: Any) -> "_Chain":
        return self

    def or_(self, *_a: Any, **_k: Any) -> "_Chain":
        return self

    def order(self, *_a: Any, **_k: Any) -> "_Chain":
        return self

    def limit(self, *_a: Any, **_k: Any) -> "_Chain":
        return self

    def range(self, *_a: Any, **_k: Any) -> "_Chain":
        return self

    def maybe_single(self, *_a: Any, **_k: Any) -> "_Chain":
        self._is_maybe_single = True
        return self

    def single(self, *_a: Any, **_k: Any) -> "_Chain":
        return self

    def execute(self) -> _Result:
        return self._sb._dispatch(self)


class _LockFakeSupabase:
    """Tracks a single tenant row (with mutable lock fields), a list of
    territories, and a per-key tenant_modules config.

    Exposes counters so tests can assert the lock gate prevented the
    underlying write from actually reaching the "DB" layer.
    """

    def __init__(
        self,
        *,
        tenant_id: str = TENANT,
        locked: bool = False,
        sorgente_config: dict[str, Any] | None = None,
    ) -> None:
        self.tenant_row: dict[str, Any] = {
            "id": tenant_id,
            "territory_locked_at": (
                "2024-01-01T00:00:00+00:00" if locked else None
            ),
            "territory_locked_by": USER if locked else None,
        }
        self.territories: list[dict[str, Any]] = [
            {"id": TERRITORY_ID, "tenant_id": tenant_id, "type": "regione",
             "code": "15", "name": "Campania"},
        ]
        self.sorgente_config: dict[str, Any] = sorgente_config or {
            "mode": "b2b_funnel_v2",
            "ateco_codes": ["10.51"],
            "regioni": ["Campania"],
            "province": [],
            "cap": [],
        }
        # Counters
        self.territory_inserts = 0
        self.territory_deletes = 0
        self.tenant_updates: list[dict[str, Any]] = []
        self.module_upserts: list[dict[str, Any]] = []

    def table(self, name: str) -> _Chain:
        return _Chain(self, name)

    def _dispatch(self, chain: _Chain) -> _Result:
        t = chain._table
        op = chain._op

        if t == "tenants":
            if op == "select":
                return _Result(data=[dict(self.tenant_row)])
            if op == "update":
                assert chain._payload is not None
                self.tenant_row.update(chain._payload)
                self.tenant_updates.append(dict(chain._payload))
                return _Result(data=[dict(self.tenant_row)])
            return _Result()

        if t == "territories":
            if op == "select":
                return _Result(data=list(self.territories))
            if op == "insert":
                self.territory_inserts += 1
                row = dict(chain._payload or {})
                row.setdefault("id", f"terr-new-{self.territory_inserts}")
                row.setdefault("created_at", "2024-01-01T00:00:00+00:00")
                row.setdefault("updated_at", "2024-01-01T00:00:00+00:00")
                self.territories.append(row)
                return _Result(data=[row])
            if op == "delete":
                before = len(self.territories)
                tid = chain._filters.get("id")
                self.territories = [r for r in self.territories if r["id"] != tid]
                self.territory_deletes += before - len(self.territories)
                return _Result(data=[])
            return _Result()

        if t == "tenant_modules":
            if op == "select":
                key = chain._filters.get("module_key")
                if key == "sorgente":
                    row = {
                        "tenant_id": chain._filters.get("tenant_id") or TENANT,
                        "module_key": "sorgente",
                        "config": dict(self.sorgente_config),
                        "active": True,
                        "version": 1,
                        "updated_at": "2024-01-01T00:00:00+00:00",
                    }
                    # maybe_single() returns the row as a plain dict (not list)
                    # — mirrors real Supabase PostgREST behaviour.
                    if chain._is_maybe_single:
                        return _Result(data=row)  # type: ignore[arg-type]
                    return _Result(data=[row])
                return _Result(data=[])
            if op == "upsert":
                self.module_upserts.append(dict(chain._payload or {}))
                row = dict(chain._payload or {})
                row.setdefault("version", 2)
                row.setdefault("active", True)
                row.setdefault("updated_at", "2024-02-01T00:00:00+00:00")
                return _Result(data=[row])
            return _Result()

        return _Result()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _override_auth(role: str = "member", *, tenant_id: str | None = TENANT) -> None:
    async def _auth() -> AuthContext:
        return AuthContext(
            user_id=USER,
            email=f"{USER}@example.com",
            tenant_id=tenant_id,
            role=role,
        )

    app.dependency_overrides[get_current_user] = _auth


def _clear_auth() -> None:
    app.dependency_overrides.clear()


def _patch_service_clients(
    monkeypatch: pytest.MonkeyPatch, fake: _LockFakeSupabase
) -> None:
    """Point every call site that reads/writes lock state at the fake.

    The service module and each route import ``get_service_client`` at
    import time, so we have to patch each bound name individually.
    """
    from src.services import territory_lock_service
    from src.routes import territories as territories_route
    from src.routes import admin as admin_route
    from src.routes import onboarding as onboarding_route
    from src.services import tenant_module_service

    monkeypatch.setattr(
        territory_lock_service, "get_service_client", lambda: fake
    )
    monkeypatch.setattr(
        territories_route, "get_service_client", lambda: fake
    )
    monkeypatch.setattr(admin_route, "get_service_client", lambda: fake)
    monkeypatch.setattr(
        tenant_module_service, "get_service_client", lambda: fake
    )
    # onboarding imports lock() from the service — already patched above.
    _ = onboarding_route  # silence unused


# ---------------------------------------------------------------------------
# 1. Service-level primitives
# ---------------------------------------------------------------------------


def test_is_locked_true_when_timestamp_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import territory_lock_service as svc

    fake = _LockFakeSupabase(locked=True)
    monkeypatch.setattr(svc, "get_service_client", lambda: fake)
    assert svc.is_locked(TENANT) is True


def test_is_locked_false_when_timestamp_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import territory_lock_service as svc

    fake = _LockFakeSupabase(locked=False)
    monkeypatch.setattr(svc, "get_service_client", lambda: fake)
    assert svc.is_locked(TENANT) is False


def test_require_unlocked_raises_423_when_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from src.services import territory_lock_service as svc

    fake = _LockFakeSupabase(locked=True)
    monkeypatch.setattr(svc, "get_service_client", lambda: fake)
    with pytest.raises(HTTPException) as ei:
        svc.require_unlocked(TENANT)
    assert ei.value.status_code == 423


def test_require_unlocked_noop_when_unlocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import territory_lock_service as svc

    fake = _LockFakeSupabase(locked=False)
    monkeypatch.setattr(svc, "get_service_client", lambda: fake)
    # Does not raise
    svc.require_unlocked(TENANT)


def test_reject_geo_change_blocks_changed_frozen_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from src.services import territory_lock_service as svc

    fake = _LockFakeSupabase(locked=True)
    monkeypatch.setattr(svc, "get_service_client", lambda: fake)

    current = {"regioni": ["Campania"], "province": [], "cap": []}
    proposed = {"regioni": ["Campania", "Lazio"], "province": [], "cap": []}
    with pytest.raises(HTTPException) as ei:
        svc.reject_geo_change(TENANT, current=current, proposed=proposed)
    assert ei.value.status_code == 423
    assert "regioni" in str(ei.value.detail)


def test_reject_geo_change_allows_non_geo_edits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing ATECO / employees / revenue while the lock is active
    must NOT raise — only the three frozen fields are gated."""
    from src.services import territory_lock_service as svc

    fake = _LockFakeSupabase(locked=True)
    monkeypatch.setattr(svc, "get_service_client", lambda: fake)

    current = {
        "regioni": ["Campania"],
        "province": [],
        "cap": [],
        "ateco_codes": ["10.51"],
        "min_employees": 20,
    }
    proposed = dict(current, ateco_codes=["10.51", "25.11"], min_employees=50)
    # Does not raise — the 3 frozen fields are equal.
    svc.reject_geo_change(TENANT, current=current, proposed=proposed)


def test_reject_geo_change_noop_when_unlocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import territory_lock_service as svc

    fake = _LockFakeSupabase(locked=False)
    monkeypatch.setattr(svc, "get_service_client", lambda: fake)
    # Even a genuine geo change is fine pre-lock.
    svc.reject_geo_change(
        TENANT,
        current={"regioni": ["Campania"]},
        proposed={"regioni": ["Lazio"]},
    )


def test_reject_geo_change_ignores_order_differences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lists are compared set-style via sorted(); reordering shouldn't
    trigger a false-positive lock violation."""
    from src.services import territory_lock_service as svc

    fake = _LockFakeSupabase(locked=True)
    monkeypatch.setattr(svc, "get_service_client", lambda: fake)
    svc.reject_geo_change(
        TENANT,
        current={"regioni": ["Campania", "Lazio"], "province": [], "cap": []},
        proposed={"regioni": ["Lazio", "Campania"], "province": [], "cap": []},
    )


def test_lock_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second ``lock()`` on an already-locked tenant must preserve
    the original timestamp (no overwrite)."""
    from src.services import territory_lock_service as svc

    fake = _LockFakeSupabase(locked=True)
    original_ts = fake.tenant_row["territory_locked_at"]
    monkeypatch.setattr(svc, "get_service_client", lambda: fake)

    row = svc.lock(TENANT, user_id=USER)
    assert row["territory_locked_at"] == original_ts
    # No UPDATE actually issued.
    assert fake.tenant_updates == []


def test_lock_sets_timestamp_when_unlocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import territory_lock_service as svc

    fake = _LockFakeSupabase(locked=False)
    monkeypatch.setattr(svc, "get_service_client", lambda: fake)

    svc.lock(TENANT, user_id=USER)
    assert len(fake.tenant_updates) == 1
    upd = fake.tenant_updates[0]
    assert upd["territory_locked_at"]  # non-empty ISO string
    assert upd["territory_locked_by"] == USER
    # Fake's state now reflects locked.
    assert fake.tenant_row["territory_locked_at"]


def test_unlock_clears_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.services import territory_lock_service as svc

    fake = _LockFakeSupabase(locked=True)
    monkeypatch.setattr(svc, "get_service_client", lambda: fake)

    svc.unlock(TENANT)
    assert fake.tenant_updates[-1] == {
        "territory_locked_at": None,
        "territory_locked_by": None,
    }


# ---------------------------------------------------------------------------
# 2. Territory routes — POST / DELETE gated on lock
# ---------------------------------------------------------------------------


def test_post_territory_returns_423_when_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _LockFakeSupabase(locked=True)
    _patch_service_clients(monkeypatch, fake)

    _override_auth("member")
    try:
        with TestClient(app) as c:
            r = c.post(
                "/v1/territories",
                headers={"Authorization": "Bearer dummy"},
                json={
                    "type": "regione",
                    "code": "12",
                    "name": "Lazio",
                    "bbox": None,
                    "priority": 1,
                    "excluded": False,
                },
            )
        assert r.status_code == 423, r.text
        # The insert must not have reached the DB.
        assert fake.territory_inserts == 0
    finally:
        _clear_auth()


def test_post_territory_succeeds_when_unlocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _LockFakeSupabase(locked=False)
    _patch_service_clients(monkeypatch, fake)

    _override_auth("member")
    try:
        with TestClient(app) as c:
            r = c.post(
                "/v1/territories",
                headers={"Authorization": "Bearer dummy"},
                json={
                    "type": "regione",
                    "code": "12",
                    "name": "Lazio",
                    "bbox": None,
                    "priority": 1,
                    "excluded": False,
                },
            )
        assert r.status_code == 201, r.text
        assert fake.territory_inserts == 1
    finally:
        _clear_auth()


def test_delete_territory_returns_423_when_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _LockFakeSupabase(locked=True)
    _patch_service_clients(monkeypatch, fake)

    _override_auth("member")
    try:
        with TestClient(app) as c:
            r = c.delete(
                f"/v1/territories/{TERRITORY_ID}",
                headers={"Authorization": "Bearer dummy"},
            )
        assert r.status_code == 423, r.text
        assert fake.territory_deletes == 0, (
            "Lock was bypassed — delete reached the DB"
        )
    finally:
        _clear_auth()


def test_delete_territory_succeeds_when_unlocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _LockFakeSupabase(locked=False)
    _patch_service_clients(monkeypatch, fake)

    _override_auth("member")
    try:
        with TestClient(app) as c:
            r = c.delete(
                f"/v1/territories/{TERRITORY_ID}",
                headers={"Authorization": "Bearer dummy"},
            )
        assert r.status_code == 200, r.text
        assert fake.territory_deletes == 1
    finally:
        _clear_auth()


# ---------------------------------------------------------------------------
# 3. Modules sorgente — geo change gated
# ---------------------------------------------------------------------------


def test_put_sorgente_geo_change_returns_423_when_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _LockFakeSupabase(
        locked=True,
        sorgente_config={
            "mode": "b2b_funnel_v2",
            "ateco_codes": ["10.51"],
            "regioni": ["Campania"],
            "province": [],
            "cap": [],
        },
    )
    _patch_service_clients(monkeypatch, fake)

    _override_auth("member")
    try:
        with TestClient(app) as c:
            r = c.put(
                "/v1/modules/sorgente",
                headers={"Authorization": "Bearer dummy"},
                json={
                    "config": {
                        "mode": "b2b_funnel_v2",
                        "ateco_codes": ["10.51"],
                        # Added a region — should be blocked.
                        "regioni": ["Campania", "Lazio"],
                        "province": [],
                        "cap": [],
                    }
                },
            )
        assert r.status_code == 423, r.text
        # upsert must NOT have been called.
        assert fake.module_upserts == [], (
            "Lock bypassed — sorgente upsert reached the DB"
        )
    finally:
        _clear_auth()


def test_put_sorgente_non_geo_change_succeeds_when_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ATECO / employees / revenue remain editable post-lock —
    only regioni/province/cap are frozen by the contract."""
    fake = _LockFakeSupabase(
        locked=True,
        sorgente_config={
            "mode": "b2b_funnel_v2",
            "ateco_codes": ["10.51"],
            "regioni": ["Campania"],
            "province": [],
            "cap": [],
        },
    )
    _patch_service_clients(monkeypatch, fake)

    _override_auth("member")
    try:
        with TestClient(app) as c:
            r = c.put(
                "/v1/modules/sorgente",
                headers={"Authorization": "Bearer dummy"},
                json={
                    "config": {
                        "mode": "b2b_funnel_v2",
                        # Added a second ATECO code — geo is untouched.
                        "ateco_codes": ["10.51", "25.11"],
                        "regioni": ["Campania"],
                        "province": [],
                        "cap": [],
                    }
                },
            )
        assert r.status_code == 200, r.text
        assert len(fake.module_upserts) == 1
    finally:
        _clear_auth()


# ---------------------------------------------------------------------------
# 4. Admin territory-unlock — super_admin gate
# ---------------------------------------------------------------------------


def test_admin_unlock_requires_super_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _LockFakeSupabase(locked=True)
    _patch_service_clients(monkeypatch, fake)

    _override_auth("member")  # <-- NOT super_admin
    try:
        with TestClient(app) as c:
            r = c.post(
                f"/v1/admin/tenants/{TENANT}/territory-unlock",
                headers={"Authorization": "Bearer dummy"},
            )
        assert r.status_code == 403, r.text
        # Lock state must be unchanged.
        assert fake.tenant_row["territory_locked_at"] is not None
    finally:
        _clear_auth()


def test_admin_unlock_clears_lock_for_super_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _LockFakeSupabase(locked=True)
    _patch_service_clients(monkeypatch, fake)

    _override_auth("super_admin")
    try:
        with TestClient(app) as c:
            r = c.post(
                f"/v1/admin/tenants/{TENANT}/territory-unlock",
                headers={"Authorization": "Bearer dummy"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tenant_id"] == TENANT
        assert body["territory_locked_at"] is None
        assert fake.tenant_row["territory_locked_at"] is None
    finally:
        _clear_auth()


# ---------------------------------------------------------------------------
# 5. Onboarding territory-confirm
# ---------------------------------------------------------------------------


def test_onboarding_territory_confirm_locks_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _LockFakeSupabase(locked=False)
    _patch_service_clients(monkeypatch, fake)

    _override_auth("member")
    try:
        with TestClient(app) as c:
            r = c.post(
                "/v1/onboarding/territory-confirm",
                headers={"Authorization": "Bearer dummy"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tenant_id"] == TENANT
        assert body["territory_locked_at"]
        assert body["territory_locked_by"] == USER
        # One UPDATE flushed the new timestamp.
        assert len(fake.tenant_updates) == 1
    finally:
        _clear_auth()


def test_onboarding_territory_confirm_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling confirm twice must not overwrite the original lock
    timestamp — contract audit trail depends on the first-lock time."""
    fake = _LockFakeSupabase(locked=True)
    original_ts = fake.tenant_row["territory_locked_at"]
    _patch_service_clients(monkeypatch, fake)

    _override_auth("member")
    try:
        with TestClient(app) as c:
            r = c.post(
                "/v1/onboarding/territory-confirm",
                headers={"Authorization": "Bearer dummy"},
            )
        assert r.status_code == 200, r.text
        assert fake.tenant_row["territory_locked_at"] == original_ts
        # No UPDATE — the existing lock was preserved.
        assert fake.tenant_updates == []
    finally:
        _clear_auth()
