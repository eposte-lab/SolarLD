"""Daily / weekly digest emails for tenants.

Each tenant gets a summary of what happened in their pipeline in
the last 24h (daily) or 7d (weekly). The composition lives here as
a pure function so tests can snapshot the HTML without hitting the
DB or Resend. The cron job in ``workers.cron`` is the only caller
that does I/O — it loads tenants, calls ``build_digest_payload``
and posts via ``resend_service``.

Tenants opt in via ``tenants.settings.feature_flags.daily_digest``
(bool) and ``.weekly_digest`` (bool). Default is both off until
super-admin flips them on from the console.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from .resend_service import SendEmailInput, send_email

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Pure composition — no I/O
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class DigestStats:
    tenant_name: str
    window_label: str           # "ultime 24 ore" | "ultimi 7 giorni"
    new_leads: int
    new_hot: int
    outreach_sent: int
    outreach_opened: int
    outreach_clicked: int
    contracts_signed: int
    total_cost_eur: float


def format_digest_html(stats: DigestStats) -> str:
    """Render a compact transactional HTML summary.

    Inline styles only — most webmail strips ``<style>`` tags in the
    preview pane. Kept deliberately minimal: the goal is the numbers,
    not a marketing layout.
    """
    row_style = (
        "padding:10px 0;border-bottom:1px solid #e5e5e5;"
        "font:14px/1.4 -apple-system,Segoe UI,sans-serif;"
    )
    num = (
        "font:600 18px/1 -apple-system,Segoe UI,sans-serif;"
        "color:#0f172a;float:right;"
    )
    rows = [
        ("Nuovi lead", stats.new_leads),
        ("Di cui HOT", stats.new_hot),
        ("Email inviate", stats.outreach_sent),
        ("Email aperte", stats.outreach_opened),
        ("Email cliccate", stats.outreach_clicked),
        ("Contratti firmati", stats.contracts_signed),
    ]
    body_rows = "\n".join(
        f'<div style="{row_style}">{label}'
        f'<span style="{num}">{value}</span></div>'
        for label, value in rows
    )
    return f"""<!doctype html>
<html lang="it">
  <body style="margin:0;background:#f4f7f6;padding:24px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="max-width:520px;margin:0 auto;background:#ffffff;
                  border-radius:12px;padding:28px;
                  font-family:-apple-system,Segoe UI,sans-serif;">
      <tr><td>
        <p style="margin:0;font-size:11px;letter-spacing:1.5px;
                  text-transform:uppercase;color:#64748b;">
          SolarLead · Riepilogo {stats.window_label}
        </p>
        <h1 style="margin:4px 0 0;font-size:24px;letter-spacing:-0.5px;
                   color:#0f172a;">
          Ciao {stats.tenant_name}
        </h1>
        <p style="margin:12px 0 24px;color:#475569;font-size:14px;">
          Ecco come è andata la tua pipeline {stats.window_label}.
        </p>
        {body_rows}
        <div style="margin-top:16px;padding-top:12px;color:#475569;
                    font-size:13px;">
          Spesa API totale:
          <span style="float:right;font-weight:600;color:#0f172a;">
            €{stats.total_cost_eur:,.2f}
          </span>
        </div>
        <p style="margin-top:28px;font-size:12px;color:#94a3b8;">
          Apri la dashboard per approfondire:
          <a href="https://dashboard.solarlead.it/"
             style="color:#0f766e;">dashboard.solarlead.it</a>
        </p>
      </td></tr>
    </table>
  </body>
