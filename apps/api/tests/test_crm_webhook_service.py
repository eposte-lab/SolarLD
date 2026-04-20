"""Unit tests for the CRM webhook dispatcher — pure helpers only.

The I/O-heavy ``dispatch_event`` function is covered by integration
tests separately; here we pin down the canonical serialisation and
signature contract so receivers stay compatible across refactors.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from src.services.crm_webhook_service import (
    SUPPORTED_EVENTS,
    build_canonical_payload,
    serialize_payload,
    sign,
)


def test_supported_events_are_frozen_and_include_contract_signed() -> None:
    assert "lead.contract_signed" in SUPPORTED_EVENTS
    assert "lead.created" in SUPPORTED_EVENTS
    assert "lead.scored" in SUPPORTED_EVENTS
    # Sanity: the set is frozen so downstream code can rely on
    # membership at module-import time.
    assert isinstance(SUPPORTED_EVENTS, frozenset)


def test_build_canonical_payload_assigns_fields() -> None:
    env = build_canonical_payload(
        event_type="lead.scored",
        tenant_id="t1",
        occurred_at="2026-04-18T12:00:00+00:00",
        data={"lead_id": "L1", "score": 87},
    )
    assert env["event"] == "lead.scored"
    assert env["tenant_id"] == "t1"
    assert env["occurred_at"] == "2026-04-18T12:00:00+00:00"
    assert env["data"] == {"lead_id": "L1", "score": 87}
    # id is a UUID string when no delivery_id given.
    assert isinstance(env["id"], str) and len(env["id"]) >= 8


def test_signature_matches_manual_hmac() -> None:
    body = b'{"event":"lead.scored"}'
    secret = "s3kret"
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    assert sign(body, secret) == expected


def test_serialize_payload_is_deterministic_and_signed() -> None:
    env = build_canonical_payload(
        event_type="lead.contract_signed",
        tenant_id="t1",
        occurred_at="2026-04-18T12:00:00+00:00",
        data={"lead_id": "L1", "contract_value_cents": 1500000},
        delivery_id="d1",
    )
    a = serialize_payload(env, "secret")
    b = serialize_payload(env, "secret")
    # Same input → byte-identical body + signature (sorted keys,
    # no whitespace).
    assert a.body == b.body
    assert a.signature == b.signature
    assert a.delivery_id == "d1"
    # Body should be valid JSON and round-trip to the same dict.
    assert json.loads(a.body.decode("utf-8")) == env


def test_different_secrets_produce_different_signatures() -> None:
    env = build_canonical_payload(
        event_type="lead.created",
        tenant_id="t1",
        occurred_at="2026-04-18T12:00:00+00:00",
        data={"lead_id": "L1"},
        delivery_id="d1",
    )
    a = serialize_payload(env, "secret-1")
    b = serialize_payload(env, "secret-2")
    assert a.body == b.body
    assert a.signature != b.signature
