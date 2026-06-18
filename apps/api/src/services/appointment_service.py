"""Appointment / inbound-contact-request side-effects.

Extracted from ``routes/public.py`` so both the public ingress
(``request_appointment``) and the super-admin approval endpoint
(``routes/admin.py``) can share one implementation of:

  * building the "new contact request" email,
  * delivering it to the tenant (``tenants.contact_email``),
  * firing the tenant's CRM webhook.

It also owns the **trial-moderation** helper. When a tenant is
moderated, the public route does NOT touch the tenant: it holds the
request in ``pending_inbound_requests``, which the operator reviews in
the super-admin "Coda inbound" queue (no email is sent). On approval,
``routes/admin.py`` replays the held side-effects through the very same
functions, so the tenant sees the exact email/webhook it would have
seen unmoderated.

Every function is **fail-open**: a failure here must never block the
prospect's 202 response nor a super-admin action.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from ..core.config import settings
from ..core.logging import get_logger
from .resend_service import SendEmailInput, send_email

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Moderation config
# ---------------------------------------------------------------------------


def is_tenant_moderated(sb: Any, tenant_id: str) -> bool:
    """Whether a tenant is under trial moderation.

    Reads ``tenants.settings.feature_flags.trial_moderation`` via the
    service client (RLS bypass). Fail-safe: any error → ``False`` so a
    config hiccup never silently swallows a real inbound request.
    """
    try:
        row = (
            sb.table("tenants")
            .select("settings")
            .eq("id", tenant_id)
            .limit(1)
            .maybe_single()
            .execute()
        )
        settings_obj = (row.data or {}).get("settings") if row else None
        flags = (settings_obj or {}).get("feature_flags") or {}
        return bool(
            flags.get("trial_moderation") is True or flags.get("trial_moderation") == "true"
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("appointment.moderation_config_failed", tenant_id=tenant_id, err=str(exc)[:200])
        return False


# ---------------------------------------------------------------------------
# Email building
# ---------------------------------------------------------------------------


def _active_inbox_from_address(sb: Any, tenant_id: str) -> str | None:
    """First active verified inbox email — the deliverable From address."""
    inbox = (
        sb.table("tenant_inboxes")
        .select("email")
        .eq("tenant_id", tenant_id)
        .eq("active", True)
        .order("created_at")
        .limit(1)
        .execute()
    )
    return (inbox.data or [{}])[0].get("email") if inbox.data else None


def build_contact_request_email(
    payload: dict[str, Any],
    dossier_url: str | None,
    business_name: str | None,
    proposal_resent_to: str | None = None,
) -> tuple[str, str, str]:
    """Build ``(subject, html, text)`` for the tenant contact-request email.

    ``proposal_resent_to`` — when the prospect left an email in the form, the
    system automatically re-sent the EXACT outreach proposal to that address.
    We surface a prominent callout so the operator knows the offer is already
    in the prospect's inbox (and to which address) before they follow up.
    """
    biz = business_name or "la vostra azienda"
    name = payload.get("contact_name") or "Un contatto"

    rows: list[str] = []
    if payload.get("contact_name"):
        rows.append(f"<b>Nome:</b> {payload['contact_name']}")
    if payload.get("phone"):
        rows.append(f'<b>Telefono:</b> <a href="tel:{payload["phone"]}">{payload["phone"]}</a>')
    if payload.get("email"):
        rows.append(f'<b>Email:</b> <a href="mailto:{payload["email"]}">{payload["email"]}</a>')
    if payload.get("preferred_time"):
        rows.append(f"<b>Preferenza orario:</b> {payload['preferred_time']}")
    if payload.get("notes"):
        rows.append(f"<b>Note:</b> {payload['notes']}")
    details = "<br>".join(rows) or "(nessun dettaglio fornito)"

    resent_callout = (
        f'<div style="margin:12px 0 0 0;background:#ecfdf5;border:1px solid #16a34a;'
        f'border-radius:8px;padding:12px 14px;color:#166534;font-size:13px;line-height:1.5;">'
        f"✅ <b>Proposta inviata automaticamente</b> anche a "
        f'<a href="mailto:{proposal_resent_to}" style="color:#166534;font-weight:700;">'
        f"{proposal_resent_to}</a> su richiesta del cliente "
        f"(stessa identica offerta del dossier).</div>"
        if proposal_resent_to
        else ""
    )

    link = (
        f'<p style="margin:16px 0 0 0;"><a href="{dossier_url}" '
        f'style="color:#16a34a;font-weight:700;">Apri il dossier del lead →</a></p>'
        if dossier_url
        else ""
    )

    subject = f"Nuova richiesta di contatto dal dossier — {name}"
    intro = f"Un prospect ha lasciato i suoi dati per essere ricontattato da {biz}."
    footer = "Rispondi a questa email per scrivere direttamente al cliente."

    html = (
        f'<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.6;color:#1f2937;">'
        f'<p style="margin:0 0 12px 0;font-size:16px;font-weight:700;color:#0f1f3d;">'
        f"Nuova richiesta di contatto dal dossier</p>"
        f'<p style="margin:0 0 12px 0;">{intro}</p>'
        f'<div style="background:#f5f7fa;border-radius:8px;padding:14px 16px;">{details}</div>'
        f"{resent_callout}"
        f"{link}"
        f'<p style="margin:16px 0 0 0;font-size:12px;color:#8a9099;">{footer}</p></div>'
    )
    text = (
        "Nuova richiesta di contatto dal dossier.\n\n"
        + "\n".join(r.replace("<b>", "").replace("</b>", "") for r in rows)
        + (
            f"\n\n✅ Proposta inviata automaticamente anche a {proposal_resent_to} "
            "su richiesta del cliente (stessa offerta del dossier)."
            if proposal_resent_to
            else ""
        )
        + (f"\n\nDossier: {dossier_url}" if dossier_url else "")
    )
    return subject, html, text


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


async def notify_tenant_contact_request(
    sb: Any,
    *,
    tenant_id: str,
    tenant_data: dict[str, Any],
    payload: dict[str, Any],
    dossier_url: str | None,
    proposal_resent_to: str | None = None,
) -> None:
    """Email the tenant (``contact_email``) about a new contact request.

    From the platform's transactional sender (``settings.notification_from_email``)
    with the tenant's business name as the display name — NOT the tenant's
    warm-up outreach inbox. The latter caused "501 invalid sender domain"
    bounces whenever the operator's mailbox sat on the same root domain as the
    outreach subdomain (info@totaltrade.it rejecting commerciale.totaltrade.it
    as a spoof, 2026-06-18). Reply-To = prospect email so the installer answers
    the lead in one click. Fail-open.

    ``proposal_resent_to`` flows through to the email body so the operator
    sees that the proposal was already auto-sent to the prospect's address.
    """
    to_email = (tenant_data.get("contact_email") or "").strip()
    if not to_email:
        return
    try:
        base_from = (settings.notification_from_email or "").strip()
        # Defensive fallback: if the platform sender isn't configured, fall back
        # to the tenant inbox (legacy behaviour) rather than dropping the alert.
        if not base_from:
            base_from = _active_inbox_from_address(sb, tenant_id) or ""
        if not base_from:
            log.warning("appointment.email_no_sender", tenant_id=tenant_id)
            return
        biz = (tenant_data.get("business_name") or "").strip()
        from_addr = f"{biz} <{base_from}>" if biz and "<" not in base_from else base_from
        subject, html, text = build_contact_request_email(
            payload, dossier_url, tenant_data.get("business_name"), proposal_resent_to
        )
        await send_email(
            SendEmailInput(
                from_address=from_addr,
                to=[to_email],
                subject=subject,
                html=html,
                text=text,
                reply_to=(payload.get("email") or None),
            )
        )
        log.info("appointment.email_sent", tenant_id=tenant_id, to=to_email)
    except Exception as exc:  # noqa: BLE001
        log.warning("appointment.email_failed", tenant_id=tenant_id, err=str(exc)[:200])


async def fire_appointment_webhook(
    webhook_url: str,
    *,
    lead_id: str,
    payload: dict[str, Any],
    dossier_url: str | None,
) -> None:
    """POST the contact request to the tenant's CRM webhook. Fail-open."""
    if not webhook_url:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                webhook_url,
                json={
                    "lead_id": lead_id,
                    "created_at": datetime.now(tz=UTC).isoformat(),
                    # Provenance tag: in the client CRM the contact shows
                    # up as generated by the Solar Lead platform, linked to
                    # the dossier/proposal.
                    "source": "Solar Lead",
                    "dossier_url": dossier_url,
                    "contact_name": payload.get("contact_name"),
                    "phone": payload.get("phone"),
                    "email": payload.get("email"),
                    "preferred_time": payload.get("preferred_time"),
                    "notes": payload.get("notes"),
                },
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "appointment.webhook_failed",
            lead_id=lead_id,
            webhook_url=webhook_url,
            err=str(exc),
        )
