"""Contact-enrichment waterfall — deterministic best-contact resolver.

``resolve_best_contact`` runs, per qualified lead:

  STEP 0  guards  — qualified, idempotent, PEC/non-business, MX, domain_intel
  STEP 1  Hunter-first — reuse ``decision_maker_finder._attempt_upgrade`` (it
          also caches the domain pattern + catch-all into ``domain_intel``)
  STEP 2  name-discovery + pattern-guess — discover the owner's name from the
          website (``decision_maker_name``), then resolve their email via the
          Hunter email-finder (real indexed data) or pattern/permutation guesses
          (strict-``valid``-verified, gated on catch-all)
  STEP 3  role ladder — ufficiotecnico/tecnico/acquisti/direzione/
          amministrazione/info, each guessed @domain and VERIFIED, **gated on
          the domain's catch-all flag** (never blind-blast a catch-all domain).

Terminal ``status`` ∈ done | done_unverified | phone_queue | needs_manual |
failed. Always writes ``leads.{best_contact_email, contact_outcome,
contact_enrichment_cost_cents, contact_enriched_at}``; on a winner it mirrors
into ``subjects.decision_maker_*`` (source ``'premium_finder'``) so the send
layer, the premium-first warehouse ordering and the UI badge keep working.

Design:
  - **Fail-open**: any miss keeps the website email; the lead still sends. The
    premium contact is a bonus, never a gate.
  - ``phone_queue`` is a LABEL — the lead stays ``ready_to_send`` and still
    emails its current address (operator's volume directive: never cut sends),
    but is flagged for a phone follow-up.
  - ``commerciale@``/``vendite@`` are NOT in the ladder (sales dept, not the
    energy buyer). Each verification is budget-charged + capped.
"""

from __future__ import annotations

import asyncio
import contextvars
import secrets
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from ..core.config import settings
from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from .decision_maker_finder import (
    _HUNTER_CREDITS_PER_LOOKUP,
    _attempt_upgrade,
    cache_domain_intel,
    is_weak_email,
)
from .decision_maker_name import (
    PersonName,
    find_decision_maker_name,
    it_permutations,
    render_pattern,
)
from .hunter_io_service import find_email, verify_email_hunter
from .national_chains import is_national_chain
from .neverbounce_service import NeverBounceError, verify_email
from .web_scraper import _has_mx_record, is_non_business_domain

log = get_logger(__name__)

# Ambient, async-safe DRY-RUN flag. When set, the waterfall runs STEP 0/1/2/3
# for real (Hunter searches + verifications still happen + cost budget) but
# performs NO mutations that would change outreach: it does not mirror the
# winner into ``subjects`` (no recipient change) and does not write
# ``leads.contact_outcome`` (so a later REAL run isn't skipped as
# already_resolved). Used by the dry-run measurement harness. Each asyncio task
# gets its own context copy, so concurrent dry/real runs never interfere.
_dry_run: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "contact_waterfall_dry_run", default=False
)

# Energy-BUYER role ladder (best-first). 'commerciale'/'vendite' are excluded by
# design (sales dept, not who buys energy); 'info' is the last resort. Each is
# guessed @domain and verified; STOP at the first deliverable.
_ROLE_LADDER = (
    "ufficiotecnico",
    "tecnico",
    "acquisti",
    "direzione",
    "amministrazione",
    "info",
)

# Statuses (NeverBounce result / Hunter verifier) that prove a random probe
# address is accepted → the domain is catch-all. 'unknown'/'webmail' are NOT
# here (couldn't determine ≠ accepts-all).
_CATCH_ALL_STATUSES = frozenset({"catchall", "accept_all", "valid"})
_NEGATIVE_STATUSES = frozenset({"invalid", "disposable"})

# STEP 3 guesses ``role@domain`` addresses that may not exist, so it demands a
# STRICT positive verdict — only a genuine ``valid``. The permissive deliverable
# flag also passes 'unknown'/'accept_all'/'webmail' (fine for STEP 1, where
# Hunter actually indexed the address; fabricate-and-send for a guessed one).
_STRICT_VALID_STATUSES = frozenset({"valid"})

