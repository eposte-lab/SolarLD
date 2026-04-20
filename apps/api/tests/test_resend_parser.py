"""Pure-function tests for ``services.resend_service``.

Only the pure helpers are exercised — actual HTTP calls are not made.
That keeps the suite runnable without network or secrets.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from src.services.resend_service import (
    EmailEvent,
    ResendError,
    SendEmailInput,
    build_send_payload,
    parse_send_response,
    parse_webhook_event,
    verify_webhook_signature,
)


# ---------------------------------------------------------------------------
# build_send_payload
# ---------------------------------------------------------------------------


def test_build_send_payload_minimal_keys() -> None:
    body = build_send_payload(
        SendEmailInput(
            from_address="SolarLead <outreach@solarlead.it>",
            to=["ceo@example.com"],
            subject="Ciao",
            html="<p>hi</p>",
        )
    )
    assert body["from"] == "SolarLead <outreach@solarlead.it>"
    assert body["to"] == ["ceo@example.com"]
    assert body["subject"] == "Ciao"
    assert body["html"] == "<p>hi</p>"
    # Missing optionals must not leak as None keys.
    assert "text" not in body
    assert "reply_to" not in body
    assert "tags" not in body
    assert "headers" not in body


def test_build_send_payload_tags_become_name_value_pairs() -> None:
    body = build_send_payload(
        SendEmailInput(
            from_address="x@y.it",
            to=["a@b.com"],
            subject="s",
            html="<p/>",
            tags={"tenant_id": "t1", "lead_id": "l9"},
        )
    )
    assert body["tags"] == [
        {"name": "tenant_id", "value": "t1"},
        {"name": "lead_id", "value": "l9"},
    ]


def test_build_send_payload_includes_text_and_headers() -> None:
    body = build_send_payload(
        SendEmailInput(
            from_address="x@y.it",
            to=["a@b.com"],
            subject="s",
            html="<p/>",
            text="plain",
            reply_to="support@y.it",
            headers={"X-Campaign": "spring"},
        )
    )
    assert body["text"] == "plain"
    assert body["reply_to"] == "support@y.it"
    assert body["headers"] == {"X-Campaign": "spring"}


# ---------------------------------------------------------------------------
# parse_send_response
# ---------------------------------------------------------------------------


def test_parse_send_response_extracts_id() -> None:
    r = parse_send_response({"id": "msg_abc", "from": "x@y.it", "to": ["a@b.com"]})
    assert r.id == "msg_abc"


def test_parse_send_response_rejects_missing_id() -> None:
    with pytest.raises(ResendError):
        parse_send_response({"to": ["a@b.com"]})


def test_parse_send_response_rejects_empty_id() -> None:
    with pytest.raises(ResendError):
        parse_send_response({"id": ""})


# ---------------------------------------------------------------------------
# parse_webhook_event
# ---------------------------------------------------------------------------


def test_parse_webhook_event_aliases_type() -> None:
    raw = {
        "id": "msg_svix_1",
        "type": "email.delivered",
        "created_at": "2026-04-16T10:00:00Z",
        "data": {"email_id": "em_1", "to": ["x@y.it"]},
    }
    ev = parse_webhook_event(raw)
    assert ev.type == "delivered"
    assert ev.email_id == "em_1"
    assert ev.to == ["x@y.it"]
    assert ev.occurred_at == "2026-04-16T10:00:00Z"


def test_parse_webhook_event_handles_data_id_alias() -> None:
    raw = {
        "type": "email.opened",
        "data": {"id": "em_x", "to": "single@r.com"},
    }
    ev = parse_webhook_event(raw)
    assert ev.email_id == "em_x"
    # Single-string "to" field is coerced to a list.
    assert ev.to == ["single@r.com"]


def test_parse_webhook_event_rejects_missing_email_id() -> None:
    with pytest.raises(ResendError):
        parse_webhook_event({"type": "email.delivered", "data": {}})


def test_parse_webhook_event_unknown_type_falls_through() -> None:
    ev = parse_webhook_event({"type": "email.exotic", "data": {"email_id": "e"}})
    assert ev.type == "exotic"


def test_parse_webhook_event_to_list_of_non_strings_ignored() -> None:
    ev = parse_webhook_event(
        {"type": "email.delivered", "data": {"email_id": "e", "to": [123, None]}}
    )
    assert ev.to == []


# ---------------------------------------------------------------------------
# verify_webhook_signature
# ---------------------------------------------------------------------------


def _sign(body: bytes, svix_id: str, ts: str, secret_b64: str) -> str:
    key = base64.b64decode(secret_b64)
    payload = f"{svix_id}.{ts}.".encode("utf-8") + body
    digest = hmac.new(key, payload, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode("ascii")


def test_verify_webhook_signature_valid() -> None:
    secret_b64 = base64.b64encode(b"superlongrandombytes" * 2).decode("ascii")
    secret = f"whsec_{secret_b64}"
    body = b'{"type":"email.delivered"}'
    ts = "1_700_000_000".replace("_", "")
    sig = _sign(body, "svix_1", ts, secret_b64)
    ok = verify_webhook_signature(
        body=body,
        svix_id="svix_1",
        svix_timestamp=ts,
        svix_signature=sig,
        secret=secret,
        now_ts=int(ts),
    )
    assert ok is True


def test_verify_webhook_signature_rejects_tampered_body() -> None:
    secret_b64 = base64.b64encode(b"x" * 32).decode("ascii")
    body = b'{"a":1}'
    ts = "1700000000"
    sig = _sign(body, "svix_1", ts, secret_b64)
    ok = verify_webhook_signature(
        body=b'{"a":2}',  # tampered
        svix_id="svix_1",
        svix_timestamp=ts,
        svix_signature=sig,
        secret=f"whsec_{secret_b64}",
        now_ts=int(ts),
    )
    assert ok is False


def test_verify_webhook_signature_rejects_stale_timestamp() -> None:
    secret_b64 = base64.b64encode(b"x" * 32).decode("ascii")
    body = b"x"
    ts = "1700000000"
    sig = _sign(body, "svix_1", ts, secret_b64)
    ok = verify_webhook_signature(
        body=body,
        svix_id="svix_1",
        svix_timestamp=ts,
        svix_signature=sig,
        secret=f"whsec_{secret_b64}",
        now_ts=int(ts) + 3600,  # 1 hour in the future → stale
    )
    assert ok is False


def test_verify_webhook_signature_accepts_any_of_rotated_keys() -> None:
    secret_b64 = base64.b64encode(b"x" * 32).decode("ascii")
    body = b"payload"
    ts = "1700000000"
    good = _sign(body, "svix_1", ts, secret_b64)
    fake = "v1,bogus_base64_signature"
    header = f"{fake} {good}"
    ok = verify_webhook_signature(
        body=body,
        svix_id="svix_1",
        svix_timestamp=ts,
        svix_signature=header,
        secret=f"whsec_{secret_b64}",
        now_ts=int(ts),
    )
    assert ok is True


def test_verify_webhook_signature_missing_fields_returns_false() -> None:
    assert verify_webhook_signature(
        body=b"", svix_id="", svix_timestamp="1", svix_signature="v1,x", secret="s"
    ) is False
    assert verify_webhook_signature(
        body=b"", svix_id="a", svix_timestamp="", svix_signature="v1,x", secret="s"
    ) is False
    assert verify_webhook_signature(
        body=b"", svix_id="a", svix_timestamp="1", svix_signature="", secret="s"
    ) is False
    assert verify_webhook_signature(
        body=b"", svix_id="a", svix_timestamp="1", svix_signature="v1,x", secret=""
    ) is False


def test_verify_webhook_signature_bad_timestamp_format() -> None:
    ok = verify_webhook_signature(
        body=b"",
        svix_id="a",
        svix_timestamp="not-a-number",
        svix_signature="v1,x",
        secret="whsec_xxx",
    )
    assert ok is False


def test_verify_webhook_signature_ignores_non_v1_candidates() -> None:
    secret_b64 = base64.b64encode(b"x" * 32).decode("ascii")
    body = b"payload"
    ts = "1700000000"
    good = _sign(body, "svix_1", ts, secret_b64)
    # Candidate without a comma must not crash.
    header = f"no_comma_candidate {good}"
    ok = verify_webhook_signature(
        body=body,
        svix_id="svix_1",
        svix_timestamp=ts,
        svix_signature=header,
        secret=f"whsec_{secret_b64}",
        now_ts=int(ts),
    )
    assert ok is True
