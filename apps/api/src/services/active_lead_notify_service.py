"""Auto-notify when a lead ENTERS the dashboard "lead attivi" section.

A lead is "attivo" when it is ENGAGED (the same ENGAGEMENT_OR gate the
dashboard uses) AND ``operator_released_at IS NOT NULL``. The moment it
enters that set we email the tenant's configured recipients ONE message
for THAT lead (never batched, never repeated) — same look as the manual
"lead attivi" digest: status, plant size, estimated saving, all contacts,
and the dossier link.

Wiring:
  - ``active_lead_notify_cron`` (workers/cron.py, every 15 min) calls
    ``run_active_lead_notify()``.
  - Per-tenant opt-in + recipients live in ``tenants.settings``::

        "active_lead_notify": {
          "enabled": true,
          "from": "Total Trade <commerciale@commerciale.totaltrade.it>",
          "reply_to": "info@totaltrade.it",
          "recipients": ["info@totaltrade.it", "m.frezzanexp@hotmail.com"]
        }

  - Idempotency: ``leads.active_lead_notified_at`` (migration 0158). The
    backfill there stamps every already-active lead so enabling the cron
    never blasts the existing pipeline.

Blacklisted/opted-out leads are excluded — their dossier is 410 and we
must not push the operator to call someone who unsubscribed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..core.config import settings
from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from .resend_service import SendEmailInput, send_email

log = get_logger(__name__)

# Mirror of the dashboard ENGAGEMENT_OR (apps/dashboard/src/lib/data/leads.ts):
# one PostgREST `or` group, AND-ed with the tenant / released / unnotified
# filters applied separately on the query.
ENGAGEMENT_OR = (
    "outreach_clicked_at.not.is.null,"
    "dashboard_visited_at.not.is.null,"
    "whatsapp_initiated_at.not.is.null,"
    "outreach_replied_at.not.is.null,"
    "portal_sessions.gt.0,"
    "engagement_score.gt.0,"
    "last_portal_event_at.not.is.null,"
    "pipeline_status.eq.clicked,"
    "pipeline_status.eq.engaged,"
    "pipeline_status.eq.whatsapp,"
    "pipeline_status.eq.appointment,"
    "pipeline_status.eq.closed_won,"
    "pipeline_status.eq.closed_lost"
)

# Safety cap per cron run: a bug that floods leads into the active set can
# never blast more than this many emails per 15-min tick (the rest carry
# to the next run, still one-per-lead).
PER_RUN_CAP = 25

_STATUS_LABEL: dict[str, tuple[str, str]] = {
    "appointment": ("Appuntamento richiesto", "#DC2626"),
    "to_call": ("Da chiamare", "#EA580C"),
    "engaged": ("Engaged", "#16A34A"),
    "clicked": ("Engaged", "#16A34A"),
    "whatsapp": ("WhatsApp", "#16A34A"),
    "closed_won": ("Cliente", "#16A34A"),
    "closed_lost": ("Chiuso perso", "#6B7280"),
}


def _truthy(v: Any) -> bool:
    """Accept a JSONB flag stored as the boolean ``true`` or the string
    ``"true"`` (migration 0146 writes the string form)."""
    return v is True or (isinstance(v, str) and v.strip().lower() == "true")


def _one(v: Any) -> dict[str, Any]:
    """PostgREST embeds can come back as an object or a 1-element list."""
    if isinstance(v, list):
        return v[0] if v else {}
    return v if isinstance(v, dict) else {}


def _fmt_eur(n: Any) -> str | None:
    try:
        return f"{round(float(n)):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return None


def _fmt_kwp(n: Any) -> str | None:
    try:
        return f"{round(float(n)):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return None


def _fmt_last(ts: Any) -> str | None:
    """ISO timestamp → 'DD/MM HH:MM' (Italian short)."""
    if not ts or not isinstance(ts, str):
        return None
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return d.strftime("%d/%m %H:%M")
    except ValueError:
        return None


def build_active_lead_email(
    lead: dict[str, Any], portal_origin: str
) -> tuple[str, str]:
    """Pure: build (subject, html) for a single newly-active lead."""
    subj = _one(lead.get("subjects"))
    roof = _one(lead.get("roofs"))
    roi = lead.get("roi_data") if isinstance(lead.get("roi_data"), dict) else {}

    name = (subj.get("business_name") or "Nuovo lead").strip()
    email = subj.get("decision_maker_email")
    phone = subj.get("decision_maker_phone")
    prov = roof.get("provincia")
    kwp = _fmt_kwp(roof.get("estimated_kwp"))
    eur = _fmt_eur(roi.get("yearly_savings_eur") or roi.get("realistic_yearly_savings_eur"))
    status = str(lead.get("pipeline_status") or "engaged")
    label, color = _STATUS_LABEL.get(status, ("Lead attivo", "#16A34A"))
    eng = lead.get("engagement_score")
    slug = lead.get("public_slug")
    url = f"{portal_origin}/dossier/{slug}" if portal_origin and slug else None

    if lead.get("outreach_clicked_at"):
        activity = "Ha aperto e cliccato la proposta"
    elif lead.get("outreach_opened_at"):
        activity = "Ha aperto la proposta"
    else:
        activity = "Ha visitato il dossier"
    last = _fmt_last(lead.get("last_portal_event_at"))

    # --- meta line (only the bits we actually have) ---
    meta_bits: list[str] = []
    if prov:
        meta_bits.append(f"Prov. {prov}")
    if kwp:
        meta_bits.append(f"Impianto stimato <b>{kwp} kWp</b>")
    if eur:
        meta_bits.append(
            f'Risparmio stimato <b style="color:#16A34A;">&euro; {eur}/anno</b>'
        )
    meta = " &nbsp;&bull;&nbsp; ".join(meta_bits)

    # --- contacts (omit a missing line) ---
    contact_bits: list[str] = []
    if email:
        contact_bits.append(
            f'&#128231; <a href="mailto:{email}" style="color:#2563eb;text-decoration:none;">{email}</a>'
        )
    if phone:
        tel = "".join(ch for ch in str(phone) if ch.isdigit() or ch == "+")
        contact_bits.append(
            f'&#128222; <a href="tel:{tel}" style="color:#2563eb;text-decoration:none;">{phone}</a>'
        )
    contacts = "<br>".join(contact_bits)

    eng_line_bits = [activity]
    if eng is not None:
        eng_line_bits.append(f"Engagement {eng}/100")
    if last:
        eng_line_bits.append(f"ultima attività {last}")
    eng_line = " &nbsp;&bull;&nbsp; ".join(eng_line_bits)

    button = (
        f'<a href="{url}" style="display:inline-block;background:#16A34A;color:#fff;'
        f'font:700 14px/1 Arial,sans-serif;text-decoration:none;padding:11px 18px;'
        f'border-radius:8px;">Apri il dossier &rarr;</a>'
        if url
        else ""
    )

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f1f5f9;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:24px 12px;">
<tr><td align="center">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid #e2e8f0;">
    <tr><td style="background:#0f172a;padding:20px 24px;">
      <div style="font:800 18px/1 Arial,sans-serif;color:#ffffff;">Total&nbsp;Trade</div>
      <div style="font:400 13px/1.4 Arial,sans-serif;color:#94a3b8;margin-top:4px;">Nuovo lead attivo</div>
    </td></tr>
    <tr><td style="padding:20px 24px 4px 24px;">
      <p style="margin:0;font:400 15px/1.6 Arial,sans-serif;color:#334155;">
        <b>{name}</b> &egrave; appena entrato tra i tuoi lead attivi: ha ricevuto la proposta e mostrato interesse. Ecco i dettagli e i contatti per richiamarlo.
      </p>
    </td></tr>
    <tr><td style="padding:16px 24px 8px 24px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e5e7eb;border-radius:12px;border-collapse:separate;">
        <tr><td style="padding:16px 18px 6px 18px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
            <td style="font:700 17px/1.3 Arial,sans-serif;color:#0f172a;">{name}</td>
            <td align="right" style="white-space:nowrap;"><span style="display:inline-block;background:{color};color:#fff;font:700 11px/1 Arial,sans-serif;padding:6px 10px;border-radius:999px;">{label}</span></td>
          </tr></table>
          <p style="margin:8px 0 0 0;font:400 13px/1.5 Arial,sans-serif;color:#475569;">{meta}</p>
          <p style="margin:8px 0 0 0;font:400 13px/1.6 Arial,sans-serif;color:#334155;">{contacts}</p>
          <p style="margin:8px 0 0 0;font:400 12px/1.5 Arial,sans-serif;color:#64748b;">{eng_line}</p>
        </td></tr>
        <tr><td style="padding:10px 18px 16px 18px;">{button}</td></tr>
      </table>
    </td></tr>
    <tr><td style="padding:8px 24px 24px 24px;border-top:1px solid #e2e8f0;">
      <p style="margin:14px 0 0 0;font:400 12px/1.6 Arial,sans-serif;color:#94a3b8;">Notifica automatica dal sistema di acquisizione Total Trade. I numeri di impianto e risparmio sono stime dai dati satellitari, da confermare in sopralluogo.</p>
    </td></tr>
  </table>
</td></tr></table>
</body></html>"""

    return f"Nuovo lead attivo: {name}", html


