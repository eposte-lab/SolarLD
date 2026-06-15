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
from .hunter_io_service import domain_search, verify_email_hunter
from .neverbounce_service import NeverBounceError, verify_email
from .web_scraper import is_non_business_domain

log = get_logger(__name__)

# One Hunter domain-search costs ~1 credit. The Starter plan bills a FLAT bucket
# of 2000 credits for ~€50 (≈ €0.025/credit), so we account the budget in
# CREDITS, not euro-cents: 1 lookup = 1 credit. The premium_contact_usage.*_cents
# columns therefore hold CREDITS for this feature (the "_cents" name is legacy).
# Hunter itself hard-stops at the plan's credit ceiling, so €50 can never be
# exceeded regardless of this counter.
_HUNTER_CREDITS_PER_LOOKUP = 1

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
        # functional inboxes — never a person; kept out of the alias fallback
        "fatture",
        "fatturazione",
        "ordini",
        "acquisti",
        "preventivi",
        "assistenza",
        "supporto",
        "support",
        "help",
        "staff",
        "hr",
        "pec",
        "prenotazioni",
        "booking",
        "reception",
        "logistica",
        "spedizioni",
        "magazzino",
    }
)


@dataclass(slots=True)
class DecisionMakerUpgrade:
    email: str
    name: str | None
    role: str | None
    confidence: str  # always "alta"
    fallback_email: str | None  # the previous (website) email, kept as backup


# Local-parts that are NOT an upgrade over the website email, so we never pick
# them as the "better" contact: the generic catch-alls we already scrape
# (info@, contatti@) plus purely functional inboxes (warehouse, invoicing,
# logistics, support…). A more-targeted generic that is NOT here — direzione@,
# commerciale@, amministrazione@, vendite@, sede@ — IS kept: it reaches
# management/sales and beats info@, even though it isn't a named person.
_NOT_AN_UPGRADE_LOCAL_PARTS = frozenset(
    {
        # baseline / catch-all — same value as a scraped info@
        "info",
        "contatti",
        "contact",
        "mail",
        "posta",
        "email",
        "pec",
        "noreply",
        "no-reply",
        # SALES dept — the wrong target for an energy-BUYER outreach (the
        # contact-enrichment spec excludes these on purpose; this also reverts
        # PR #328 which had briefly accepted commerciale@ as a "good generic").
        "commerciale",
        "vendite",
        "sales",
        # purely functional — never a commercial / management / buying contact.
        # NB: "acquisti" (purchasing) is NOT here — it IS a good energy-buyer
        # target (role ladder), so a Hunter-returned acquisti@ is accepted.
        "fatture",
        "fatturazione",
        "ordini",
        "preventivi",
        "assistenza",
        "supporto",
        "support",
        "help",
        "staff",
        "hr",
        "prenotazioni",
        "booking",
        "reception",
        "logistica",
        "spedizioni",
        "magazzino",
        "privacy",
        "dpo",
        "gdpr",
        "legal",
    }
)


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