# STEP-1 reasons that mean a Hunter credit was actually charged (the reserve
# passed and the domain-search ran).
_STEP1_CHARGED_REASONS = frozenset(
    {"hunter_error", "no_named_candidate", "validation_failed", "ok"}
)


@dataclass(slots=True)
class ContactOutcome:
    status: Literal["done", "done_unverified", "phone_queue", "needs_manual", "failed"]
    email: str | None = None
    name: str | None = None
    role: str | None = None
    kind: Literal["decision_maker", "role", "generic"] | None = None
    verified: bool = False
    cost_cents: int = 0
    reason: str = ""
    candidates: list[str] = field(default_factory=list)


def _reserve_budget(sb: Any, tenant_id: str) -> bool:
    """Atomically reserve 1 Hunter credit against the tenant budget. False when
    the budget is exhausted OR the RPC errors (caller treats both as a stop)."""
    try:
        reserved = sb.rpc(
            "reserve_premium_budget",
            {"p_tenant_id": tenant_id, "p_cost_cents": _HUNTER_CREDITS_PER_LOOKUP},
        ).execute()
        return bool(reserved.data)
    except Exception as exc:  # noqa: BLE001 — budget RPC is a hard boundary
        log.warning("waterfall.budget_check_failed", err=type(exc).__name__)
        return False


async def _reserve_and_verify(
    email: str,
    *,
    tenant_id: str,
    sb: Any,
    client: httpx.AsyncClient | None,
) -> tuple[bool, str, int]:
    """Charge 1 budget credit and verify ``email`` deliverability. Returns
    ``(deliverable, status, cost_charged)``. Role addresses ARE allowed (this is
    the role ladder). Budget-exhausted → ``(False, 'budget_exhausted', 0)``."""
    if not _reserve_budget(sb, tenant_id):
        return False, "budget_exhausted", 0

    cost = _HUNTER_CREDITS_PER_LOOKUP
    if settings.neverbounce_api_key:
        try:
            v = await verify_email(email, client=client)
        except NeverBounceError:
            return False, "nb_error", cost
        return bool(v.result.sendable), str(v.result.value), cost
    try:
        deliverable, status = await verify_email_hunter(email, client=client)
    except Exception as exc:  # noqa: BLE001 — fail closed on verify error
        log.warning("waterfall.verify_failed", err=type(exc).__name__)
        return False, "verify_error", cost
    return deliverable, status, cost


async def _detect_catch_all(
    domain: str,
    *,
    tenant_id: str,
    sb: Any,
    client: httpx.AsyncClient | None,
) -> tuple[bool | None, int]:
    """1-probe catch-all detection: verify a random, certainly-non-existent
    localpart. If it's accepted → catch-all. Returns ``(catch_all|None, cost)``;
    ``None`` when the probe couldn't decide (unknown/error/budget)."""
    probe = f"zz{secrets.token_hex(4)}@{domain}"
    _deliverable, status, cost = await _reserve_and_verify(
        probe, tenant_id=tenant_id, sb=sb, client=client
    )
    if status in _CATCH_ALL_STATUSES:
        return True, cost
    if status in _NEGATIVE_STATUSES:
        return False, cost
    return None, cost  # unknown / error → undecided


def _mirror_to_subject(
    sb: Any,
    subject_id: str,
    *,
    email: str,
    name: str | None,
    role: str | None,
    fallback: str | None,
) -> None:
    """Write the winning contact into subjects.* so the send layer + premium
    ordering + badge use it. source='premium_finder' (the value warehouse_pick
    orders on). No-op under a dry run — never change the outreach recipient."""
    if _dry_run.get():
        return
    sb.table("subjects").update(
        {
            "decision_maker_email": email,
            "decision_maker_name": name,
            "decision_maker_role": role,
            "decision_maker_email_source": "premium_finder",
            "decision_maker_email_fallback": fallback,
            "decision_maker_email_verified": True,
        }
    ).eq("id", subject_id).execute()


