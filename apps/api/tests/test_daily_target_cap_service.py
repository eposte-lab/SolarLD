"""Sprint 2 — daily target cap service unit tests.

These tests don't talk to Redis. We patch ``get_redis`` with a tiny
in-memory async stub that supports the four ops the service uses
(``incr``, ``decr``, ``expire``, ``get``). That keeps the tests
hermetic and fast (~ms) while still exercising the real INCR/DECR
rollback semantics + the cap-boundary races the service handles.

Integration with a real Redis is covered separately in the staging
E2E (SC-31 in the plan).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.services import daily_target_cap_service as svc


# ---------------------------------------------------------------------------
# Pure helpers — no Redis needed
# ---------------------------------------------------------------------------


class TestCapForTenant:
    def test_uses_explicit_int(self) -> None:
        assert svc.cap_for_tenant({"daily_target_send_cap": 500}) == 500

    def test_falls_back_when_missing(self) -> None:
        assert svc.cap_for_tenant({}) == svc.DEFAULT_DAILY_CAP

    def test_falls_back_on_none(self) -> None:
        assert (
            svc.cap_for_tenant({"daily_target_send_cap": None})
            == svc.DEFAULT_DAILY_CAP
        )

    def test_falls_back_on_zero(self) -> None:
        # 0 would silently "disable" the cap → guard against it.
        assert (
            svc.cap_for_tenant({"daily_target_send_cap": 0})
            == svc.DEFAULT_DAILY_CAP
        )

    def test_falls_back_on_negative(self) -> None:
        assert (
            svc.cap_for_tenant({"daily_target_send_cap": -1})
            == svc.DEFAULT_DAILY_CAP
        )

    def test_coerces_float(self) -> None:
        # Defensive: a JSONB read could surface a float.
        assert svc.cap_for_tenant({"daily_target_send_cap": 250.0}) == 250


class TestRedisKey:
    def test_uses_rome_date(self) -> None:
        # 2026-04-25 23:30 UTC → 2026-04-26 01:30 Rome (CEST, +2)
        # so the key date should already be the next Rome day.
        late = datetime(2026, 4, 25, 23, 30, tzinfo=timezone.utc)
        key = svc.redis_key_for("tenant-abc", now_utc=late)
        assert key == "daily_target_cap:tenant-abc:2026-04-26"

    def test_morning_utc_matches_rome_today(self) -> None:
        morning = datetime(2026, 4, 25, 8, 0, tzinfo=timezone.utc)
        key = svc.redis_key_for("tenant-abc", now_utc=morning)
        assert key == "daily_target_cap:tenant-abc:2026-04-25"


# ---------------------------------------------------------------------------
# In-memory fake redis (just enough for check_and_reserve / peek_usage)
# ---------------------------------------------------------------------------


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        # Lets a test simulate "Redis is down" cleanly.
        self.fail = False

    async def incr(self, key: str) -> int:
        if self.fail:
            raise RuntimeError("redis down")
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def decr(self, key: str) -> int:
        if self.fail:
            raise RuntimeError("redis down")
        self.store[key] = self.store.get(key, 0) - 1
        return self.store[key]

    async def expire(self, key: str, ttl: int) -> None:
        if self.fail:
            raise RuntimeError("redis down")
        self.ttls[key] = ttl

    async def get(self, key: str) -> str | None:
        if self.fail:
            raise RuntimeError("redis down")
        v = self.store.get(key)
        return str(v) if v is not None else None


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(svc, "get_redis", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# check_and_reserve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_send_allowed_and_sets_ttl(fake_redis: FakeRedis) -> None:
    decision = await svc.check_and_reserve(
        {"id": "tenant-1", "daily_target_send_cap": 250}
    )
    assert decision.allowed
    assert decision.verdict == "allowed"
    assert decision.used == 1
    assert decision.limit == 250
    assert decision.remaining == 249
    # TTL was set on the first INCR.
    assert any(v == svc.COUNTER_TTL_S for v in fake_redis.ttls.values())


@pytest.mark.asyncio
async def test_under_cap_increments(fake_redis: FakeRedis) -> None:
    tenant = {"id": "tenant-1", "daily_target_send_cap": 3}
    for expected in (1, 2, 3):
        decision = await svc.check_and_reserve(tenant)
        assert decision.allowed
        assert decision.used == expected


@pytest.mark.asyncio
async def test_at_cap_blocks_and_rolls_back(fake_redis: FakeRedis) -> None:
    tenant = {"id": "tenant-1", "daily_target_send_cap": 2}
    # Burn the budget.
    await svc.check_and_reserve(tenant)
    await svc.check_and_reserve(tenant)
    # The 3rd call must be rejected — and the counter must roll back
    # to the cap, not climb to 3.
    decision = await svc.check_and_reserve(tenant)
    assert not decision.allowed
    assert decision.verdict == "cap_reached"
    assert decision.used == 2  # report cap-as-used for "250/250" UI
    assert decision.limit == 2
    # Internal counter rolled back to 2 (not 3).
    key = svc.redis_key_for("tenant-1")
    assert fake_redis.store[key] == 2


@pytest.mark.asyncio
async def test_blocked_subsequent_calls_stay_blocked(
    fake_redis: FakeRedis,
) -> None:
    tenant = {"id": "tenant-1", "daily_target_send_cap": 1}
    first = await svc.check_and_reserve(tenant)
    assert first.allowed
    second = await svc.check_and_reserve(tenant)
    third = await svc.check_and_reserve(tenant)
    assert not second.allowed
    assert not third.allowed
    # Counter never diverges upward — repeated blocked calls don't
    # leak +1 each time thanks to the DECR rollback.
    key = svc.redis_key_for("tenant-1")
    assert fake_redis.store[key] == 1


@pytest.mark.asyncio
async def test_missing_tenant_id_fails_open(fake_redis: FakeRedis) -> None:
    decision = await svc.check_and_reserve({})
    assert decision.allowed
    assert decision.used == 0
    # Nothing was written to Redis.
    assert fake_redis.store == {}


@pytest.mark.asyncio
async def test_redis_down_fails_open(fake_redis: FakeRedis) -> None:
    fake_redis.fail = True
    decision = await svc.check_and_reserve(
        {"id": "tenant-1", "daily_target_send_cap": 250}
    )
    # Fail-open: we don't take the tenant's pipeline down because
    # Redis blipped — the inbox-level caps still bound blast radius.
    assert decision.allowed
    assert decision.used == 0


@pytest.mark.asyncio
async def test_uses_default_cap_when_column_missing(
    fake_redis: FakeRedis,
) -> None:
    decision = await svc.check_and_reserve({"id": "tenant-1"})
    assert decision.allowed
    assert decision.limit == svc.DEFAULT_DAILY_CAP


# ---------------------------------------------------------------------------
# peek_usage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_peek_returns_zero_when_no_sends(fake_redis: FakeRedis) -> None:
    decision = await svc.peek_usage(
        {"id": "tenant-1", "daily_target_send_cap": 250}
    )
    assert decision.used == 0
    assert decision.verdict == "allowed"
    assert decision.remaining == 250


@pytest.mark.asyncio
async def test_peek_reflects_current_count(fake_redis: FakeRedis) -> None:
    tenant = {"id": "tenant-1", "daily_target_send_cap": 250}
    await svc.check_and_reserve(tenant)
    await svc.check_and_reserve(tenant)
    decision = await svc.peek_usage(tenant)
    assert decision.used == 2
    assert decision.remaining == 248
    assert decision.verdict == "allowed"


@pytest.mark.asyncio
async def test_peek_says_cap_reached_at_limit(fake_redis: FakeRedis) -> None:
    tenant = {"id": "tenant-1", "daily_target_send_cap": 2}
    await svc.check_and_reserve(tenant)
    await svc.check_and_reserve(tenant)
    decision = await svc.peek_usage(tenant)
    assert decision.used == 2
    assert decision.verdict == "cap_reached"
    assert decision.remaining == 0


@pytest.mark.asyncio
async def test_peek_redis_down_fails_open(fake_redis: FakeRedis) -> None:
    fake_redis.fail = True
    decision = await svc.peek_usage(
        {"id": "tenant-1", "daily_target_send_cap": 250}
    )
    # Same fail-open philosophy — UI shows 0/250 rather than crashing.
    assert decision.used == 0
    assert decision.verdict == "allowed"
