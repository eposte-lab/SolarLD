"""Email-notify the tenant operator on high-intent inbound events.

Two events trigger an immediate operator notification:

  * ``lead.appointment_requested`` — prospect filled the contact form
    on the lead portal asking the operator to call back. This is the
    single highest-intent signal the funnel produces; the operator
    needs to know within seconds, not at the next dashboard visit.

  * ``lead.bolletta_uploaded`` — prospect uploaded their utility bill.
    Slightly lower intent (they're researching, not yet asking for
    contact) but still strong, and the operator usually wants to
    follow up while the prospect is engaged.

Implementation note: this is fire-and-forget. We schedule the call as
an ``asyncio.create_task`` from the public route so the prospect's
HTTP response returns in <100 ms regardless of how slow Resend is.
Failures log but never propagate — the realtime toaster + dashboard
timeline are the durable signals; the operator email is a best-
effort convenience.

Why not via a dedicated worker queue: today's volume is < 50 inbound
events / day per tenant. Once we cross ~5/min we should move this
to ``arq`` so the API isn't doing email work on the request path.
"""

from __future__ import annotations

from typing import Any

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from .resend_service import SendEmailInput, send_email

log = get_logger(__name__)


def _format_url(payload: dict[str, Any], *, tenant_dashboard_url: str | None) -> str | None:
    """Best-effort link back to the lead detail page in the dashboard."""
    lead_id = payload.get("lead_id")
    if not lead_id or not tenant_dashboard_url:
        return None
    return f"{tenant_dashboard_url.rstrip('/')}/leads/{lead_id}"


def _appointment_template(
    *, business_name: str, payload: dict[str, Any], dashboard_link: str | None
) -> tuple[str, str, str]:
    """Subject + HTML + text for a contact-form submission."""
    contact_name = payload.get("contact_name") or "Un decision-maker"
    contact_email = payload.get("contact_email") or ""
    contact_phone = payload.get("contact_phone") or ""
    message = (payload.get("message") or "").strip()

    subject = f"🔥 {contact_name} di {business_name} chiede di essere ricontattato"

    rows = [f"<strong>Azienda:</strong> {business_name}"]
    if contact_name:
        rows.append(f"<strong>Persona:</strong> {contact_name}")
    if contact_phone:
        rows.append(
            f"<strong>Telefono:</strong> "
            f'<a href="tel:{contact_phone}">{contact_phone}</a>'
        )
    if contact_email:
        rows.append(
            f"<strong>Email:</strong> "
            f'<a href="mailto:{contact_email}">{contact_email}</a>'
        )
    if message:
        rows.append(f"<strong>Messaggio:</strong><br>{message}")
    rows_html = "<br>".join(rows)

    cta_html = (
        f'<p style="margin-top:24px"><a href="{dashboard_link}" '
        f'style="background:#10b981;color:#fff;padding:12px 24px;'
        f'border-radius:9999px;text-decoration:none;font-weight:600">'
        f"Apri scheda lead</a></p>"
        if dashboard_link
        else ""
    )
    html = (
        f"<div style=\"font-family:Arial,sans-serif;color:#0f172a\">"
        f"<h2 style=\"margin:0 0 8px\">Richiesta di contatto dal portale</h2>"
        f"<p style=\"color:#475569;margin:0 0 16px\">"
        f"È il segnale d'intent più alto della pipeline — "
        f"il prospect ha compilato il form sulla scheda lead.</p>"
        f"<div style=\"background:#f1f5f9;padding:16px;border-radius:12px\">"
        f"{rows_html}"
        f"</div>"
        f"{cta_html}"
        f"</div>"
    )
    text = (
        f"Richiesta di contatto dal portale\n\n"
        f"{contact_name} di {business_name} ha chiesto di essere ricontattato.\n"
        + (f"Telefono: {contact_phone}\n" if contact_phone else "")
        + (f"Email: {contact_email}\n" if contact_email else "")
        + (f"Messaggio: {message}\n" if message else "")
        + (f"\nApri la scheda: {dashboard_link}\n" if dashboard_link else "")
    )
    return subject, html, text


