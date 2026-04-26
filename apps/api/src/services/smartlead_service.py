"""Task 14 — Smartlead.ai warm-up management service.

Responsibilities
----------------
1. **Enroll** a new Google Workspace inbox in Smartlead's warm-up pool,
   configuring the ramp-up curve that matches SolarLead's 21-day schedule
   (10 → 25 → 40 → 50/day).

2. **Query** current warm-up stats per inbox (emails sent, received, reply
   rate, health score) from the Smartlead API.

3. **Sync** warmup state back to ``tenant_inboxes`` so ``inbox_service.
   pick_and_claim`` knows the actual effective daily cap at any given point
   in the ramp-up.  Sync runs as a daily cron (see cron_service.py).

4. **List** all Smartlead email accounts for a given API key — used to
   reconcile local DB state with Smartlead's canonical list on startup.

5. **CLI entry-point** (``python -m src.services.smartlead_service enroll-all``)
   reads ``shadow_domains_topology.json`` produced by Task 13 and bulk-enrolls
   all 12 inboxes automatically.

Smartlead API
-------------
Base URL: ``https://server.smartlead.ai/api/v1``
Auth:     ``?api_key={key}`` query param on every request
Rate limit: 30 req/min (HTTP 429 → retry with 60 s back-off)
Docs:     https://api.smartlead.ai/reference

Key endpoints used
------------------
``POST /email-accounts/save``           — create or update an email account
``GET  /email-accounts``                — list all accounts
``GET  /email-accounts/{id}``           — get one account + warmup settings
``POST /email-accounts/{id}/update-warmup-settings`` — update warmup config
``GET  /email-accounts/{id}/warmup-stats``           — daily stats (7/30d)

Error handling
--------------
All HTTP errors are raised as ``SmartleadError`` (or the sub-class
``SmartleadRateLimited`` for 429).  Callers that are in the warm-up sync
path should catch and log — a transient Smartlead outage must not halt
the SolarLead pipeline.

SMTP password note
------------------
For Gmail OAuth inboxes, the SMTP password registered in Smartlead is a
Google App Password (not the Workspace account password).  The operator
must generate one per mailbox in Google Account → Security → App Passwords.
This script expects the password to be in the topology JSON ``smtp_password``
field, which is populated interactively or via the CLI flag ``--smtp-passwords``
(a JSON mapping ``email → password``).  Passwords are never written to git.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import httpx
import structlog

from ..core.config import settings

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SMARTLEAD_BASE_URL = "https://server.smartlead.ai/api/v1"

# Smartlead warmup defaults that match the Sprint 6.3 curve (Task 13).
# We set a generous target here; Smartlead ramps up automatically based on
# inbox health, so the hard per-day cap comes from rate_limit_service,
# not from Smartlead's own warmup scheduler.
DEFAULT_WARMUP_TARGET_PER_DAY = 40     # max warmup emails per day
DEFAULT_WARMUP_DAILY_RAMPUP = 2        # daily increment
DEFAULT_WARMUP_REPLY_RATE_PCT = 30     # % of warmup emails that should get a reply

# HTTP client config
_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_MAX_RETRIES = 3
_RETRY_BACKOFF_S = 60  # back-off on 429

# Smartlead account statuses we consider "active warm-up"
_WARMUP_ACTIVE_STATUSES = {"active", "warming_up", "warmed"}


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class SmartleadError(Exception):
    """Base error for Smartlead API failures."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SmartleadRateLimited(SmartleadError):
    """Raised when Smartlead returns HTTP 429."""


class SmartleadAccountExists(SmartleadError):
    """Raised when trying to create an account that already exists."""


# ---------------------------------------------------------------------------
# Response dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SmartleadAccount:
    """Minimal representation of a Smartlead email account."""

    id: int
    email: str
    from_name: str
    smtp_host: str
    smtp_port: int
    warmup_enabled: bool
    warmup_target_per_day: int
    warmup_daily_rampup: int
    warmup_reply_rate_pct: int
    # These come from the warmup-stats endpoint
    warmup_emails_sent_today: int = 0
    warmup_reply_rate_actual: float = 0.0
    health_score: float | None = None   # 0-100; None if not yet computed
    raw: dict = field(default_factory=dict)


@dataclass
class WarmupStats:
    """Daily warmup statistics for one inbox."""

    account_id: int
    email: str
    date: str          # ISO date of this stat snapshot
    sent_count: int    # warmup emails sent on this date
    received_count: int
    reply_count: int
    reply_rate_pct: float
    health_score: float | None