def _select_newly_active(sb: Any, tenant_id: str) -> list[dict[str, Any]]:
    """Leads that ENTERED 'lead attivi' but haven't been notified yet."""
    res = (
        sb.table("leads")
        .select(
            "id, public_slug, pipeline_status, engagement_score, "
            "outreach_opened_at, outreach_clicked_at, last_portal_event_at, "
            "roi_data, "
            "subjects(business_name, decision_maker_email, decision_maker_phone), "
            "roofs(provincia, estimated_kwp)"
        )
        .eq("tenant_id", tenant_id)
        .is_("active_lead_notified_at", "null")
        .not_.is_("operator_released_at", "null")
        .neq("pipeline_status", "blacklisted")
        .or_(ENGAGEMENT_OR)
        .order("engagement_score", desc=True)
        .limit(PER_RUN_CAP)
        .execute()
    )
    return res.data or []


async def run_active_lead_notify() -> dict[str, int]:
    """Scan every opted-in tenant, email each newly-active lead once."""
    sb = get_service_client()
    now = datetime.now(UTC)
    origin = (settings.next_public_lead_portal_url or "").rstrip("/")

    tenants = sb.table("tenants").select("id, settings").execute().data or []
    tenants_active = 0
    sent = 0

    for t in tenants:
        cfg = (t.get("settings") or {}).get("active_lead_notify") or {}
        if not _truthy(cfg.get("enabled")):
            continue
        recipients = [r for r in (cfg.get("recipients") or []) if r]
        from_addr = cfg.get("from")
        reply_to = cfg.get("reply_to")
        if not recipients or not from_addr:
            log.warning("active_lead_notify.misconfigured", tenant_id=str(t.get("id")))
            continue
        tenants_active += 1

        leads = _select_newly_active(sb, str(t["id"]))
        if len(leads) >= PER_RUN_CAP:
            log.warning(
                "active_lead_notify.capped",
                tenant_id=str(t.get("id")),
                cap=PER_RUN_CAP,
            )
        for lead in leads:
            subject, html = build_active_lead_email(lead, origin)
            try:
                await send_email(
                    SendEmailInput(
                        from_address=from_addr,
                        to=recipients,
                        subject=subject,
                        html=html,
                        reply_to=reply_to,
                    )
                )
            except Exception:  # noqa: BLE001 — leave unstamped → retried next run
                log.exception(
                    "active_lead_notify.send_failed", lead_id=str(lead.get("id"))
                )
                continue
            # Stamp only AFTER a successful send (one-per-lead, never repeated).
            sb.table("leads").update(
                {"active_lead_notified_at": now.isoformat()}
            ).eq("id", lead["id"]).execute()
            sent += 1

    log.info("cron.active_lead_notify.done", tenants=tenants_active, sent=sent)
    return {"tenants": tenants_active, "sent": sent}
