"""Task 13 — Shadow Domain Setup CLI.

Generates everything an operator needs to stand up the SolarLead
shadow outreach domain infrastructure:

  1. Complete DNS record tables for all 4 domains:
       • Google Workspace MX  (5 records per domain)
       • SPF TXT
       • DKIM TXT placeholder  (key generated in Google Admin Console)
       • DMARC TXT with p=none + reporting address
       • Tracking CNAME  (go.{domain} → track.solarld.app)

  2. 12 Italian persona mailboxes (3 per domain), each with a suggested
     display name that reads as a real person's name.

  3. A step-by-step Markdown provisioning guide that the founding team
     can follow to create Google Workspace accounts, paste DNS records,
     and enroll inboxes in Smartlead warm-up.

  4. A JSON topology file (``shadow_domains_topology.json``) that
     ``smartlead_service.py`` (Task 14) reads to auto-enroll inboxes.

Usage
-----
::

    # Print guide to stdout
    python -m src.scripts.shadow_domain_setup

    # Write guide + topology file to ./infra/
    python -m src.scripts.shadow_domain_setup --save --output-dir ./infra

    # Only DNS tables (useful for registrar copy-paste)
    python -m src.scripts.shadow_domain_setup --format dns-only

    # Only topology JSON (for CI/CD scripting)
    python -m src.scripts.shadow_domain_setup --format json-only

Design
------
• Zero external dependencies — stdlib only. Works offline.
• All domain-specific config is in the ``SHADOW_DOMAINS`` constant at
  the top of the file. Add / remove domains there; the rest auto-derives.
• DKIM keys cannot be pre-generated — they are issued by the Google
  Admin Console after DNS delegation. The script emits clear TODO
  placeholders and exact navigation steps.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import date
from typing import Literal


# ---------------------------------------------------------------------------
# Domain topology — edit here when domains/mailboxes change
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Mailbox:
    local_part: str      # e.g. "luca.ferrari"
    display_name: str    # e.g. "Luca Ferrari"
    title: str           # short persona title for the guide


@dataclass(frozen=True)
class ShadowDomain:
    domain: str
    purpose: str          # human label
    dmarc_rua: str        # DMARC reporting address (brand domain)
    mailboxes: tuple[Mailbox, ...]

    @property
    def tracking_host(self) -> str:
        return f"go.{self.domain}"

    @property
    def emails(self) -> list[str]:
        return [f"{mb.local_part}@{self.domain}" for mb in self.mailboxes]


# Brand domain (used for DMARC reporting, NOT for cold outreach)
BRAND_DOMAIN = "solarlead.it"
DMARC_RUA = f"dmarc@{BRAND_DOMAIN}"

# Tracking infrastructure (all tracking CNAMEs point here)
TRACKING_CNAME_TARGET = "track.solarld.app"

SHADOW_DOMAINS: tuple[ShadowDomain, ...] = (
    ShadowDomain(
        domain="solarlead-progetti.it",
        purpose="Progetti fotovoltaici (focus manifatturiero / PMI)",
        dmarc_rua=DMARC_RUA,
        mailboxes=(
            Mailbox("luca.ferrari",    "Luca Ferrari",    "Responsabile Sviluppo Progetti"),
            Mailbox("giulia.romano",   "Giulia Romano",   "Consulente Impianti Industriali"),
            Mailbox("marco.esposito",  "Marco Esposito",  "Tecnico Fotovoltaico Senior"),
        ),
    ),
    ShadowDomain(
        domain="solarlead-energia.it",
        purpose="Energia rinnovabile (focus logistica / grande distribuzione)",
        dmarc_rua=DMARC_RUA,
        mailboxes=(
            Mailbox("sara.bianchi",   "Sara Bianchi",   "Energy Manager"),
            Mailbox("andrea.conti",   "Andrea Conti",   "Consulente Risparmio Energetico"),
            Mailbox("chiara.ricci",   "Chiara Ricci",   "Responsabile Efficienza Energetica"),
        ),
    ),
    ShadowDomain(
        domain="info-solarlead.it",
        purpose="Info / discovery (primo contatto generico)",
        dmarc_rua=DMARC_RUA,
        mailboxes=(
            Mailbox("matteo.russo",  "Matteo Russo",  "Specialista Fotovoltaico"),
            Mailbox("elena.marino",  "Elena Marino",  "Consulente Energie Rinnovabili"),
            Mailbox("davide.greco",  "Davide Greco",  "Account Manager Solare"),
        ),
    ),
    ShadowDomain(
        domain="pro-solarlead.com",
        purpose="Pro (.com) — targeting aziende internazionalizzate / export",
        dmarc_rua=DMARC_RUA,
        mailboxes=(
            Mailbox("sofia.lombardi",    "Sofia Lombardi",    "Solar Solutions Consultant"),
            Mailbox("roberto.fontana",   "Roberto Fontana",   "Business Development Manager"),
            Mailbox("valentina.caruso",  "Valentina Caruso",  "Senior PV Project Advisor"),
        ),
    ),
)

# Google Workspace MX records (identical for every Google Workspace domain)
_GW_MX_RECORDS: tuple[tuple[int, str], ...] = (
    (1,  "ASPMX.L.GOOGLE.COM."),
    (5,  "ALT1.ASPMX.L.GOOGLE.COM."),
    (5,  "ALT2.ASPMX.L.GOOGLE.COM."),
    (10, "ALT3.ASPMX.L.GOOGLE.COM."),
    (10, "ALT4.ASPMX.L.GOOGLE.COM."),
)


# ---------------------------------------------------------------------------
# DNS record builders
# ---------------------------------------------------------------------------

def mx_records(domain: ShadowDomain) -> list[dict]:
    return [
        {
            "type": "MX",
            "host": "@",
            "value": value,
            "priority": priority,
            "ttl": 3600,
        }
        for priority, value in _GW_MX_RECORDS
    ]


def spf_record(domain: ShadowDomain) -> dict:
    return {
        "type": "TXT",
        "host": "@",
        "value": "v=spf1 include:_spf.google.com ~all",
        "ttl": 3600,
        "note": "SPF — authorises Google Workspace to send on behalf of this domain",
    }


def dkim_placeholder(domain: ShadowDomain) -> dict:
    """DKIM TXT record.

    The actual key is generated in Google Admin Console →
    Apps → Google Workspace → Gmail → Authenticate email → Generate new record.
    Selector is typically 'google' unless changed.
    """
    return {
        "type": "TXT",
        "host": f"google._domainkey.{domain.domain}",
        "value": "v=DKIM1; k=rsa; p=<PASTE_KEY_FROM_GOOGLE_ADMIN_CONSOLE>",
        "ttl": 3600,
        "note": (
            "DKIM — key NOT yet generated. Follow Step 3 in the provisioning "
            "guide to obtain the real value from Google Admin Console."
        ),
    }


def dmarc_record(domain: ShadowDomain) -> dict:
    return {
        "type": "TXT",
        "host": f"_dmarc.{domain.domain}",
        "value": (
            f"v=DMARC1; p=none; "
            f"rua=mailto:{domain.dmarc_rua}; "
            f"ruf=mailto:{domain.dmarc_rua}; "
            f"pct=100; adkim=s; aspf=s"
        ),
        "ttl": 3600,
        "note": (
            "DMARC p=none = monitoring only. After 14 days of clean "
            "reports upgrade to p=quarantine via DNS."
        ),
    }


def tracking_cname(domain: ShadowDomain) -> dict:
    return {
        "type": "CNAME",
        "host": domain.tracking_host,
        "value": f"{TRACKING_CNAME_TARGET}.",
        "ttl": 300,
        "note": "Tracking host — routes click/open events through SolarLead infra",
    }


def all_records_for(domain: ShadowDomain) -> list[dict]:
    records: list[dict] = []
    records.extend(mx_records(domain))
    records.append(spf_record(domain))
    records.append(dkim_placeholder(domain))
    records.append(dmarc_record(domain))
    records.append(tracking_cname(domain))
    return records


# ---------------------------------------------------------------------------
# Topology JSON (consumed by smartlead_service.py)
# ---------------------------------------------------------------------------

def build_topology_json() -> dict:
    """Return the full shadow domain topology as a dict ready for JSON serialise."""
    domains_out = []
    for sd in SHADOW_DOMAINS:
        inboxes_out = []
        for mb in sd.mailboxes:
            inboxes_out.append(
                {
                    "email": f"{mb.local_part}@{sd.domain}",
                    "display_name": mb.display_name,
                    "local_part": mb.local_part,
                    "persona_title": mb.title,
                    # SMTP / IMAP settings for Google Workspace
                    "smtp_host": "smtp.gmail.com",
                    "smtp_port": 587,
                    "smtp_use_tls": True,
                    "imap_host": "imap.gmail.com",
                    "imap_port": 993,
                    "imap_use_tls": True,
                    # Smartlead warm-up defaults (Sprint 6.3 curve)
                    "warmup_enabled": True,
                    "warmup_target_per_day": 40,
                    "warmup_daily_rampup": 2,
                    "warmup_reply_rate_pct": 30,
                    # SolarLead inbox metadata
                    "provider": "gmail_oauth",
                    "purpose": "outreach",
                    "daily_cap": 50,     # steady-state; overridden by warmup curve
                }
            )
        domains_out.append(
            {
                "domain": sd.domain,
                "purpose_label": sd.purpose,
                "dmarc_rua": sd.dmarc_rua,
                "tracking_host": sd.tracking_host,
                "tracking_cname_target": TRACKING_CNAME_TARGET,
                "dns_records": all_records_for(sd),
                "inboxes": inboxes_out,
            }
        )
    return {
        "generated_at": date.today().isoformat(),
        "brand_domain": BRAND_DOMAIN,
        "shadow_domains": domains_out,
        "total_inboxes": sum(len(sd.mailboxes) for sd in SHADOW_DOMAINS),
        "daily_capacity_at_steady_state": sum(
            len(sd.mailboxes) * 50 for sd in SHADOW_DOMAINS
        ),
    }


# ---------------------------------------------------------------------------
# Text formatters
# ---------------------------------------------------------------------------

def _rule(char: str = "─", width: int = 72) -> str:
    return char * width


def _table_row(cols: list[str], widths: list[int]) -> str:
    parts = [str(c).ljust(w) for c, w in zip(cols, widths)]
    return "│ " + " │ ".join(parts) + " │"


def _table_header(cols: list[str], widths: list[int]) -> str:
    top = "┌─" + "─┬─".join("─" * w for w in widths) + "─┐"
    hdr = _table_row(cols, widths)
    sep = "├─" + "─┼─".join("─" * w for w in widths) + "─┤"
    return "\n".join([top, hdr, sep])


def _table_footer(widths: list[int]) -> str:
    return "└─" + "─┴─".join("─" * w for w in widths) + "─┘"


def _dns_section(sd: ShadowDomain) -> str:
    lines: list[str] = []
    lines.append(f"\n### {sd.domain}\n")
    lines.append(f"*{sd.purpose}*\n")

    # MX records
    lines.append("\n**MX Records** (Google Workspace)\n")
    lines.append("```")
    lines.append(f"{'Priority':<10}  {'Value'}")
    lines.append("-" * 50)
    for rec in mx_records(sd):
        lines.append(f"{rec['priority']:<10}  {rec['value']}")
    lines.append("```\n")

    # SPF
    spf = spf_record(sd)
    lines.append("**SPF TXT** (host: `@`)\n")
    lines.append("```")
    lines.append(spf["value"])
    lines.append("```\n")

    # DKIM
    dkim = dkim_placeholder(sd)
    lines.append(f"**DKIM TXT** (host: `google._domainkey.{sd.domain}`)\n")
    lines.append("```")
    lines.append("v=DKIM1; k=rsa; p=<PASTE_KEY_FROM_GOOGLE_ADMIN_CONSOLE>")
    lines.append("```")
    lines.append(
        "> ⚠️  DKIM key must be generated in Google Admin Console. "
        "See Step 3 below.\n"
    )

    # DMARC
    dmarc = dmarc_record(sd)
    lines.append(f"**DMARC TXT** (host: `_dmarc.{sd.domain}`)\n")
    lines.append("```")
    lines.append(dmarc["value"])
    lines.append("```\n")

    # Tracking CNAME
    cname = tracking_cname(sd)
    lines.append(f"**Tracking CNAME** (host: `go.{sd.domain}`)\n")
    lines.append("```")
    lines.append(f"go.{sd.domain}  CNAME  {TRACKING_CNAME_TARGET}.")
    lines.append("```\n")

    # Mailboxes
    lines.append("**Mailboxes to create in Google Admin Console**\n")
    lines.append("| Email | Display Name | Persona |")
    lines.append("|-------|-------------|---------|")
    for mb in sd.mailboxes:
        lines.append(
            f"| `{mb.local_part}@{sd.domain}` "
            f"| {mb.display_name} "
            f"| {mb.title} |"
        )
    lines.append("")
    return "\n".join(lines)


def build_markdown_guide() -> str:
    """Return the complete Markdown provisioning guide."""

    total_inboxes = sum(len(sd.mailboxes) for sd in SHADOW_DOMAINS)
    total_daily = total_inboxes * 50

    guide_parts: list[str] = []

    guide_parts.append(
        textwrap.dedent(
            f"""\
            # SolarLead Shadow Domain Provisioning Guide

            **Generated:** {date.today().isoformat()}
            **Domains:** {len(SHADOW_DOMAINS)}
            **Total mailboxes:** {total_inboxes}
            **Steady-state send capacity:** {total_daily} emails / day
            **Warm-up duration:** 21 days per inbox
            **Day-1 send capacity:** {total_inboxes * 10} emails / day

            ---

            ## Overview

            This guide walks through setting up {len(SHADOW_DOMAINS)} Google Workspace
            outreach domains with {total_inboxes} shared mailboxes (3 per domain).
            Each inbox is enrolled in Smartlead.ai automated warm-up.

            Architecture:
            - **Brand domain** (`{BRAND_DOMAIN}`) — used by Resend for transactional
              email (auth, notifications, lead portal). **Never used for cold outreach.**
            - **Shadow domains** (below) — dedicated Google Workspace domains used
              exclusively for B2B cold outreach. Reputation isolation: if one domain
              has a delivery issue it doesn't affect the brand or the other domains.

            Total time to complete setup: **~90 minutes** (mostly waiting for DNS propagation).

            ---

            ## Prerequisites

            - [ ] Access to the DNS control panel for all 4 domains
            - [ ] A Google Workspace Business Starter account per domain (€6/user/month)
            - [ ] A Smartlead.ai account with API key in `.env` as `SMARTLEAD_API_KEY`
            - [ ] `APP_SECRET_KEY` set in `.env` (Fernet key — `Fernet.generate_key()`)
            - [ ] DMARC reporting mailbox `dmarc@{BRAND_DOMAIN}` is receiving email

            ---

            ## Step 1 — Register Google Workspace for each domain

            For each of the {len(SHADOW_DOMAINS)} domains below:

            1. Go to <https://workspace.google.com/business/signup>
            2. Enter the domain name when prompted
            3. Complete the admin account setup. The admin account email is for
               administrative access only — **do NOT use it for outreach**.
            4. Follow the Google Workspace domain verification steps (usually a
               TXT record at `@` — this is separate from DKIM verification).
            5. Accept the billing; ~€6/user/month × 3 users per domain = ~€18/domain/month.

            > 💡 Use a single Google billing account across all 4 domains to simplify invoicing.

            ---

            ## Step 2 — Create the mailboxes

            In **Google Admin Console** (`admin.google.com`) for each domain, create
            exactly 3 user accounts matching the personas below. Use the display names
            exactly — they will appear in the "From:" field of every outreach email.

            """,
        )
    )

    for sd in SHADOW_DOMAINS:
        guide_parts.append(f"### {sd.domain}\n")
        guide_parts.append(f"*{sd.purpose}*\n")
        guide_parts.append("| Email | Display Name | Suggested password |")
        guide_parts.append("|-------|-------------|-------------------|")
        for mb in sd.mailboxes:
            # Suggest a deterministic placeholder password pattern
            pw_hint = f"`{mb.local_part.split('.')[0].capitalize()}#Solar{date.today().year}!`"
            guide_parts.append(
                f"| `{mb.local_part}@{sd.domain}` | {mb.display_name} | {pw_hint} *(change this)* |"
            )
        guide_parts.append("")

    guide_parts.append(
        textwrap.dedent(
            """\
            > 🔒  Store the actual passwords in 1Password / Bitwarden under the
            > "SolarLead Outreach Inboxes" vault. Never commit them to git.

            ---

            ## Step 3 — Configure DNS records

            Set DNS TTL to **300 seconds** on all records before you start.
            Lower TTL = faster iteration when debugging DNS issues.

            Add the records below at your DNS provider **for each domain**.
            After adding all records, run the SolarLead DNS verification endpoint:

            ```bash
            curl -X POST https://api.solarld.app/v1/email-domains/{domain-id}/dns-check
            ```

            Or from the dashboard: **Settings → Email Domains → Verify now**.

            """,
        )
    )

    for sd in SHADOW_DOMAINS:
        guide_parts.append(_dns_section(sd))

    guide_parts.append(
        textwrap.dedent(
            """\
            ---

            ## Step 4 — Generate and install DKIM keys

            For **each domain**:

            1. In Google Admin Console → **Apps → Google Workspace → Gmail → Authenticate email**
            2. Select the domain from the dropdown
            3. Click **Generate new record** → selector prefix: `google` → key size: **2048 bit**
            4. Copy the TXT record value (it looks like `v=DKIM1; k=rsa; p=MIIBIjANBg…`)
            5. Add/update the DNS TXT record:
               - Host: `google._domainkey.{your-domain}`
               - Value: paste the full string from step 4
            6. Back in Google Admin Console, click **Start authentication**
            7. Wait for Google to verify (usually <10 minutes after DNS propagates)
            8. Status should show "Email authenticated" ✅

            > ⚠️  Do **not** start sending email from any inbox until DKIM shows
            > "Email authenticated". Sending without DKIM is the #1 spam trigger.

            ---

            ## Step 5 — Connect each inbox via Gmail OAuth in SolarLead

            Once Google Workspace is configured and DKIM is verified:

            1. Open SolarLead Dashboard → **Settings → Inboxes**
            2. For each of the 12 mailboxes, click **"+ Add inbox"** then
               **"Connect Gmail"**
            3. Authenticate with the mailbox credentials (e.g. `luca.ferrari@solarlead-progetti.it`)
            4. Grant the `https://www.googleapis.com/auth/gmail.send` scope
            5. SolarLead stores the refresh token encrypted; the inbox status
               should switch to **"Gmail OAuth ✅"**

            Alternatively, enroll via the CLI (runs `smartlead_service.enroll_all_from_topology()`):

            ```bash
            python -m src.scripts.shadow_domain_setup --enroll
            ```

            This reads `shadow_domains_topology.json` and calls the Smartlead API to
            create and warm-up all 12 inboxes automatically.

            ---

            ## Step 6 — Enroll in Smartlead warm-up

            Once all inboxes are OAuth-connected in SolarLead:

            ```bash
            # Enroll all inboxes in Smartlead warm-up (requires SMARTLEAD_API_KEY in .env)
            python -m src.services.smartlead_service enroll-all
            ```

            Expected output:
            ```
            ✅  luca.ferrari@solarlead-progetti.it → enrolled (id=12345)
            ✅  giulia.romano@solarlead-progetti.it → enrolled (id=12346)
            … (12 lines total)
            ```

            Warm-up schedule (per inbox):
            | Days  | Warmup emails/day | Live outreach cap |
            |-------|-------------------|-------------------|
            | 1–7   | 10                | 10                |
            | 8–14  | 25                | 25                |
            | 15–21 | 40                | 40                |
            | 22+   | 40 (maintenance)  | **50** (steady state) |

            Total day-1 capacity: **120 emails / day** across all 12 inboxes.
            Total steady-state capacity: **600 emails / day** across all 12 inboxes.
            Target SLA ("250 in-target / day"): reached on **day 8** of warm-up.

            ---

            ## Step 7 — Monitor DMARC reports

            After 24 hours of sending, check `dmarc@{BRAND_DOMAIN}` for DMARC
            aggregate reports (`.xml.gz` attachments from Google, Yahoo, Microsoft).

            Key metrics to watch (first 14 days):
            - `dkim=pass` rate should be **100%**
            - `spf=pass` rate should be **100%**
            - `disposition=none` for all rows (p=none mode = report only, no quarantine)

            After 14 days of clean reports, upgrade DMARC policy to `p=quarantine`:
            ```
            v=DMARC1; p=quarantine; rua=mailto:{DMARC_RUA}; pct=100; adkim=s; aspf=s
            ```

            After 30 more days of clean reports, upgrade to `p=reject`.

            ---

            ## Step 8 — First live send

            Before the first live send:

            - [ ] All 4 domains show DKIM "Email authenticated" in Google Admin
            - [ ] All DNS records verified green in SolarLead dashboard
            - [ ] All 12 inboxes connected via OAuth in SolarLead
            - [ ] All 12 inboxes enrolled in Smartlead warm-up (status: active)
            - [ ] `APP_SECRET_KEY` configured in API `.env`
            - [ ] At least 3 warm-up days completed (inbox health > 80% in Smartlead)

            Then flip the first tenant to V2 pipeline:
            ```sql
            UPDATE tenants SET pipeline_version = 2 WHERE id = '<tenant-uuid>';
            ```

            Monitor the first 50 sends in Sentry + dashboard `/invii` before
            scaling up.

            ---

            ## Troubleshooting

            | Symptom | Likely cause | Fix |
            |---------|-------------|-----|
            | Gmail → Spam on day 1 | DKIM not verified yet | Verify DKIM first |
            | High bounce rate (>5%) | Bad prospect list | Clean list; pause domain |
            | Smartlead enrollment fails | Wrong SMTP password | Re-check App Password |
            | `InvalidToken` on unsubscribe | `APP_SECRET_KEY` not set | Set env var |
            | Domain paused automatically | Bounce/complaint threshold | Check `domain_reputation` table |

            ---

            *Generated by `src/scripts/shadow_domain_setup.py` — edit `SHADOW_DOMAINS` in that file to update.*
            """,
        )
    )

    return "\n".join(guide_parts)


# ---------------------------------------------------------------------------
# DNS-only printer (compact, for registrar copy-paste)
# ---------------------------------------------------------------------------

def print_dns_tables() -> None:
    for sd in SHADOW_DOMAINS:
        print(f"\n{'=' * 72}")
        print(f"  {sd.domain}")
        print(f"  {sd.purpose}")
        print(f"{'=' * 72}\n")

        widths = [6, 42, 8, 50]
        headers = ["TYPE", "HOST", "PRIO", "VALUE / DATA"]
        print(_table_header(headers, widths))

        all_recs = all_records_for(sd)
        for rec in all_recs:
            t = rec["type"]
            host = rec["host"]
            prio = str(rec.get("priority", "-"))
            value = rec["value"]
            # Truncate long values for table display
            if len(value) > 48:
                value = value[:45] + "…"
            print(_table_row([t, host, prio, value], widths))
        print(_table_footer(widths))
        if note := rec.get("note"):
            print(f"  ℹ  {note}")
        print()


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="SolarLead shadow domain DNS + provisioning guide generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            examples:
              python -m src.scripts.shadow_domain_setup
              python -m src.scripts.shadow_domain_setup --format dns-only
              python -m src.scripts.shadow_domain_setup --save --output-dir ./infra
              python -m src.scripts.shadow_domain_setup --format json-only --save
            """
        ),
    )
    p.add_argument(
        "--format",
        choices=["full", "dns-only", "json-only", "guide-only"],
        default="full",
        help="What to output (default: full)",
    )
    p.add_argument(
        "--save",
        action="store_true",
        help="Write output files to --output-dir instead of stdout",
    )
    p.add_argument(
        "--output-dir",
        default=".",
        metavar="DIR",
        help="Directory for output files when --save is used (default: .)",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    fmt: str = args.format
    save: bool = args.save
    out_dir: str = args.output_dir

    if save:
        os.makedirs(out_dir, exist_ok=True)

    # --- JSON topology ---
    if fmt in ("full", "json-only"):
        topology = build_topology_json()
        json_str = json.dumps(topology, indent=2, ensure_ascii=False)
        if save:
            path = os.path.join(out_dir, "shadow_domains_topology.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json_str + "\n")
            print(f"✅  Written: {path}", file=sys.stderr)
        else:
            if fmt == "json-only":
                print(json_str)
                return

    # --- DNS tables ---
    if fmt in ("full", "dns-only"):
        if save:
            # Write one file per domain for easy hand-off to DNS admin
            for sd in SHADOW_DOMAINS:
                path = os.path.join(out_dir, f"dns_{sd.domain.replace('.', '_')}.txt")
                lines = [
                    f"DNS records for {sd.domain}",
                    f"Generated: {date.today().isoformat()}",
                    "=" * 72,
                ]
                for rec in all_records_for(sd):
                    lines.append(
                        f"{rec['type']:6}  {rec['host']:<50}  "
                        f"TTL={rec['ttl']}  "
                        f"{rec.get('priority', '')!s:4}  "
                        f"{rec['value']}"
                    )
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(lines) + "\n")
                print(f"✅  Written: {path}", file=sys.stderr)
            if fmt == "dns-only":
                return
        else:
            print_dns_tables()
            if fmt == "dns-only":
                return

    # --- Markdown guide ---
    if fmt in ("full", "guide-only"):
        guide = build_markdown_guide()
        if save:
            path = os.path.join(out_dir, "shadow_domain_provisioning_guide.md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(guide)
            print(f"✅  Written: {path}", file=sys.stderr)
        else:
            print(guide)

    if save:
        print(
            f"\n📦  All files written to '{out_dir}'. "
            "Share shadow_domain_provisioning_guide.md with ops team.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
