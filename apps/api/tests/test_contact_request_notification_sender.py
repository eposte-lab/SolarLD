"""The operator contact-request notification goes out from the platform's
transactional domain, NOT the tenant's outreach subdomain.

Regression for the 2026-06-18 bounce: notifying info@totaltrade.it FROM
commerciale@commerciale.totaltrade.it hit Aruba's same-root-domain anti-spoof
("501 invalid sender domain"). It now sends from settings.notification_from_email
(a Resend-verified platform domain) with the tenant name as the display, and
reply-to = the prospect so the operator answers the lead in one click.
"""

from __future__ import annotations

from typing import Any

from src.services import appointment_service as svc
from src.services.appointment_service import notify_tenant_contact_request


async def _run(monkeypatch: Any, tenant_data: dict, payload: dict) -> Any:
    captured: dict[str, Any] = {}

    async def _fake_send(inp: Any) -> dict:
        captured["inp"] = inp
        return {"id": "x"}

    monkeypatch.setattr(svc, "send_email", _fake_send)
    monkeypatch.setattr(svc.settings, "notification_from_email", "notifiche@agenda-pro.it")

    await notify_tenant_contact_request(
        object(),  # sb — only touched by the disused inbox fallback
        tenant_id="t",
        tenant_data=tenant_data,
        payload=payload,
        dossier_url="https://x/dossier/abc",
    )
    return captured.get("inp")


async def test_sends_from_platform_domain_not_outreach_subdomain(monkeypatch: Any) -> None:
    inp = await _run(
        monkeypatch,
        {"contact_email": "info@totaltrade.it", "business_name": "Total Trade"},
        {"contact_name": "Antonello", "phone": "089210400", "email": "prospect@example.com"},
    )
    assert inp is not None
    assert inp.to == ["info@totaltrade.it"]
    # FROM must be the platform's verified sender, never a totaltrade.it address.
    addr_part = inp.from_address.split("<")[-1].rstrip(">")
    assert addr_part == "notifiche@agenda-pro.it"
    assert "totaltrade.it" not in addr_part
    # Tenant name kept as the display name; prospect is the reply-to.
    assert "Total Trade" in inp.from_address
    assert inp.reply_to == "prospect@example.com"


async def test_no_business_name_falls_back_to_bare_sender(monkeypatch: Any) -> None:
    inp = await _run(
        monkeypatch,
        {"contact_email": "info@totaltrade.it"},
        {"contact_name": "X", "phone": "1"},
    )
    assert inp.from_address == "notifiche@agenda-pro.it"
    assert inp.reply_to is None  # no prospect email in the form
