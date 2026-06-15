"""Premium decision-maker email finder.

Upgrades a WEAK contact email (a generic role inbox like ``info@`` /
``direzione@``, or an inferred ``info@<domain>``) to a NAMED decision-maker
email using Hunter.io domain-search, validated with NeverBounce, under a capped
per-tenant budget (``reserve_premium_budget`` RPC, migration 0150).

Shared by:
  - the v3 funnel automatic enrichment (future leads),
  - the on-demand "find a better contact" endpoint,
  - the batch re-enrichment of already-sent leads.

Design rules:
  - **Fails OPEN**: any miss / API error / budget-exhausted returns ``None`` and
    the caller keeps the website email — a lead is never dropped over this.
  - **Fails CLOSED on validation**: an unverified named guess is never promoted
    (NeverBounce must say sendable + not a role address).
  - **Vendor-neutral**: the provider names never reach the UI; provenance is
    persisted as ``"premium_finder"``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ..core.config import settings
from ..core.logging import get_logger
from ..core.queue import enqueue
from ..core.supabase_client import get_service_client
from .audit_service import log_action
from .hunter_io_service import HUNTER_COST_PER_CALL_CENTS, domain_search
from .neverbounce_service import NeverBounceError, verify_email
from .web_scraper import is_non_business_domain

log = get_logger(__name__)

# Fallback validation bar when NeverBounce is NOT configured: trust Hunter's
# own bundled verification (included in the Hunter plan). Accept a candidate
# only if Hunter marked it "valid" OR returns a high deliverability confidence.
# Role inboxes are already excluded before this gate.
_MIN_HUNTER_CONFIDENCE = 85

# Local-parts that are a shared ROLE inbox, not a person. A best_email whose
# local part is one of these (or that has no person-shaped local part) is
# "weak" and worth a premium lookup.
_ROLE_LOCAL_PARTS = frozenset(
    {
        "info",
        "direzione",
        "amministrazione",
        "commerciale",
        "contatti",
        "contact",
        "segreteria",
        "ufficio",
        "vendite",
        "sales",
        "marketing",
        "privacy",
        "dpo",
        "gdpr",
        "legal",
        "noreply",
        "no-reply",
        "mail",
        "posta",
    }
)


@dataclass(slots=True)
class DecisionMakerUpgrade:
    email: str
    name: str | None
    role: str | None
    confidence: str  # always "alta"
    fallback_email: str | None  # the previous (website) email, kept as backup


def _is_personal_email(email: str | None) -> bool:
    """True when ``email`` already looks like a named person (``mario.rossi@``,
    ``m.rossi@``) rather than a shared role inbox."""
    if not email or "@" not in email:
        return False
    local = email.split("@", 1)[0].lower()
    if local in _ROLE_LOCAL_PARTS:
        return False
    return "." in local or "_" in local


def is_weak_email(email: str | None) -> bool:
    """Whether ``email`` is worth a premium decision-maker lookup: missing,
    generic/inferred, or a role inbox. An already-personal email is NOT weak
    (skipped to save budget)."""
    return not _is_personal_email(email)


async def upgrade_to_decision_maker(
    *,
    company_domain: str | None,
    current_email: str | None,
    tenant_id: str,
    client: httpx.AsyncClient | None = None,
    lead_id: str | None = None,
    candidate_id: str | None = None,
) -> DecisionMakerUpgrade | None:
    """Find a NAMED, validated decision-maker email for ``company_domain``.

    Returns ``None`` (caller keeps the current email) when the current email is
    already personal, there is no usable business domain, the budget is
    exhausted, or no validated named person is found.
    """
    if not is_weak_email(current_email):
        return None
    domain = (company_domain or "").strip().lower()
    if domain.startswith("www."):
        domain = domain[4:]
    if not domain or "." not in domain or is_non_business_domain(domain):
        return None

    sb = get_service_client()

    # Atomically reserve the Hunter cost; skip if the budget is exhausted.
    try:
        reserved = sb.rpc(
            "reserve_premium_budget",
            {"p_tenant_id": tenant_id, "p_cost_cents": HUNTER_COST_PER_CALL_CENTS},
        ).execute()
        if not bool(reserved.data):
            log.info("premium_finder.budget_exhausted", tenant_id=tenant_id)
            return None
    except Exception as exc:  # noqa: BLE001 — budget RPC is a hard dependency boundary
        log.warning("premium_finder.budget_check_failed", err=type(exc).__name__)
        return None

    # Hunter domain-search for executives / management.
    try:
        candidates = await domain_search(domain, client=client)
    except Exception as exc:  # noqa: BLE001 — fail open, keep the website email
        log.warning("premium_finder.lookup_failed", domain=domain, err=type(exc).__name__)
        return None

    # Pick the best NAMED person: real first+last name, on the company domain,
    # not a role alias. candidates are already sorted by confidence desc.
    best = None
    for c in candidates:
        if not c.email or not c.first_name or not c.last_name:
            continue
        if c.email.split("@", 1)[-1].lower() != domain:
            continue
        if c.email.split("@", 1)[0].lower() in _ROLE_LOCAL_PARTS:
            continue
        best = c
        break
    if best is None or not best.email:
        return None

    # Validate the named guess — fail CLOSED: never promote an unverified
    # personal guess. Prefer NeverBounce when its key is configured (most
    # authoritative); otherwise fall back to Hunter's own bundled verification
    # (included in the Hunter plan) gated on an explicit "valid" status OR a
    # high deliverability confidence.
    if settings.neverbounce_api_key:
        try:
            verification = await verify_email(best.email, client=client)
        except NeverBounceError:
            return None
        if not verification.result.sendable or verification.role_address:
            return None
    elif not (best.verified or best.confidence_score >= _MIN_HUNTER_CONFIDENCE):
        log.info(
            "premium_finder.hunter_validation_failed",
            tenant_id=tenant_id,
            domain=domain,
            confidence=best.confidence_score,
            verified=best.verified,
        )
        return None

    name = " ".join(p for p in (best.first_name, best.last_name) if p) or None
    log.info(
        "premium_finder.upgraded",
        tenant_id=tenant_id,
        lead_id=lead_id,
        candidate_id=candidate_id,
        domain=domain,
        confidence=best.confidence_score,
    )
    return DecisionMakerUpgrade(
        email=best.email.strip().lower(),
        name=name,
        role=best.position,
        confidence="alta",
        fallback_email=current_email,
    )


async def reenrich_lead_contact(*, tenant_id: str, lead_id: str) -> dict[str, Any]:
    """On-demand / batch re-enrichment of ONE existing lead's contact.

    Derives the company domain from the subject's current email, runs the
    premium finder, and (on success) updates the subject's decision-maker
    email/name/role in place — marking it verified and provenance
    ``premium_finder``. Idempotent: a subject already on ``premium_finder`` is
    skipped (no spend). Returns a small status dict for the caller/log.
    """
    sb = get_service_client()
    lead = (
        sb.table("leads")
        .select("id, subject_id")
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .maybe_single()
        .execute()
    )
    if not lead or not lead.data:
        return {"ok": False, "reason": "lead_not_found"}
    subject_id = lead.data.get("subject_id")
    subj = (
        sb.table("subjects")
        .select("id, decision_maker_email, decision_maker_email_source")
        .eq("id", subject_id)
        .limit(1)
        .maybe_single()
        .execute()
    )
    if not subj or not subj.data:
        return {"ok": False, "reason": "subject_not_found"}

    if subj.data.get("decision_maker_email_source") == "premium_finder":
        return {"ok": True, "upgraded": False, "reason": "already_premium"}

    current_email = (subj.data.get("decision_maker_email") or "").strip().lower()
    if "@" not in current_email:
        return {"ok": True, "upgraded": False, "reason": "no_domain"}
    domain = current_email.split("@", 1)[1]

    upgrade = await upgrade_to_decision_maker(
        company_domain=domain,
        current_email=current_email,
        tenant_id=tenant_id,
        lead_id=lead_id,
    )
    if upgrade is None:
        return {"ok": True, "upgraded": False, "reason": "no_better_contact"}

    sb.table("subjects").update(
        {
            "decision_maker_email": upgrade.email,
            "decision_maker_name": upgrade.name,
            "decision_maker_role": upgrade.role,
            "decision_maker_email_source": "premium_finder",
            "decision_maker_email_fallback": upgrade.fallback_email,
            "decision_maker_email_verified": True,
        }
    ).eq("id", subject_id).execute()
    return {"ok": True, "upgraded": True}


# Terminal lead states that are no longer valid re-send targets.
_TERMINAL_LEAD_STATES = ["closed_won", "closed_lost", "blacklisted", "to_call"]


def _spread_defer(now: datetime, idx: int, per_day_cap: int) -> datetime:
    """Schedule the idx-th re-send: ``per_day_cap`` sends per day, rolled to the
    next weekday at ~10:00 Rome (≈08:00 UTC) so real prospects never get a
    night/weekend email even though ``force`` bypasses the live send-window."""
    day_offset = idx // max(1, per_day_cap)
    target = now + timedelta(days=day_offset)
    while target.weekday() >= 5:  # 5=Sat, 6=Sun
        target = target + timedelta(days=1)
    return target.replace(hour=8, minute=0, second=0, microsecond=0)


async def batch_reenrich_and_resend(
    *,
    tenant_id: str,
    limit: int = 150,
    spread_days: int = 5,  # noqa: ARG001 — kept for caller symmetry; pacing is per_day_cap
    per_day_cap: int = 30,
    dry_run: bool = True,
    actor: str | None = None,
) -> dict[str, Any]:
    """§D — batch re-enrich already-SENT leads and (optionally) re-send.

    Selects SENT, non-terminal leads that have NOT already received a follow-up,
    EXCLUDING the leads handled manually via the alt-address resend (Hilton /
    Sigma — identified from the ``lead.outreach_resent_alt_address`` audit
    trail). For each it runs the premium finder (``reenrich_lead_contact`` —
    budget-capped, idempotent, fail-open). When ``dry_run`` is False AND a lead
    was upgraded, it enqueues a re-send of the OFFICIAL outreach to the new
    address (``force`` re-send, no template change), paced at ``per_day_cap``/day
    on weekday mornings, and stamps ``last_followup_sent_at`` so re-runs never
    double-send the same lead.

    ``dry_run`` defaults to True: the first run only finds + persists better
    contacts (surfacing the premium badge) and reports counts — it never sends.
    """
    sb = get_service_client()
    capped_limit = max(1, min(limit, 500))

    # 1) Exclude leads already handled by the operator via the alt-address
    #    resend (Hilton / Sigma) — reached by phone / alternate email already.
    excluded_ids: set[str] = set()
    try:
        alt = (
            sb.table("audit_log")
            .select("target_id")
            .eq("tenant_id", tenant_id)
            .eq("action", "lead.outreach_resent_alt_address")
            .eq("target_table", "leads")
            .execute()
        )
        for r in alt.data or []:
            tid = r.get("target_id")
            if tid:
                excluded_ids.add(str(tid))
    except Exception as exc:  # noqa: BLE001 — exclusion is best-effort
        log.warning("batch_reenrich.audit_lookup_failed", err=type(exc).__name__)

    # 2) Candidate SENT leads: not terminal, not already (manually) followed-up.
    res = (
        sb.table("leads")
        .select("id")
        .eq("tenant_id", tenant_id)
        .not_.is_("outreach_sent_at", "null")
        .is_("last_followup_sent_at", "null")
        .not_.in_("pipeline_status", _TERMINAL_LEAD_STATES)
        .order("outreach_sent_at", desc=False)
        .limit(capped_limit * 2)  # over-fetch; exclusions trim to capped_limit
        .execute()
    )
    rows = [r for r in (res.data or []) if r.get("id")]

    # 3) Exclude cron follow-ups (outreach_sends.sequence_step >= 2).
    cand_ids = [str(r["id"]) for r in rows if str(r["id"]) not in excluded_ids]
    followed_up: set[str] = set()
    if cand_ids:
        try:
            fu = (
                sb.table("outreach_sends")
                .select("lead_id, sequence_step")
                .in_("lead_id", cand_ids)
                .gte("sequence_step", 2)
                .execute()
            )
            for r in fu.data or []:
                lid = r.get("lead_id")
                if lid:
                    followed_up.add(str(lid))
        except Exception as exc:  # noqa: BLE001 — exclusion is best-effort
            log.warning("batch_reenrich.followup_lookup_failed", err=type(exc).__name__)

    eligible = [
        str(r["id"])
        for r in rows
        if str(r["id"]) not in excluded_ids and str(r["id"]) not in followed_up
    ][:capped_limit]

    # 4) Re-enrich each (budget-capped, idempotent, fail-open). Bounded concurrency
    #    — Hunter + NeverBounce are rate-limited.
    sem = asyncio.Semaphore(3)
    upgraded_ids: list[str] = []

    async def _one(lead_id: str) -> None:
        async with sem:
            try:
                out = await reenrich_lead_contact(tenant_id=tenant_id, lead_id=lead_id)
            except Exception as exc:  # noqa: BLE001 — fail open per lead
                log.warning("batch_reenrich.lead_failed", lead_id=lead_id, err=type(exc).__name__)
                return
        if out.get("upgraded"):
            upgraded_ids.append(lead_id)

    await asyncio.gather(*[_one(lid) for lid in eligible])

    # 5) Optionally re-send the OFFICIAL outreach to the upgraded address.
    queued = 0
    if not dry_run and upgraded_ids:
        now = datetime.now(tz=UTC)
        for idx, lead_id in enumerate(upgraded_ids):
            defer = _spread_defer(now, idx, per_day_cap)
            # Stamp BEFORE enqueue so a re-run can never double-send this lead:
            # the eligibility query filters `last_followup_sent_at IS NULL`, so
            # the stamp must land first. On enqueue failure we revert it (below)
            # to keep the lead retryable.
            sb.table("leads").update({"last_followup_sent_at": now.isoformat()}).eq(
                "id", lead_id
            ).eq("tenant_id", tenant_id).execute()
            try:
                await enqueue(
                    "outreach_task",
                    {
                        "tenant_id": tenant_id,
                        "lead_id": lead_id,
                        "channel": "email",
                        "force": True,  # bypass the already-sent guard; footer still renders
                        "sequence_step": 1,  # official outreach copy (not a follow-up template)
                    },
                    job_id=f"batch_resend:{tenant_id}:{lead_id}:{int(defer.timestamp())}",
                    defer_until=defer,
                )
                queued += 1
            except Exception as exc:  # noqa: BLE001 — one bad enqueue doesn't kill the batch
                log.warning(
                    "batch_reenrich.enqueue_failed", lead_id=lead_id, err=type(exc).__name__
                )
                # Revert the stamp so a failed-to-queue lead stays retryable.
                try:
                    sb.table("leads").update({"last_followup_sent_at": None}).eq("id", lead_id).eq(
                        "tenant_id", tenant_id
                    ).execute()
                except Exception:  # noqa: BLE001 — best-effort revert
                    pass

    result: dict[str, Any] = {
        "ok": True,
        "tenant_id": tenant_id,
        "dry_run": dry_run,
        "eligible": len(eligible),
        "upgraded": len(upgraded_ids),
        "resends_queued": queued,
    }
    try:
        await log_action(
            tenant_id,
            "batch.reenrich_and_resend",
            actor_user_id=actor,
            target_table="leads",
            diff=result,
        )
    except Exception:  # noqa: BLE001 — audit is best-effort
        pass
    log.info("batch_reenrich.done", **result)
    return result
