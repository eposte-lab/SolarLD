"""Task 20 — Runtime pre-flight check.

Runs immediately before ``provider.send()`` in OutreachAgent as a final
safety layer. Its purpose is to catch state that changed BETWEEN the time
we loaded the lead (beginning of ``execute()``) and now — e.g.:

* Another worker processed a complaint for this address → pii_hash or
  email_hash is now in the blacklist.
* A concurrent send from the same inbox caused the inbox to be
  auto-paused (provider 429 / 5xx).
* The hourly deliverability monitor detected a bounce/complaint spike and
  paused the domain mid-batch.
* The compliance agent finished processing an earlier complaint and added
  the subject to ``global_blacklist``.

The preflight is a thin, read-only pass — it performs no state mutations.
All checks are run concurrently via ``asyncio.gather`` to keep total latency
under 50 ms on a warm Supabase connection pool.

Fail-open policy
----------------
Any individual check that raises a Supabase exception is silently skipped
(the PASS result is used). A transient DB outage should never silently
swallow a send — it is far worse to stop all outreach for 30 minutes
because Supabase is slow than to let one borderline send through.

The upstream compliance + tracking + hourly-monitor pipeline provides
redundant catch-up coverage for anything this gate misses.

Usage in OutreachAgent::

    from ..services.runtime_preflight import check_preflight

    preflight = await check_preflight(
        sb,
        recipient_email=recipient,
        pii_hash=subject.get("pii_hash"),
        inbox_id=inbox_id,
        domain_id=domain_id,
        tenant_id=payload.tenant_id,
    )
    if not preflight.ok:
        return await self._record_skip(
            payload=payload,
            lead=lead,
            reason=f"preflight_{preflight.reason}",
        )
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class PreflightResult:
    """Outcome of one complete pre-flight check run."""

    ok: bool
    # Non-empty only when ok=False — the specific reason for the block.
    reason: str = ""
    # Which gate tripped: "blacklist" | "email_blacklist" | "inbox" | "domain"
    gate: str = ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def check_preflight(
    sb: Any,
    *,
    recipient_email: str,
    pii_hash: str | None,
    inbox_id: str | None,
    domain_id: str | None,
    tenant_id: str,
) -> PreflightResult:
    """Run all pre-flight safety checks concurrently.

    Args:
        sb:              Supabase service-role client (sync, wrapped in
                         ``asyncio.to_thread`` by each sub-check).
        recipient_email: The final resolved recipient address.
        pii_hash:        Subject's PII hash (SHA-256 hex), or None.
        inbox_id:        ID of the claimed inbox row, or None for legacy path.
        domain_id:       ID of the sending domain row, or None if unavailable.
        tenant_id:       Tenant performing the send (for email_blacklist scope).

    Returns:
        ``PreflightResult(ok=True)`` when all checks pass.
        ``PreflightResult(ok=False, reason=..., gate=...)`` on the first
        failed check (in ``gather`` order: blacklist → email → inbox → domain).
    """
    checks = await asyncio.gather(
        _check_global_blacklist(sb, pii_hash=pii_hash),
        _check_email_blacklist(sb, email=recipient_email, tenant_id=tenant_id),
        _check_inbox_health(sb, inbox_id=inbox_id),
        _check_domain_health(sb, domain_id=domain_id),
        return_exceptions=True,
    )

    gate_labels = ("blacklist", "email_blacklist", "inbox", "domain")
    for label, result in zip(gate_labels, checks):
        if isinstance(result, Exception):
            # Fail-open: log the error but let the send proceed.
            log.debug(
                "preflight.check_exception",
                gate=label,
                tenant_id=tenant_id,
                err=str(result),
            )
            continue
        # result is a PreflightResult at this point.
        if not result.ok:  # type: ignore[union-attr]
            log.info(
                "preflight.blocked",
                gate=label,
                reason=result.reason,  # type: ignore[union-attr]
                tenant_id=tenant_id,
                inbox_id=inbox_id,
                recipient=_mask_email(recipient_email),
            )
            return result  # type: ignore[return-value]

    return PreflightResult(ok=True)


# ---------------------------------------------------------------------------
# Individual gate checks (all async, all fail-open on exceptions)
# ---------------------------------------------------------------------------


async def _check_global_blacklist(
    sb: Any,
    *,
    pii_hash: str | None,
) -> PreflightResult:
    """Verify the subject's pii_hash is NOT in global_blacklist."""
    if not pii_hash:
        return PreflightResult(ok=True)

    try:
        res = await asyncio.to_thread(
            lambda: sb.table("global_blacklist")
            .select("id")
            .eq("pii_hash", pii_hash)
            .limit(1)
            .execute()
        )
        if res.data:
            return PreflightResult(
                ok=False,
                reason="pii_hash_blacklisted",
                gate="blacklist",
            )
    except Exception as exc:  # noqa: BLE001
        log.debug("preflight.global_blacklist_error", err=str(exc))

    return PreflightResult(ok=True)