def _finish(
    sb: Any,
    *,
    lead_id: str,
    tenant_id: str,
    outcome: ContactOutcome,
    best_email: str | None,
) -> ContactOutcome:
    """Always-write tail: persist the terminal outcome on the lead. Never raises
    on the audit write (the outcome is still returned). Skips the write under a
    dry run so a later REAL run isn't short-circuited as already_resolved."""
    if not _dry_run.get():
        try:
            sb.table("leads").update(
                {
                    "best_contact_email": best_email,
                    "contact_outcome": outcome.status,
                    "contact_enrichment_cost_cents": outcome.cost_cents,
                    "contact_enriched_at": datetime.now(tz=UTC).isoformat(),
                }
            ).eq("id", lead_id).eq("tenant_id", tenant_id).execute()
        except Exception as exc:  # noqa: BLE001 — audit write is best-effort
            log.warning("waterfall.finish_write_failed", lead_id=lead_id, err=type(exc).__name__)
    log.info(
        "waterfall.done",
        tenant_id=tenant_id,
        lead_id=lead_id,
        status=outcome.status,
        reason=outcome.reason,
        cost_cents=outcome.cost_cents,
    )
    return outcome


def _load_domain_intel(sb: Any, domain: str, *columns: str) -> dict[str, Any] | None:
    try:
        row = (
            sb.table("domain_intel")
            .select(",".join(columns))
            .eq("domain", domain)
            .limit(1)
            .maybe_single()
            .execute()
        )
        if row and row.data:
            return dict(row.data)
    except Exception as exc:  # noqa: BLE001 — cache read is best-effort
        log.debug("domain_intel.read_failed", domain=domain, err=type(exc).__name__)
    return None


def _load_catch_all(sb: Any, domain: str) -> bool | None:
    row = _load_domain_intel(sb, domain, "catch_all")
    if row and row.get("catch_all") is not None:
        return bool(row["catch_all"])
    return None


def _load_email_pattern(sb: Any, domain: str) -> str | None:
    row = _load_domain_intel(sb, domain, "email_pattern")
    pattern = row.get("email_pattern") if row else None
    return pattern if isinstance(pattern, str) and pattern else None


@dataclass(slots=True)
class _CatchAll:
    """Per-resolution catch-all memo. ``probed`` guards against re-probing an
    UNDECIDED domain (Hunter 'unknown' → value stays None but probed flips True),
    which is the dominant case for SMTP-blocking SME mail hosts."""

    value: bool | None = None
    probed: bool = False


async def _resolve_catch_all(
    state: _CatchAll,
    domain: str,
    *,
    tenant_id: str,
    sb: Any,
    client: httpx.AsyncClient | None,
    verifications: int,
) -> tuple[bool | None, int, int]:
    """Resolve the domain catch-all flag AT MOST ONCE per resolution (memoized in
    ``state``, including an undecided None), then reuse. Returns
    ``(catch_all, cost_added, verifications_added)``."""
    if state.value is not None or state.probed:
        return state.value, 0, 0
    cached = _load_catch_all(sb, domain)
    if cached is not None:
        state.value, state.probed = cached, True
        return cached, 0, 0
    if verifications >= settings.max_verifications_per_lead:
        return None, 0, 0
    catch_all, pcost = await _detect_catch_all(domain, tenant_id=tenant_id, sb=sb, client=client)
    state.value, state.probed = catch_all, True
    if catch_all is not None:
        cache_domain_intel(sb, domain, catch_all=catch_all)
    return catch_all, pcost, (1 if pcost else 0)


def _person_from_hint(name_hint: tuple[str, str] | None) -> PersonName | None:
    if not name_hint:
        return None
    first, last = (name_hint[0] or "").strip(), (name_hint[1] or "").strip()
    if not first or not last:
        return None
    return PersonName(first=first, last=last, role=None)


async def _verify_guess(
    addr: str,
    *,
    tenant_id: str,
    sb: Any,
    client: httpx.AsyncClient | None,
) -> tuple[str, int]:
    """Reserve + verify one GUESSED address. Returns ``(status, cost)`` where
    status is the raw verifier verdict ('valid'/'invalid'/'budget_exhausted'/…)."""
    _deliverable, status, vcost = await _reserve_and_verify(
        addr, tenant_id=tenant_id, sb=sb, client=client
    )
    return status, vcost