def _bolletta_template(
    *, business_name: str, payload: dict[str, Any], dashboard_link: str | None
) -> tuple[str, str, str]:
    kwh = payload.get("kwh")
    eur = payload.get("eur")
    metrics: list[str] = []
    if kwh:
        metrics.append(f"{int(kwh):,} kWh/anno".replace(",", "."))
    if eur:
        metrics.append(f"€ {int(eur):,}/anno".replace(",", "."))
    metrics_html = " · ".join(metrics) if metrics else "(in elaborazione OCR)"

    subject = f"Bolletta caricata · {business_name}"
    cta = (
        f'<p style="margin-top:16px"><a href="{dashboard_link}" '
        f'style="background:#0f172a;color:#fff;padding:10px 20px;'
        f'border-radius:9999px;text-decoration:none;font-weight:600">'
        f"Apri scheda lead</a></p>"
        if dashboard_link
        else ""
    )
    html = (
        f"<div style=\"font-family:Arial,sans-serif;color:#0f172a\">"
        f"<h2 style=\"margin:0 0 8px\">Bolletta caricata sul portale</h2>"
        f"<p style=\"color:#475569;margin:0 0 16px\">"
        f"<strong>{business_name}</strong> ha caricato la bolletta. "
        f"Consumi rilevati: <strong>{metrics_html}</strong>.</p>"
        f"{cta}"
        f"</div>"
    )
    text = (
        f"Bolletta caricata sul portale\n\n"
        f"{business_name} ha caricato la bolletta.\n"
        f"Consumi: {metrics_html}\n"
        + (f"\nApri scheda: {dashboard_link}\n" if dashboard_link else "")
    )
    return subject, html, text


# Map the event_type enum to a (display label, template builder).
_TEMPLATES = {
    "lead.appointment_requested": _appointment_template,
    "lead.bolletta_uploaded": _bolletta_template,
}


async def notify_operator(
    *,
    tenant_id: str,
    lead_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Email the tenant's contact_email with a high-intent event summary.

    Best-effort: never raises. Caller schedules with
    ``asyncio.create_task`` so the response path doesn't block.
    """
    if event_type not in _TEMPLATES:
        return

    sb = get_service_client()
    tenant_res = (
        sb.table("tenants")
        .select(
            "id, business_name, contact_email, email_from_domain, email_from_name"
        )
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    rows = tenant_res.data or []
    if not rows:
        log.info("operator_notify.tenant_not_found", tenant_id=tenant_id)
        return

    tenant = rows[0]
    recipient = (tenant.get("contact_email") or "").strip()
    if not recipient:
        log.info(
            "operator_notify.no_contact_email",
            tenant_id=tenant_id,
            event_type=event_type,
            note="set tenants.contact_email to receive operator alerts",
        )
        return

    # Dashboard URL — derived from the same env-var the lead-portal uses.
    # We don't have a tenant-specific dashboard host today; the dashboard
    # is a single Next.js deployment per environment.
    from ..core.config import settings
    dashboard_link = None
    if settings.next_public_dashboard_url:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(settings.next_public_dashboard_url)
        origin = urlunparse(
            (parsed.scheme or "https", parsed.netloc, "", "", "", "")
        )
        dashboard_link = f"{origin.rstrip('/')}/leads/{lead_id}"

    subject_business = tenant.get("business_name") or "un'azienda"
    # Augment payload with business_name for the template — convenient.
    template_builder = _TEMPLATES[event_type]
    subject, html, text = template_builder(
        business_name=_resolve_lead_company_name(sb, lead_id) or subject_business,
        payload=payload,
        dashboard_link=dashboard_link,
    )

    from_domain = tenant.get("email_from_domain") or "solarlead.it"
    from_name = tenant.get("email_from_name") or "SolarLead"
    payload_email = SendEmailInput(
        from_address=f"{from_name} alerts <alerts@{from_domain}>",
        to=[recipient],
        subject=subject,
        html=html,
        text=text,
        tags={"kind": "operator_alert", "event_type": event_type},
    )

    try:
        result = await send_email(payload_email)
        log.info(
            "operator_notify.sent",
            tenant_id=tenant_id,
            lead_id=lead_id,
            event_type=event_type,
            message_id=result.id,
            recipient=recipient,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "operator_notify.send_failed",
            tenant_id=tenant_id,
            lead_id=lead_id,
            event_type=event_type,
            err_type=type(exc).__name__,
            err=str(exc)[:200],
        )


def _resolve_lead_company_name(sb: Any, lead_id: str) -> str | None:
    """Best-effort lookup: subjects.business_name for the lead."""
    try:
        res = (
            sb.table("leads")
            .select("subject_id, subjects(business_name)")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return None
        subj = rows[0].get("subjects") or {}
        if isinstance(subj, list):
            subj = subj[0] if subj else {}
        return subj.get("business_name")
    except Exception:  # noqa: BLE001 — best-effort
        return None