async def _check_email_blacklist(
    sb: Any,
    *,
    email: str,
    tenant_id: str,
) -> PreflightResult:
    """Verify the recipient address is NOT in email_blacklist.

    ``email_blacklist`` (migration 0057) is indexed on ``(email_hash,
    tenant_id)`` for a near-instant lookup. The hash uses SHA-256 of the
    lowercased email, matching the format written by the unsubscribe handler
    and the complaint webhook.
    """
    if not email:
        return PreflightResult(ok=True)

    email_hash = hashlib.sha256(email.strip().lower().encode()).hexdigest()

    try:
        res = await asyncio.to_thread(
            lambda: sb.table("email_blacklist")
            .select("reason")
            .eq("email_hash", email_hash)
            .eq("tenant_id", tenant_id)
            .limit(1)
            .execute()
        )
        if res.data:
            reason = (res.data[0].get("reason") or "email_blacklisted")
            return PreflightResult(
                ok=False,
                reason=reason,
                gate="email_blacklist",
            )
    except Exception as exc:  # noqa: BLE001
        log.debug("preflight.email_blacklist_error", err=str(exc))

    return PreflightResult(ok=True)


async def _check_inbox_health(
    sb: Any,
    *,
    inbox_id: str | None,
) -> PreflightResult:
    """Verify the claimed inbox is still active and not paused.

    Inboxes are paused atomically by ``inbox_service.pause_inbox`` when
    provider errors occur.  There is a small window between the time
    ``pick_and_claim`` selects an inbox and the time ``provider.send()``
    fires; a concurrent worker running in the same second might have used
    up the last slot and triggered a 429 pause.  This check catches it.
    """
    if not inbox_id:
        return PreflightResult(ok=True)  # Legacy path: no inbox to verify.

    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        res = await asyncio.to_thread(
            lambda: sb.table("tenant_inboxes")
            .select("id, active, paused_until")
            .eq("id", inbox_id)
            .limit(1)
            .execute()
        )
        row = (res.data or [None])[0]
        if not row:
            return PreflightResult(ok=True)  # Row disappeared → fail-open.

        if not row.get("active"):
            return PreflightResult(
                ok=False,
                reason="inbox_deactivated",
                gate="inbox",
            )

        paused_until = row.get("paused_until")
        if paused_until and str(paused_until) > now_iso:
            return PreflightResult(
                ok=False,
                reason="inbox_paused",
                gate="inbox",
            )
    except Exception as exc:  # noqa: BLE001
        log.debug(
            "preflight.inbox_health_error",
            inbox_id=inbox_id,
            err=str(exc),
        )

    return PreflightResult(ok=True)


async def _check_domain_health(
    sb: Any,
    *,
    domain_id: str | None,
) -> PreflightResult:
    """Verify the sending domain is not paused.

    The hourly deliverability monitor and the real-time complaint/bounce
    handlers may pause a domain between the time this arq worker job was
    dequeued and now.  A single DB read is sufficient to confirm the domain
    is still live.
    """
    if not domain_id:
        return PreflightResult(ok=True)

    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        res = await asyncio.to_thread(
            lambda: sb.table("tenant_email_domains")
            .select("id, paused_until, pause_reason")
            .eq("id", domain_id)
            .limit(1)
            .execute()
        )
        row = (res.data or [None])[0]
        if not row:
            return PreflightResult(ok=True)

        paused_until = row.get("paused_until")
        if paused_until and str(paused_until) > now_iso:
            pause_reason = row.get("pause_reason") or "domain_paused"
            return PreflightResult(
                ok=False,
                reason=pause_reason,
                gate="domain",
            )
    except Exception as exc:  # noqa: BLE001
        log.debug(
            "preflight.domain_health_error",
            domain_id=domain_id,
            err=str(exc),
        )

    return PreflightResult(ok=True)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _mask_email(email: str) -> str:
    """Return ``a***@domain.tld`` for privacy-safe logging."""
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    return f"{local[:1]}***@{domain}"