</html>"""


def format_digest_text(stats: DigestStats) -> str:
    return (
        f"SolarLead — riepilogo {stats.window_label}\n\n"
        f"Nuovi lead:         {stats.new_leads}\n"
        f"  di cui HOT:       {stats.new_hot}\n"
        f"Email inviate:      {stats.outreach_sent}\n"
        f"Email aperte:       {stats.outreach_opened}\n"
        f"Email cliccate:     {stats.outreach_clicked}\n"
        f"Contratti firmati:  {stats.contracts_signed}\n"
        f"Spesa API:          €{stats.total_cost_eur:,.2f}\n\n"
        "https://dashboard.solarlead.it/\n"
    )


# ---------------------------------------------------------------------------
# Side-effecting orchestration
# ---------------------------------------------------------------------------


async def _compute_stats(
    *, tenant_id: str, tenant_name: str, since: datetime, window_label: str
) -> DigestStats:
    """Roll up the counts for one tenant over the [since, now) window."""
    sb = get_service_client()
    since_iso = since.isoformat()

    # Fetch all metrics in parallel-ish via separate head-count queries.
    leads_all = (
        sb.table("leads")
        .select("id", count="exact", head=True)
        .eq("tenant_id", tenant_id)
        .gte("created_at", since_iso)
        .execute()
    )
    leads_hot = (
        sb.table("leads")
        .select("id", count="exact", head=True)
        .eq("tenant_id", tenant_id)
        .eq("score_tier", "hot")
        .gte("created_at", since_iso)
        .execute()
    )
    sent = (
        sb.table("leads")
        .select("id", count="exact", head=True)
        .eq("tenant_id", tenant_id)
        .gte("outreach_sent_at", since_iso)
        .execute()
    )
    opened = (
        sb.table("leads")
        .select("id", count="exact", head=True)
        .eq("tenant_id", tenant_id)
        .gte("outreach_opened_at", since_iso)
        .execute()
    )
    clicked = (
        sb.table("leads")
        .select("id", count="exact", head=True)
        .eq("tenant_id", tenant_id)
        .gte("outreach_clicked_at", since_iso)
        .execute()
    )
    signed = (
        sb.table("leads")
        .select("id", count="exact", head=True)
        .eq("tenant_id", tenant_id)
        .eq("feedback", "contract_signed")
        .gte("feedback_at", since_iso)
        .execute()
    )
    cost_res = (
        sb.table("api_usage_log")
        .select("cost_cents")
        .eq("tenant_id", tenant_id)
        .gte("occurred_at", since_iso)
        .execute()
    )
    cost_eur = sum((r.get("cost_cents") or 0) for r in (cost_res.data or [])) / 100.0

    return DigestStats(
        tenant_name=tenant_name,
        window_label=window_label,
        new_leads=leads_all.count or 0,
        new_hot=leads_hot.count or 0,
        outreach_sent=sent.count or 0,
        outreach_opened=opened.count or 0,
        outreach_clicked=clicked.count or 0,
        contracts_signed=signed.count or 0,
        total_cost_eur=cost_eur,
    )


async def _send_digest_to_tenant(
    *,
    tenant: dict[str, Any],
    window_days: int,
    window_label: str,
) -> dict[str, Any]:
    """Compute + email one tenant's digest. Returns a summary row."""
    tenant_id = tenant["id"]
    recipient = tenant.get("contact_email")
    if not recipient:
        return {"tenant_id": tenant_id, "skipped": "no_contact_email"}

    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    stats = await _compute_stats(
        tenant_id=tenant_id,
        tenant_name=tenant.get("business_name") or "Installer",
        since=since,
        window_label=window_label,
    )

    # Skip completely empty digests — no signal, just inbox noise.
    if (
        stats.new_leads == 0
        and stats.outreach_sent == 0
        and stats.contracts_signed == 0
        and stats.total_cost_eur == 0
    ):
        return {"tenant_id": tenant_id, "skipped": "empty_window"}

    from_domain = tenant.get("email_from_domain") or "solarlead.it"
    from_name = tenant.get("email_from_name") or "SolarLead"
    subject = (
        "Riepilogo giornaliero — SolarLead"
        if window_days == 1
        else "Riepilogo settimanale — SolarLead"
    )
    payload = SendEmailInput(
        from_address=f"{from_name} <digest@{from_domain}>",
        to=[recipient],
        subject=subject,
        html=format_digest_html(stats),
        text=format_digest_text(stats),
        tags={"kind": "digest", "window": f"{window_days}d"},
    )
    try:
        result = await send_email(payload)
        return {
            "tenant_id": tenant_id,
            "sent": True,
            "message_id": result.id,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "digest.send_failed",
            tenant_id=tenant_id,
            window=window_days,
            err=str(exc),
        )
        return {"tenant_id": tenant_id, "sent": False, "error": str(exc)[:200]}


def _flag_enabled(settings_obj: dict[str, Any] | None, key: str) -> bool:
    if not settings_obj:
        return False
    flags = settings_obj.get("feature_flags") or {}
    return bool(flags.get(key, False))


async def send_daily_digests() -> dict[str, Any]:
    """Cron entry: email today's digest to every tenant that opted in."""
    sb = get_service_client()
    res = (
        sb.table("tenants")
        .select(
            "id, business_name, contact_email, "
            "email_from_domain, email_from_name, settings"
        )
        .eq("status", "active")
        .execute()
    )
    tenants = [
        t
        for t in (res.data or [])
        if _flag_enabled(t.get("settings"), "daily_digest")
    ]
    log.info("digest.daily.candidates", count=len(tenants))
    summary = [
        await _send_digest_to_tenant(
            tenant=t, window_days=1, window_label="ultime 24 ore"
        )
        for t in tenants
    ]
    return {"window": "daily", "results": summary}


async def send_weekly_digests() -> dict[str, Any]:
    """Cron entry: email this week's digest to every tenant that opted in."""
    sb = get_service_client()
    res = (
        sb.table("tenants")
        .select(
            "id, business_name, contact_email, "
            "email_from_domain, email_from_name, settings"
        )
        .eq("status", "active")
        .execute()
    )
    tenants = [
        t
        for t in (res.data or [])
        if _flag_enabled(t.get("settings"), "weekly_digest")
    ]
    log.info("digest.weekly.candidates", count=len(tenants))
    summary = [
        await _send_digest_to_tenant(
            tenant=t, window_days=7, window_label="ultimi 7 giorni"
        )
        for t in tenants
    ]
    return {"window": "weekly", "results": summary}