def _win_named(
    sb: Any,
    *,
    lead_id: str,
    tenant_id: str,
    subject_id: str,
    email: str,
    person: PersonName,
    current_email: str,
    cost: int,
    reason: str,
    candidates: list[str],
) -> ContactOutcome:
    """Mirror a verified NAMED contact into subjects.* and persist the win."""
    full_name = f"{person.first} {person.last}".strip()
    _mirror_to_subject(
        sb, subject_id, email=email, name=full_name, role=person.role, fallback=current_email
    )
    return _finish(
        sb,
        lead_id=lead_id,
        tenant_id=tenant_id,
        outcome=ContactOutcome(
            status="done",
            email=email,
            name=full_name,
            role=person.role,
            kind="decision_maker",
            verified=True,
            cost_cents=cost,
            reason=reason,
            candidates=candidates,
        ),
        best_email=email,
    )


async def _step2_named_guess(
    *,
    sb: Any,
    tenant_id: str,
    lead_id: str,
    subject_id: str,
    domain: str,
    current_email: str,
    name_hint: tuple[str, str] | None,
    catch_state: _CatchAll,
    client: httpx.AsyncClient | None,
    cost: int,
    verifications: int,
) -> tuple[ContactOutcome | None, int, int]:
    """STEP 2 — name discovery + pattern-guess. Returns
    ``(outcome|None, cost, verifications)``; a non-None outcome is TERMINAL
    (already mirrored + persisted). ``None`` → fall through to STEP 3.

    Order (credit-frugal): Hunter email-finder (real indexed data, trusted even
    on catch-all when Hunter-verified) → blind pattern/permutation guesses (ONLY
    on a confirmed non-catch-all domain, each strict-'valid'-verified).
    """
    cap = settings.max_verifications_per_lead
    person = _person_from_hint(name_hint)
    if person is None and verifications < cap:
        person = await find_decision_maker_name(domain=domain, client=client)
    if person is None:
        return None, cost, verifications  # no name → STEP 3

    tried: list[str] = []
    catch_all: bool | None = None

    # --- 2a) Hunter email-finder: authoritative person → email ----------------
    if verifications < cap:
        if not _reserve_budget(sb, tenant_id):
            return None, cost, verifications  # budget gone → STEP 3 surfaces it
        cost += _HUNTER_CREDITS_PER_LOOKUP
        verifications += 1
        try:
            res = await find_email(
                domain=domain, first_name=person.first, last_name=person.last, client=client
            )
        except Exception as exc:  # noqa: BLE001 — finder is best-effort
            log.debug("waterfall.find_email_failed", domain=domain, err=type(exc).__name__)
            res = None
        cand = (res.email or "").strip().lower() if res and res.email else ""
        if cand and "@" in cand:
            tried.append(cand)
            if res is not None and res.verified:  # Hunter found AND verified it
                return (
                    _win_named(
                        sb,
                        lead_id=lead_id,
                        tenant_id=tenant_id,
                        subject_id=subject_id,
                        email=cand,
                        person=person,
                        current_email=current_email,
                        cost=cost,
                        reason="step2_hunter_finder",
                        candidates=tried,
                    ),
                    cost,
                    verifications,
                )
            # Unverified Hunter candidate → strict-verify, but only off catch-all.
            catch_all, ccost, cvadd = await _resolve_catch_all(
                catch_state,
                domain,
                tenant_id=tenant_id,
                sb=sb,
                client=client,
                verifications=verifications,
            )
            cost += ccost
            verifications += cvadd
            if catch_all is False and verifications < cap:
                status, vcost = await _verify_guess(cand, tenant_id=tenant_id, sb=sb, client=client)
                cost += vcost
                if vcost:
                    verifications += 1
                if status in _STRICT_VALID_STATUSES:
                    return (
                        _win_named(
                            sb,
                            lead_id=lead_id,
                            tenant_id=tenant_id,
                            subject_id=subject_id,
                            email=cand,
                            person=person,
                            current_email=current_email,
                            cost=cost,
                            reason="step2_finder_verified",
                            candidates=tried,
                        ),
                        cost,
                        verifications,
                    )

    # --- 2b) blind pattern / permutation guesses (only off catch-all) ---------
    # _resolve_catch_all is memoized in catch_state: if 2a already probed, this
    # reuses that value at zero extra cost (no double probe).
    catch_all, ccost, cvadd = await _resolve_catch_all(
        catch_state, domain, tenant_id=tenant_id, sb=sb, client=client, verifications=verifications
    )
    cost += ccost
    verifications += cvadd
    if catch_all is not False:
        return None, cost, verifications  # catch-all/undecided → never blind-blast

    pattern = _load_email_pattern(sb, domain)
    locals_ = [render_pattern(pattern, person)] if pattern else it_permutations(person)
    for lp in locals_:
        if verifications >= cap:
            break
        if not lp:
            continue
        addr = f"{lp}@{domain}"
        if addr == current_email or addr in tried:
            continue
        status, vcost = await _verify_guess(addr, tenant_id=tenant_id, sb=sb, client=client)
        cost += vcost
        if vcost:
            verifications += 1
        tried.append(addr)
        if status in {"budget_exhausted", "budget_check_failed"}:
            return (
                _finish(
                    sb,
                    lead_id=lead_id,
                    tenant_id=tenant_id,
                    outcome=ContactOutcome(
                        status="needs_manual",
                        reason="budget_exhausted",
                        cost_cents=cost,
                        candidates=tried,
                    ),
                    best_email=current_email,
                ),
                cost,
                verifications,
            )
        if status in _STRICT_VALID_STATUSES:
            return (
                _win_named(
                    sb,
                    lead_id=lead_id,
                    tenant_id=tenant_id,
                    subject_id=subject_id,
                    email=addr,
                    person=person,
                    current_email=current_email,
                    cost=cost,
                    reason="step2_pattern_guess",
                    candidates=tried,
                ),
                cost,
                verifications,
            )

    return None, cost, verifications  # no verified named email → STEP 3