async def _attempt_upgrade(
    *,
    company_domain: str | None,
    current_email: str | None,
    tenant_id: str,
    client: httpx.AsyncClient | None = None,
    lead_id: str | None = None,
    candidate_id: str | None = None,
) -> tuple[DecisionMakerUpgrade | None, str]:
    """Core premium lookup. Returns ``(upgrade_or_None, reason)`` — the reason is
    a short diagnostic tag surfaced in the on-demand/batch result so an operator
    can tell WHY a lead wasn't upgraded:

      already_personal · non_business_domain · no_api_key · budget_exhausted ·
      budget_check_failed · hunter_error · no_named_candidate · validation_failed
      · ok
    """
    if not is_weak_email(current_email):
        return None, "already_personal"
    domain = (company_domain or "").strip().lower()
    if domain.startswith("www."):
        domain = domain[4:]
    if not domain or "." not in domain or is_non_business_domain(domain):
        return None, "non_business_domain"

    # No premium provider configured → no-op WITHOUT charging the budget counter
    # (so spend/lookups stay clean) until the key is set on the worker.
    if not settings.hunter_api_key:
        log.warning("premium_finder.no_api_key", tenant_id=tenant_id)
        return None, "no_api_key"

    sb = get_service_client()

    # Atomically reserve the Hunter cost; skip if the budget is exhausted.
    try:
        reserved = sb.rpc(
            "reserve_premium_budget",
            {"p_tenant_id": tenant_id, "p_cost_cents": _HUNTER_CREDITS_PER_LOOKUP},
        ).execute()
        if not bool(reserved.data):
            log.info("premium_finder.budget_exhausted", tenant_id=tenant_id)
            return None, "budget_exhausted"
    except Exception as exc:  # noqa: BLE001 — budget RPC is a hard dependency boundary
        log.warning("premium_finder.budget_check_failed", err=type(exc).__name__)
        return None, "budget_check_failed"

    # Hunter domain-search — BROAD: no seniority/department filter (same 1-credit
    # cost) so SME contacts that aren't classified into a department/seniority
    # still surface. The department filter alone dropped most Italian SMEs to
    # zero results; we re-prioritise in-process instead.
    try:
        _ds = await domain_search(domain, seniority=None, department=None, limit=10, client=client)
        candidates = _ds.emails
    except Exception as exc:  # noqa: BLE001 — fail open, keep the website email
        # ``detail`` carries the HTTP status/body from HunterIoError → an
        # invalid/unauthorised key shows as status=401/403 here.
        log.warning(
            "premium_finder.lookup_failed",
            domain=domain,
            err=type(exc).__name__,
            detail=str(exc)[:200],
        )
        return None, "hunter_error"

    # Prefer the best NAMED person (real first+last) on the company domain. If
    # Hunter has none, fall back to the best MORE-TARGETED mailbox — a named-ish
    # alias OR a commercial/management generic (direzione@, commerciale@,
    # amministrazione@, vendite@, sede@) — which still beats the scraped info@.
    # We skip only the baseline catch-alls we already have (info@, contatti@)
    # and purely-functional inboxes (magazzino@, fatture@, …), and never the
    # email we already hold. candidates are already sorted by confidence desc.
    cur = (current_email or "").strip().lower()
    best = None
    best_alias = None
    for c in candidates:
        if not c.email or "@" not in c.email:
            continue
        local, dom = c.email.split("@", 1)
        if dom.lower() != domain:
            continue
        if local.lower() in _NOT_AN_UPGRADE_LOCAL_PARTS:
            continue
        if c.email.strip().lower() == cur:
            continue  # same mailbox we already have — not an upgrade
        if c.first_name and c.last_name:
            if best is None:
                best = c
        elif best_alias is None:
            best_alias = c
    best = best or best_alias
    if best is None or not best.email:
        log.info(
            "premium_finder.no_named_candidate",
            tenant_id=tenant_id,
            domain=domain,
            returned=len(candidates),
            # what Hunter actually returned → distinguishes "no data" from "only
            # generics we excluded" from "we over-rejected a usable contact".
            emails=[c.email for c in candidates if c.email][:6],
        )
        return None, "no_named_candidate"

    # Validate the named guess — fail CLOSED: never promote an unverified
    # personal guess. Prefer NeverBounce when its key is configured (most
    # authoritative); otherwise fall back to Hunter's own bundled verification
    # (included in the Hunter plan) gated on an explicit "valid" status OR a
    # high deliverability confidence.
    if settings.neverbounce_api_key:
        try:
            verification = await verify_email(best.email, client=client)
        except NeverBounceError:
            return None, "validation_failed"
        if not verification.result.sendable or verification.role_address:
            return None, "validation_failed"
    else:
        # No NeverBounce → use Hunter's own email-verifier (included in the plan)
        # as the last-layer deliverability check, instead of a confidence
        # heuristic that rejected real moderate-confidence emails. Fall back to
        # the domain-search signal ONLY if the verifier call itself errors, so a
        # transient hiccup doesn't drop a good candidate.
        try:
            deliverable, vstatus = await verify_email_hunter(best.email, client=client)
        except Exception as exc:  # noqa: BLE001 — verifier hiccup → soft fallback
            log.warning("premium_finder.verify_failed", err=type(exc).__name__)
            deliverable = best.verified or best.confidence_score >= _MIN_HUNTER_CONFIDENCE
            vstatus = "verifier_error"
        if not deliverable:
            log.info(
                "premium_finder.hunter_validation_failed",
                tenant_id=tenant_id,
                domain=domain,
                email=best.email,
                status=vstatus,
                confidence=best.confidence_score,
            )
            return None, "validation_failed"

    name = " ".join(p for p in (best.first_name, best.last_name) if p) or None
    log.info(
        "premium_finder.upgraded",
        tenant_id=tenant_id,
        lead_id=lead_id,
        candidate_id=candidate_id,
        domain=domain,
        confidence=best.confidence_score,
    )
    return (
        DecisionMakerUpgrade(
            email=best.email.strip().lower(),
            name=name,
            role=best.position,
            confidence="alta",
            fallback_email=current_email,
        ),
        "ok",
    )


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

    Thin wrapper over :func:`_attempt_upgrade` that drops the diagnostic reason
    — used by L6 / batch, which only need the upgrade-or-None. Returns ``None``
    when the current email is already personal, there is no usable business
    domain, the budget is exhausted, or no validated named person is found.
    """
    upgrade, _reason = await _attempt_upgrade(
        company_domain=company_domain,
        current_email=current_email,
        tenant_id=tenant_id,
        client=client,
        lead_id=lead_id,
        candidate_id=candidate_id,
    )
    return upgrade


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

    upgrade, reason = await _attempt_upgrade(
        company_domain=domain,
        current_email=current_email,
        tenant_id=tenant_id,
        lead_id=lead_id,
    )
    if upgrade is None:
        return {"ok": True, "upgraded": False, "reason": reason}

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
    limit: int = 50,
    spread_days: int = 5,  # noqa: ARG001 — kept for caller symmetry; pacing is per_day_cap
    per_day_cap: int = 30,
    dry_run: bool = True,
    target: str = "sent",
    actor: str | None = None,
) -> dict[str, Any]:
    """§D — batch re-enrich leads and (optionally, target='sent' only) re-send.

    ``target='ready_to_send'`` instead enriches the NOT-yet-sent warehouse
    backlog (the leads about to go out) so they get a premium decision-maker
    contact BEFORE the daily cron sends them — it never sends here (the normal
    send path does), regardless of ``dry_run``. The alt-address / follow-up
    exclusions apply only to the already-sent target.

    Selects SENT, non-terminal leads that have NOT already received a follow-up,
    EXCLUDING the leads handled manually via the alt-address resend (Hilton /
    Sigma — identified from the ``lead.outreach_resent_alt_address`` audit
    trail). Picks the BEST candidates first — highest L5 score, then most
    recently sent (recent render) — and caps at ``limit`` (default 50) so we
    spend premium credits on the strongest already-sent leads instead of all
    ~300, leaving budget for the funnel auto-enrichment of NEW leads. For each
    it runs the premium finder (``reenrich_lead_contact`` — budget-capped,
    idempotent, fail-open). When ``dry_run`` is False AND a lead was upgraded, it
    enqueues a re-send of the OFFICIAL outreach to the new address (``force``
    re-send, no template change), paced at ``per_day_cap``/day on weekday
    mornings, and stamps ``last_followup_sent_at`` so re-runs never double-send.

    ``dry_run`` defaults to True: the first run only finds + persists better
    contacts (surfacing the premium badge) and reports counts — it never sends.
    """
    sb = get_service_client()
    capped_limit = max(1, min(limit, 500))
    is_sent = target != "ready_to_send"

    # 1) Already-SENT target only: exclude leads handled manually via the
    #    alt-address resend (Hilton / Sigma) — already reached by phone/alt email.
    excluded_ids: set[str] = set()
    if is_sent:
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

    # 2) Candidate leads — best first (highest L5 score). target='sent' takes
    #    already-sent, non-terminal, not-yet-followed-up leads (re-engagement);
    #    target='ready_to_send' takes the NOT-yet-sent warehouse backlog so the
    #    leads about to go out get a premium contact before tomorrow's send.
    q = sb.table("leads").select("id").eq("tenant_id", tenant_id)
    if is_sent:
        q = (
            q.not_.is_("outreach_sent_at", "null")
            .is_("last_followup_sent_at", "null")
            .not_.in_("pipeline_status", _TERMINAL_LEAD_STATES)
            .order("score", desc=True)
            .order("outreach_sent_at", desc=True)
        )
    else:
        q = (
            q.eq("pipeline_status", "ready_to_send")
            .is_("outreach_sent_at", "null")
            .order("score", desc=True)
        )
    res = q.limit(capped_limit * 2).execute()  # over-fetch; exclusions trim below
    rows = [r for r in (res.data or []) if r.get("id")]

    # 3) Already-SENT target only: exclude cron follow-ups (sequence_step >= 2).
    followed_up: set[str] = set()
    if is_sent:
        cand_ids = [str(r["id"]) for r in rows if str(r["id"]) not in excluded_ids]
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
    #    ONLY for the already-sent target — ready_to_send leads must go out via
    #    the normal daily cron (with the premium contact now in place), never
    #    force-sent here.
    queued = 0
    if is_sent and not dry_run and upgraded_ids:
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
        "target": target,
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
