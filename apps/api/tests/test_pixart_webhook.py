"""Route-level tests for the Pixart postal tracking webhook.

These tests exercise the HMAC auth gate and the enqueue path without
touching the Supabase backend — ``enqueue`` is monkey-patched to a
recording stub.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.routes import webhooks as webhooks_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def recorded_enqueue(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict]]:
    """Replace ``enqueue`` inside the webhooks module with a recorder."""
    calls: list[dict] = []

    async def _fake_enqueue(function: str, payload: dict, **kwargs: Any) -> None:
        calls.append({"function": function, "payload": payload, "kwargs": kwargs})

    monkeypatch.setattr(webhooks_module, "enqueue", _fake_enqueue)
    yield calls


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_pixart_webhook_no_secret_configured_returns_ignored(
    monkeypatch: pytest.MonkeyPatch,
    recorded_enqueue: list[dict],
) -> None:
    """With no ``PIXART_WEBHOOK_SECRET`` set (dev mode) the webhook
    accepts but does not enqueue — mirrors the Resend dev-mode policy."""
    monkeypatch.setattr(webhooks_module.settings, "pixart_webhook_secret", "")

    with TestClient(app) as client:
        resp = client.post(
            "/v1/webhooks/pixart",
            json={"tracking_number": "PX-123", "event_type": "delivered"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"ok": "ignored", "reason": "no_secret_configured"}
    assert recorded_enqueue == []


def test_pixart_webhook_missing_signature_header_400(
    monkeypatch: pytest.MonkeyPatch,
    recorded_enqueue: list[dict],
) -> None:
    monkeypatch.setattr(webhooks_module.settings, "pixart_webhook_secret", "topsecret")

    with TestClient(app) as client:
        resp = client.post(
            "/v1/webhooks/pixart",
            json={"tracking_number": "PX-1", "event_type": "delivered"},
        )
    assert resp.status_code == 400
    assert recorded_enqueue == []


def test_pixart_webhook_invalid_signature_401(
    monkeypatch: pytest.MonkeyPatch,
    recorded_enqueue: list[dict],
) -> None:
    monkeypatch.setattr(webhooks_module.settings, "pixart_webhook_secret", "topsecret")

    body = {"tracking_number": "PX-1", "event_type": "delivered"}
    with TestClient(app) as client:
        resp = client.post(
            "/v1/webhooks/pixart",
            json=body,
            headers={"X-Pixart-Signature": "deadbeef" * 8},
        )
    assert resp.status_code == 401
    assert recorded_enqueue == []


def test_pixart_webhook_valid_signature_enqueues_tracking_task(
    monkeypatch: pytest.MonkeyPatch,
    recorded_enqueue: list[dict],
) -> None:
    secret = "topsecret"
    monkeypatch.setattr(webhooks_module.settings, "pixart_webhook_secret", secret)

    body = {"tracking_number": "PX-abc", "event_type": "delivered"}
    raw = json.dumps(body).encode("utf-8")
    sig = _sign(raw, secret)

    with TestClient(app) as client:
        resp = client.post(
            "/v1/webhooks/pixart",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Pixart-Signature": sig,
            },
        )

    assert resp.status_code == 200
    assert resp.json() == {"ok": "queued"}

    assert len(recorded_enqueue) == 1
    call = recorded_enqueue[0]
    assert call["function"] == "tracking_task"
    assert call["payload"]["provider"] == "pixart"
    assert call["payload"]["event_type"] == "delivered"
    assert call["payload"]["raw_payload"]["tracking_number"] == "PX-abc"
    # Dedupe job_id tied to tracking id + event type
    assert call["kwargs"]["job_id"] == "tracking:pixart:PX-abc:delivered"


def test_pixart_webhook_accepts_sha256_prefixed_signature(
    monkeypatch: pytest.MonkeyPatch,
    recorded_enqueue: list[dict],
) -> None:
    """Some Pixart deployments send ``sha256=<hex>`` instead of raw hex."""
    secret = "topsecret"
    monkeypatch.setattr(webhooks_module.settings, "pixart_webhook_secret", secret)

    body = {"tracking_number": "PX-1", "event_type": "shipped"}
    raw = json.dumps(body).encode("utf-8")
    sig = f"sha256={_sign(raw, secret)}"

    with TestClient(app) as client:
        resp = client.post(
            "/v1/webhooks/pixart",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Pixart-Signature": sig,
            },
        )

    assert resp.status_code == 200
    assert len(recorded_enqueue) == 1


def test_pixart_webhook_missing_tracking_id_ignored(
    monkeypatch: pytest.MonkeyPatch,
    recorded_enqueue: list[dict],
) -> None:
    secret = "topsecret"
    monkeypatch.setattr(webhooks_module.settings, "pixart_webhook_secret", secret)

    body = {"event_type": "delivered"}  # no tracking_number
    raw = json.dumps(body).encode("utf-8")
    sig = _sign(raw, secret)

    with TestClient(app) as client:
        resp = client.post(
            "/v1/webhooks/pixart",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Pixart-Signature": sig,
            },
        )

    assert resp.status_code == 200
    assert resp.json()["reason"] == "no_tracking_id"
    assert recorded_enqueue == []


def test_pixart_webhook_invalid_json_400(
    monkeypatch: pytest.MonkeyPatch,
    recorded_enqueue: list[dict],
) -> None:
    secret = "topsecret"
    monkeypatch.setattr(webhooks_module.settings, "pixart_webhook_secret", secret)

    raw = b"not json at all"
    sig = _sign(raw, secret)

    with TestClient(app) as client:
        resp = client.post(
            "/v1/webhooks/pixart",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Pixart-Signature": sig,
            },
        )
    assert resp.status_code == 400
    assert recorded_enqueue == []