async def resolve_best_contact(
    *,
    tenant_id: str,
    lead_id: str,
    name_hint: tuple[str, str] | None = None,
    sector: str | None = None,  # noqa: ARG001 — reserved for Hunter dept filtering
    force: bool = False,
    dry_run: bool = False,
    client: httpx.AsyncClient | None = None,
) -> ContactOutcome:
    """Resolve the best deliverable contact for one qualified lead (STEP 0/1/2/3).

    ``dry_run=True`` runs every step for real (Hunter searches + verifications,
    budget IS charged because the credits are genuinely spent) but writes NOTHING
    that changes outreach — no subject mirror, no ``leads.contact_outcome`` — so
    it measures what the waterfall WOULD do without touching live sends.
    """
    token = _dry_run.set(dry_run)
    try:
        return await _resolve_best_contact(
            tenant_id=tenant_id,
            lead_id=lead_id,
            name_hint=name_hint,
            force=force,
            client=client,
        )
    finally:
        _dry_run.reset(token)


async def _resolve_best_contact(
    *,
    tenant_id: str,
    lead_id: str,
    name_hint: tuple[str, str] | None,
    force: bool,
    client: httpx.AsyncClient | None,
) -> ContactOutcome:
    sb = get_service_client()

    # --- load lead + subject -------------------------------------------------
    lead = (
        sb.table("leads")
        .select("id, subject_id, contact_outcome")
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .maybe_single()
        .execute()
    )
    if not lead or not lead.data:
        return ContactOutcome(status="failed", reason="lead_not_found")
    if lead.data.get("contact_outcome") and not force:
        return ContactOutcome(status="done", reason="already_resolved")
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
        return _finish(
            sb,
            lead_id=lead_id,
            tenant_id=tenant_id,
            outcome=ContactOutcome(status="failed", reason="subject_not_found"),
            best_email=None,
        )
    if subj.data.get("decision_maker_email_source") == "premium_finder" and not force:
        return ContactOutcome(status="done", reason="already_premium")

    current_email = (subj.data.get("decision_maker_email") or "").strip().lower()
    if "@" not in current_email:
        return _finish(
            sb,
            lead_id=lead_id,
            tenant_id=tenant_id,
            outcome=ContactOutcome(status="needs_manual", reason="no_domain"),
            best_email=None,
        )
    local, domain = current_email.split("@", 1)

    # --- STEP 0 guards: PEC / non-business / chain / MX ----------------------
    if (
        local in {"pec", "postacertificata"}
        or domain.endswith("pec.it")
        or is_non_business_domain(domain)
    ):
        return _finish(
            sb,
            lead_id=lead_id,
            tenant_id=tenant_id,
            outcome=ContactOutcome(status="needs_manual", reason="non_business_or_pec"),
            best_email=current_email,
        )
    # National chains: do NOT enrich. The decision-maker flow would only surface a
    # far-away HQ contact; the per-store/targeted address already on the lead is
    # the right one — keep it untouched. (chain+generic is excluded at send.)
    if is_national_chain(domain=domain):
        return _finish(
            sb,
            lead_id=lead_id,
            tenant_id=tenant_id,
            outcome=ContactOutcome(status="needs_manual", reason="national_chain"),
            best_email=current_email,
        )
    if not await asyncio.to_thread(_has_mx_record, domain):
        cache_domain_intel(sb, domain, mx_valid=False)
        return _finish(
            sb,
            lead_id=lead_id,
            tenant_id=tenant_id,
            outcome=ContactOutcome(status="needs_manual", reason="no_mx"),
            best_email=current_email,
        )
    cache_domain_intel(sb, domain, mx_valid=True)

    cost = 0
    verifications = 0

    # --- STEP 1: Hunter-first (also caches pattern/accept_all → domain_intel) -
    upgrade, reason = await _attempt_upgrade(
        company_domain=domain,
        current_email=current_email,
        tenant_id=tenant_id,
        lead_id=lead_id,
        client=client,
    )
    if reason in _STEP1_CHARGED_REASONS:
        cost += _HUNTER_CREDITS_PER_LOOKUP
        verifications += 1
    if upgrade is not None:
        _mirror_to_subject(
            sb,
            subject_id,
            email=upgrade.email,
            name=upgrade.name,
            role=upgrade.role,
            fallback=upgrade.fallback_email,
        )
        return _finish(
            sb,
            lead_id=lead_id,
            tenant_id=tenant_id,
            outcome=ContactOutcome(
                status="done",
                email=upgrade.email,
                name=upgrade.name,
                role=upgrade.role,
                kind="decision_maker",
                verified=True,
                cost_cents=cost,
                reason="step1_hunter",
            ),
            best_email=upgrade.email,
        )
    if reason == "already_personal":  # current email is already a named person
        return _finish(
            sb,
            lead_id=lead_id,
            tenant_id=tenant_id,
            outcome=ContactOutcome(
                status="done",
                email=current_email,
                kind="decision_maker",
                reason="already_personal",
                cost_cents=cost,
            ),
            best_email=current_email,
        )
    if reason in {"no_api_key", "budget_exhausted", "budget_check_failed"}:
        return _finish(
            sb,
            lead_id=lead_id,
            tenant_id=tenant_id,
            outcome=ContactOutcome(status="needs_manual", reason=reason, cost_cents=cost),
            best_email=current_email,
        )

    # The domain catch-all is probed at most ONCE across STEP 2 + STEP 3
    # (memoized here, including the undecided 'unknown' verdict).
    catch_state = _CatchAll()

    # --- STEP 2: name discovery + pattern-guess ------------------------------
    step2_outcome, cost, verifications = await _step2_named_guess(
        sb=sb,
        tenant_id=tenant_id,
        lead_id=lead_id,
        subject_id=subject_id,
        domain=domain,
        current_email=current_email,
        name_hint=name_hint,
        catch_state=catch_state,
        client=client,
        cost=cost,
        verifications=verifications,
    )
    if step2_outcome is not None:
        return step2_outcome

    # --- STEP 3: role ladder (gated on catch-all) ----------------------------
    catch_all, ccost, cvadd = await _resolve_catch_all(
        catch_state, domain, tenant_id=tenant_id, sb=sb, client=client, verifications=verifications
    )
    cost += ccost
    verifications += cvadd
    if catch_all is not False:  # True or undecided → never blind-blast
        return _finish(
            sb,
            lead_id=lead_id,
            tenant_id=tenant_id,
            outcome=ContactOutcome(
                status="phone_queue",
                reason="catch_all" if catch_all else "catch_all_unknown",
                cost_cents=cost,
            ),
            best_email=current_email,
        )

    tried: list[str] = []
    for role in _ROLE_LADDER:
        if verifications >= settings.max_verifications_per_lead:
            break
        addr = f"{role}@{domain}"
        if addr == current_email:
            continue  # we already hold this exact mailbox — not an upgrade
        _deliverable, status, vcost = await _reserve_and_verify(
            addr, tenant_id=tenant_id, sb=sb, client=client
        )
        cost += vcost
        if vcost:
            verifications += 1
        tried.append(addr)
        if status in {"budget_exhausted", "budget_check_failed"}:
            return _finish(
                sb,
                lead_id=lead_id,
                tenant_id=tenant_id,
                outcome=ContactOutcome(
                    status="needs_manual",
                    reason="budget_exhausted",
                    cost_cents=cost,
                    candidates=tried,
                ),
                best_email=current_email,
            )
        # Guessed address → only a strict 'valid' is trustworthy enough to send.
        if status in _STRICT_VALID_STATUSES:
            _mirror_to_subject(
                sb, subject_id, email=addr, name=None, role=role, fallback=current_email
            )
            return _finish(
                sb,
                lead_id=lead_id,
                tenant_id=tenant_id,
                outcome=ContactOutcome(
                    status="done",
                    email=addr,
                    role=role,
                    kind="generic" if role == "info" else "role",
                    verified=True,
                    cost_cents=cost,
                    reason=f"step3_{role}",
                    candidates=tried,
                ),
                best_email=addr,
            )

    return _finish(
        sb,
        lead_id=lead_id,
        tenant_id=tenant_id,
        outcome=ContactOutcome(
            status="phone_queue", reason="ladder_exhausted", cost_cents=cost, candidates=tried
        ),
        best_email=current_email,
    )