# ---------------------------------------------------------------------------
# Internal HTTP helpers
# ---------------------------------------------------------------------------


def _api_key() -> str:
    key = (settings.smartlead_api_key or "").strip()
    if not key:
        raise SmartleadError(
            "SMARTLEAD_API_KEY is not set. Add it to .env before calling Smartlead APIs."
        )
    return key


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=SMARTLEAD_BASE_URL,
        timeout=_HTTP_TIMEOUT,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )


async def _request(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
) -> dict | list:
    """Make one authenticated request with retry on 429."""
    key = _api_key()
    qs = {"api_key": key, **(params or {})}

    for attempt in range(1, _MAX_RETRIES + 1):
        async with _client() as client:
            resp = await client.request(
                method,
                path,
                params=qs,
                json=json_body,
            )
        if resp.status_code == 429:
            if attempt < _MAX_RETRIES:
                log.warning(
                    "smartlead.rate_limited",
                    attempt=attempt,
                    backoff_s=_RETRY_BACKOFF_S,
                    path=path,
                )
                await asyncio.sleep(_RETRY_BACKOFF_S)
                continue
            raise SmartleadRateLimited(
                f"Smartlead rate limit exceeded on {path}",
                status_code=429,
            )
        if not resp.is_success:
            body_text = resp.text[:400]
            log.error(
                "smartlead.http_error",
                status=resp.status_code,
                path=path,
                body=body_text,
            )
            raise SmartleadError(
                f"Smartlead {method} {path} → HTTP {resp.status_code}: {body_text}",
                status_code=resp.status_code,
            )
        try:
            return resp.json()
        except Exception:  # noqa: BLE001
            return {}
    # Unreachable — the loop always returns or raises.
    raise SmartleadError(f"Max retries exceeded for {path}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def list_accounts() -> list[SmartleadAccount]:
    """Return all email accounts registered in Smartlead for this API key."""
    data = await _request("GET", "/email-accounts")
    accounts = []
    for row in data if isinstance(data, list) else (data.get("data") or []):
        accounts.append(_parse_account(row))
    return accounts


async def get_account(account_id: int) -> SmartleadAccount:
    """Fetch a single Smartlead account by numeric ID."""
    data = await _request("GET", f"/email-accounts/{account_id}")
    return _parse_account(data)


async def enroll_inbox(
    *,
    email: str,
    display_name: str,
    smtp_host: str,
    smtp_port: int,
    smtp_password: str,
    imap_host: str,
    imap_port: int,
    warmup_target_per_day: int = DEFAULT_WARMUP_TARGET_PER_DAY,
    warmup_daily_rampup: int = DEFAULT_WARMUP_DAILY_RAMPUP,
    warmup_reply_rate_pct: int = DEFAULT_WARMUP_REPLY_RATE_PCT,
) -> SmartleadAccount:
    """Create (or update) a Smartlead email account and enable warm-up.

    If an account with the same ``email`` already exists, its warm-up
    settings are updated to match the provided parameters.

    Returns the resulting ``SmartleadAccount``.
    """
    # Check if already enrolled
    existing = await _find_account_by_email(email)

    if existing is None:
        # Create new account
        payload: dict[str, Any] = {
            "from_name": display_name,
            "from_email": email,
            "user_name": email,
            "password": smtp_password,
            "smtp_host": smtp_host,
            "smtp_port": smtp_port,
            "imap_host": imap_host,
            "imap_port": imap_port,
            "max_email_per_day": warmup_target_per_day,
            "warmup_enabled": True,
            "total_warmup_per_day": warmup_target_per_day,
            "daily_rampup": warmup_daily_rampup,
            "reply_rate_percentage": warmup_reply_rate_pct,
        }
        result = await _request("POST", "/email-accounts/save", json_body=payload)
        account_id: int = int(
            result.get("id") or result.get("email_account_id") or 0
        )
        if not account_id:
            raise SmartleadError(f"Enroll did not return account ID for {email}. Response: {result}")
        log.info("smartlead.enrolled", email=email, account_id=account_id)
    else:
        account_id = existing.id
        log.info("smartlead.already_enrolled", email=email, account_id=account_id)

    # Ensure warm-up settings are correct (idempotent update)
    warmup_payload: dict[str, Any] = {
        "warmup_enabled": True,
        "total_warmup_per_day": warmup_target_per_day,
        "daily_rampup": warmup_daily_rampup,
        "reply_rate_percentage": warmup_reply_rate_pct,
    }
    await _request(
        "POST",
        f"/email-accounts/{account_id}/update-warmup-settings",
        json_body=warmup_payload,
    )
    log.info("smartlead.warmup_configured", email=email, account_id=account_id, **warmup_payload)

    return await get_account(account_id)


async def get_warmup_stats(account_id: int, days: int = 7) -> list[WarmupStats]:
    """Return daily warm-up stats for the last ``days`` days."""
    data = await _request(
        "GET",
        f"/email-accounts/{account_id}/warmup-stats",
        params={"limit": days},
    )
    rows = data if isinstance(data, list) else (data.get("data") or [])
    stats = []
    for row in rows:
        stats.append(
            WarmupStats(
                account_id=account_id,
                email=row.get("from_email", ""),
                date=row.get("date") or date.today().isoformat(),
                sent_count=int(row.get("sent_count") or 0),
                received_count=int(row.get("received_count") or 0),
                reply_count=int(row.get("reply_count") or 0),
                reply_rate_pct=float(row.get("reply_rate") or 0.0),
                health_score=_safe_float(row.get("health_score")),
            )
        )
    return stats


async def sync_warmup_to_db(
    *,
    tenant_id: str,
    sb: Any,
    email_to_smartlead_id: dict[str, int],
) -> dict[str, Any]:
    """Sync Smartlead warm-up stats into ``tenant_inboxes``.

    For every inbox in ``email_to_smartlead_id`` (mapping local email →
    Smartlead account numeric ID):
      1. Fetch today's warmup stats from Smartlead.
      2. Compute the effective daily cap from the warmup phase.
      3. Update ``tenant_inboxes.warmup_started_at`` if not already set
         (first successful warmup day = day 1).
      4. Log a summary of the sync.

    Called by the daily cron at 06:00 Europe/Rome (before the pipeline
    run starts) so ``inbox_service.pick_and_claim`` has fresh caps.

    Returns a sync summary dict: ``{email: {synced: bool, health: float|None, ...}}``.
    """
    summary: dict[str, Any] = {}

    for email, sl_id in email_to_smartlead_id.items():
        try:
            account = await get_account(sl_id)
            stats_list = await get_warmup_stats(sl_id, days=1)
            today_stats = stats_list[0] if stats_list else None

            health = account.health_score
            warmup_active = account.warmup_enabled

            # Compute the effective cap the inbox should have today.
            # We let SolarLead's rate_limit_service own the cap curve;
            # here we only write `warmup_started_at` if missing.
            update_payload: dict[str, Any] = {}

            if warmup_active:
                # Mark start date if not already set (first day of warm-up).
                # We need to read the current row first.
                res = await asyncio.to_thread(
                    lambda: sb.table("tenant_inboxes")
                    .select("id, warmup_started_at")
                    .eq("email", email)
                    .eq("tenant_id", tenant_id)
                    .limit(1)
                    .execute()
                )
                if res.data and res.data[0].get("warmup_started_at") is None:
                    update_payload["warmup_started_at"] = datetime.now(
                        tz=timezone.utc
                    ).isoformat()

            # Persist health score in a JSON metadata blob if the column exists.
            # Gracefully skipped if the column doesn't exist yet.
            if health is not None:
                update_payload["smartlead_health_score"] = health

            if update_payload:
                await asyncio.to_thread(
                    lambda: sb.table("tenant_inboxes")
                    .update(update_payload)
                    .eq("email", email)
                    .eq("tenant_id", tenant_id)
                    .execute()
                )

            summary[email] = {
                "synced": True,
                "smartlead_id": sl_id,
                "health": health,
                "warmup_active": warmup_active,
                "today_sent": today_stats.sent_count if today_stats else None,
                "today_replied": today_stats.reply_count if today_stats else None,
            }
            log.info(
                "smartlead.sync_ok",
                email=email,
                health=health,
                warmup_active=warmup_active,
            )
        except SmartleadRateLimited:
            # Don't crash the entire sync on rate limit — skip and try tomorrow.
            log.warning("smartlead.sync_rate_limited", email=email, sl_id=sl_id)
            summary[email] = {"synced": False, "error": "rate_limited"}
        except Exception as exc:  # noqa: BLE001
            log.error("smartlead.sync_error", email=email, sl_id=sl_id, err=str(exc))
            summary[email] = {"synced": False, "error": str(exc)}

    return summary


async def get_all_smartlead_ids_for_tenant(
    *,
    tenant_id: str,
    sb: Any,
) -> dict[str, int]:
    """Return a mapping of ``email → smartlead_id`` for all active outreach inboxes.

    Reads ``tenant_inboxes.smartlead_account_id`` (populated during enrollment).
    Falls back to looking up by email in the Smartlead API if ``smartlead_account_id``
    is null (handles inboxes enrolled before this column was added).
    """
    res = await asyncio.to_thread(
        lambda: sb.table("tenant_inboxes")
        .select("email, smartlead_account_id")
        .eq("tenant_id", tenant_id)
        .eq("active", True)
        .eq("provider", "gmail_oauth")
        .execute()
    )
    if not res.data:
        return {}

    mapping: dict[str, int] = {}
    need_lookup: list[str] = []

    for row in res.data:
        email = row.get("email")
        sl_id = row.get("smartlead_account_id")
        if email and sl_id:
            mapping[email] = int(sl_id)
        elif email:
            need_lookup.append(email)

    if need_lookup:
        # Bulk-fetch from Smartlead to fill in missing IDs.
        try:
            all_accs = await list_accounts()
            email_to_id = {acc.email: acc.id for acc in all_accs}
            for email in need_lookup:
                if sl_id := email_to_id.get(email):
                    mapping[email] = sl_id
                    # Back-fill the DB column for next time.
                    await asyncio.to_thread(
                        lambda: sb.table("tenant_inboxes")
                        .update({"smartlead_account_id": sl_id})
                        .eq("email", email)
                        .eq("tenant_id", tenant_id)
                        .execute()
                    )
        except SmartleadError as exc:
            log.warning(
                "smartlead.bulk_lookup_failed",
                tenant_id=tenant_id,
                err=str(exc),
            )

    return mapping


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_account(row: dict) -> SmartleadAccount:
    warmup = row.get("warmup_details") or {}
    return SmartleadAccount(
        id=int(row.get("id") or 0),
        email=row.get("from_email") or row.get("email") or "",
        from_name=row.get("from_name") or "",
        smtp_host=row.get("smtp_host") or "",
        smtp_port=int(row.get("smtp_port") or 587),
        warmup_enabled=bool(row.get("warmup_enabled") or warmup.get("warmup_enabled")),
        warmup_target_per_day=int(
            row.get("total_warmup_per_day")
            or warmup.get("total_warmup_per_day")
            or DEFAULT_WARMUP_TARGET_PER_DAY
        ),
        warmup_daily_rampup=int(
            row.get("daily_rampup") or warmup.get("daily_rampup") or DEFAULT_WARMUP_DAILY_RAMPUP
        ),
        warmup_reply_rate_pct=int(
            row.get("reply_rate_percentage")
            or warmup.get("reply_rate_percentage")
            or DEFAULT_WARMUP_REPLY_RATE_PCT
        ),
        health_score=_safe_float(row.get("health_score")),
        raw=row,
    )


async def _find_account_by_email(email: str) -> SmartleadAccount | None:
    """Return the Smartlead account matching ``email``, or None if not found."""
    try:
        accounts = await list_accounts()
    except SmartleadError:
        return None
    for acc in accounts:
        if acc.email.lower() == email.lower():
            return acc
    return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Topology-based bulk enrollment (reads Task 13 JSON output)
# ---------------------------------------------------------------------------


async def enroll_all_from_topology(
    topology_path: str,
    smtp_passwords: dict[str, str],
    *,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Bulk-enroll all inboxes defined in ``shadow_domains_topology.json``.

    ``smtp_passwords`` — mapping ``email → google_app_password``. Must be
    provided by the operator (never stored in the topology file).

    ``dry_run=True`` — print what would be enrolled without calling Smartlead.

    Returns a list of result dicts, one per inbox.
    """
    with open(topology_path, encoding="utf-8") as fh:
        topology = json.load(fh)

    results: list[dict[str, Any]] = []

    for domain_cfg in topology.get("shadow_domains", []):
        for inbox_cfg in domain_cfg.get("inboxes", []):
            email: str = inbox_cfg["email"]
            password = smtp_passwords.get(email)

            if not password:
                results.append(
                    {
                        "email": email,
                        "status": "skipped",
                        "reason": "no smtp_password provided",
                    }
                )
                if dry_run:
                    print(f"⏭  {email}  (no password — skipped)")
                continue

            if dry_run:
                print(
                    f"[dry-run] Would enroll {email} as '{inbox_cfg['display_name']}' "
                    f"in Smartlead warmup (target={inbox_cfg['warmup_target_per_day']}/day)"
                )
                results.append({"email": email, "status": "dry_run"})
                continue

            try:
                account = await enroll_inbox(
                    email=email,
                    display_name=inbox_cfg["display_name"],
                    smtp_host=inbox_cfg["smtp_host"],
                    smtp_port=inbox_cfg["smtp_port"],
                    smtp_password=password,
                    imap_host=inbox_cfg["imap_host"],
                    imap_port=inbox_cfg["imap_port"],
                    warmup_target_per_day=inbox_cfg.get(
                        "warmup_target_per_day", DEFAULT_WARMUP_TARGET_PER_DAY
                    ),
                    warmup_daily_rampup=inbox_cfg.get(
                        "warmup_daily_rampup", DEFAULT_WARMUP_DAILY_RAMPUP
                    ),
                    warmup_reply_rate_pct=inbox_cfg.get(
                        "warmup_reply_rate_pct", DEFAULT_WARMUP_REPLY_RATE_PCT
                    ),
                )
                results.append(
                    {
                        "email": email,
                        "status": "enrolled",
                        "smartlead_id": account.id,
                        "warmup_enabled": account.warmup_enabled,
                    }
                )
                print(f"✅  {email} → enrolled (smartlead_id={account.id})")
            except SmartleadRateLimited:
                results.append({"email": email, "status": "failed", "reason": "rate_limited"})
                print(f"⏳  {email} → rate limited (retry later)", file=sys.stderr)
                # Sleep so we don't hammer the 30 req/min limit during bulk enroll
                await asyncio.sleep(2)
            except SmartleadError as exc:
                results.append({"email": email, "status": "failed", "reason": str(exc)})
                print(f"❌  {email} → {exc}", file=sys.stderr)

    return results


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def _cli_main() -> None:
    """Minimal CLI for bulk operations.

    Usage::

        # Dry-run: show what would be enrolled (no API calls)
        python -m src.services.smartlead_service enroll-all --dry-run

        # Enroll, providing passwords inline (JSON string)
        python -m src.services.smartlead_service enroll-all \
            --topology ./infra/shadow_domains_topology.json \
            --passwords '{"luca.ferrari@solarlead-progetti.it": "app-password-here", ...}'

        # Enroll, reading passwords from a JSON file
        python -m src.services.smartlead_service enroll-all \
            --topology ./infra/shadow_domains_topology.json \
            --passwords-file ./infra/smtp_passwords.json

        # List all currently enrolled accounts
        python -m src.services.smartlead_service list-accounts
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="smartlead_service",
        description="Smartlead.ai inbox management CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # enroll-all
    enroll_p = sub.add_parser("enroll-all", help="Bulk-enroll inboxes from topology JSON")
    enroll_p.add_argument(
        "--topology",
        default="shadow_domains_topology.json",
        help="Path to shadow_domains_topology.json (default: ./shadow_domains_topology.json)",
    )
    enroll_p.add_argument(
        "--passwords",
        default=None,
        help="JSON string mapping email → smtp_password",
    )
    enroll_p.add_argument(
        "--passwords-file",
        default=None,
        metavar="FILE",
        help="JSON file mapping email → smtp_password",
    )
    enroll_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be enrolled without making API calls",
    )

    # list-accounts
    sub.add_parser("list-accounts", help="List all Smartlead accounts for this API key")

    args = parser.parse_args()

    async def _run() -> None:
        if args.command == "list-accounts":
            accounts = await list_accounts()
            for acc in accounts:
                warmup_icon = "🔥" if acc.warmup_enabled else "❄️"
                health_str = f"  health={acc.health_score:.0f}%" if acc.health_score else ""
                print(
                    f"{warmup_icon}  [{acc.id:6}]  {acc.email:<45}  "
                    f"target={acc.warmup_target_per_day}/day{health_str}"
                )
            print(f"\n{len(accounts)} account(s) total.")

        elif args.command == "enroll-all":
            passwords: dict[str, str] = {}
            if args.passwords:
                passwords = json.loads(args.passwords)
            elif args.passwords_file:
                with open(args.passwords_file, encoding="utf-8") as fh:
                    passwords = json.load(fh)
            elif not args.dry_run:
                print(
                    "❌  No passwords provided. Use --passwords, --passwords-file, "
                    "or --dry-run.",
                    file=sys.stderr,
                )
                sys.exit(1)

            results = await enroll_all_from_topology(
                args.topology,
                passwords,
                dry_run=args.dry_run,
            )
            enrolled = sum(1 for r in results if r["status"] == "enrolled")
            skipped = sum(1 for r in results if r["status"] == "skipped")
            failed = sum(1 for r in results if r["status"] == "failed")
            print(
                f"\n{'dry-run: ' if args.dry_run else ''}"
                f"enrolled={enrolled}  skipped={skipped}  failed={failed}"
            )

    asyncio.run(_run())


if __name__ == "__main__":
    _cli_main()