# --------------------------------------------------------------------------- #
# Dry-run measurement harness — efficiency report over a sample (no mutations)
# --------------------------------------------------------------------------- #
_ACTIVE_STATUSES = ("sent", "ready_to_send", "engaged", "picked")


def _select_dryrun_targets(
    sb: Any, tenant_id: str, sample: int, statuses: tuple[str, ...] = _ACTIVE_STATUSES
) -> list[str]:
    """Pick up to ``sample`` B2B lead ids (in ``statuses``) whose current contact
    is a weak generic email on a real business domain — the pool the waterfall
    could actually upgrade (skips PEC / free webmail / chains / already-named)."""
    leads = (
        sb.table("leads")
        .select("id, subject_id, created_at")
        .eq("tenant_id", tenant_id)
        .in_("pipeline_status", list(statuses))
        .order("created_at", desc=True)
        .limit(sample * 4)
        .execute()
    )
    rows = leads.data or []
    sid_to_lead: dict[str, str] = {}
    for r in rows:
        sid = r.get("subject_id")
        if sid and sid not in sid_to_lead:
            sid_to_lead[sid] = r["id"]
    if not sid_to_lead:
        return []
    subs = (
        sb.table("subjects")
        .select("id, decision_maker_email, type")
        .in_("id", list(sid_to_lead))
        .execute()
    )
    targets: list[str] = []
    for s in subs.data or []:
        if s.get("type") != "b2b":
            continue
        email = (s.get("decision_maker_email") or "").strip().lower()
        if "@" not in email:
            continue
        local, domain = email.split("@", 1)
        if local in {"pec", "postacertificata"} or domain.endswith("pec.it"):
            continue
        if is_non_business_domain(domain) or not is_weak_email(email):
            continue
        if is_national_chain(domain=domain):
            continue  # chains skew the measure (HQ contact, not the local buyer)
        targets.append(sid_to_lead[s["id"]])
        if len(targets) >= sample:
            break
    return targets


async def contact_waterfall_dryrun(
    *,
    tenant_id: str,
    sample: int = 50,
    concurrency: int = 8,
) -> dict[str, Any]:
    """Run the FULL waterfall (Hunter + verify, real credits) in DRY-RUN over a
    sample of upgradeable leads and return an efficiency report — no subject
    mirror, no ``leads`` write, so live outreach is untouched. Returns the
    distribution of terminal outcomes, the verified-contact rate, and the Hunter
    credits spent. MUST run where the Hunter key is configured (the worker)."""
    sb = get_service_client()
    targets = _select_dryrun_targets(sb, tenant_id, sample)
    if not targets:
        return {"tenant_id": tenant_id, "measured": 0, "note": "no upgradeable leads found"}

    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(lead_id: str) -> ContactOutcome | None:
        async with sem:
            try:
                return await resolve_best_contact(
                    tenant_id=tenant_id, lead_id=lead_id, force=True, dry_run=True
                )
            except Exception as exc:  # noqa: BLE001 — one bad lead must not abort the run
                log.warning("dryrun.lead_failed", lead_id=lead_id, err=type(exc).__name__)
                return None

    outcomes = [o for o in await asyncio.gather(*(_one(t) for t in targets)) if o is not None]
    measured = len(outcomes)
    by_status = Counter(o.status for o in outcomes)
    wins = [o for o in outcomes if o.status == "done"]
    report: dict[str, Any] = {
        "tenant_id": tenant_id,
        "sample_requested": sample,
        "measured": measured,
        "by_status": dict(by_status),
        "by_reason": dict(Counter(o.reason for o in outcomes)),
        "by_kind": dict(Counter(o.kind for o in outcomes if o.kind)),
        "verified_contact_done": len(wins),
        "done_pct": round(100 * len(wins) / measured, 1) if measured else 0.0,
        "phone_queue": by_status.get("phone_queue", 0),
        "needs_manual": by_status.get("needs_manual", 0),
        "hunter_credits_spent": sum(o.cost_cents for o in outcomes),
        "examples": [
            {"email": o.email, "name": o.name, "role": o.role, "reason": o.reason}
            for o in wins[:15]
        ],
    }
    log.info(
        "contact_waterfall.dryrun_report",
        **{k: v for k, v in report.items() if k != "examples"},
    )
    return report


async def contact_waterfall_backfill(
    *,
    tenant_id: str,
    target: str = "ready_to_send",
    limit: int = 200,
    concurrency: int = 5,
) -> dict[str, Any]:
    """REAL waterfall enrichment over the existing ``target`` backlog (non-chain,
    weak-email leads): resolves + WRITES the premium contact (mirror into
    subjects, contact_outcome) so the qualified-contact send gate has something
    to send to. Unlike the dry-run this mutates. Returns the same report shape."""
    sb = get_service_client()
    targets = _select_dryrun_targets(sb, tenant_id, limit, statuses=(target,))
    if not targets:
        return {"tenant_id": tenant_id, "target": target, "enriched": 0, "note": "no targets"}

    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(lead_id: str) -> ContactOutcome | None:
        async with sem:
            try:
                return await resolve_best_contact(tenant_id=tenant_id, lead_id=lead_id, force=True)
            except Exception as exc:  # noqa: BLE001 — one bad lead must not abort
                log.warning("backfill.lead_failed", lead_id=lead_id, err=type(exc).__name__)
                return None

    outcomes = [o for o in await asyncio.gather(*(_one(t) for t in targets)) if o is not None]
    by_status = Counter(o.status for o in outcomes)
    wins = [o for o in outcomes if o.status == "done"]
    report: dict[str, Any] = {
        "tenant_id": tenant_id,
        "target": target,
        "processed": len(outcomes),
        "by_status": dict(by_status),
        "qualified_done": len(wins),
        "phone_queue": by_status.get("phone_queue", 0),
        "needs_manual": by_status.get("needs_manual", 0),
        "hunter_credits_spent": sum(o.cost_cents for o in outcomes),
    }
    log.info("contact_waterfall.backfill_report", **report)
    return report
