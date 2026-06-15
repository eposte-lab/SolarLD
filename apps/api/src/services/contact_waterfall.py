"""Contact-enrichment waterfall (Phase 1b) — deterministic best-contact resolver.

``resolve_best_contact`` runs, per qualified lead:

  STEP 0  guards  — qualified, idempotent, PEC/non-business, MX, domain_intel
  STEP 1  Hunter-first — reuse ``decision_maker_finder._attempt_upgrade`` (it
          also caches the domain pattern + catch-all into ``domain_intel``)
  STEP 3  role ladder — ufficiotecnico/tecnico/acquisti/direzione/
          amministrazione/info, each guessed @domain and VERIFIED, **gated on
          the domain's catch-all flag** (never blind-blast a catch-all domain).

(STEP 2 — name-discovery + pattern-guess — is Phase 2.)

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
import secrets
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
)
from .hunter_io_service import verify_email_hunter
from .neverbounce_service import NeverBounceError, verify_email
from .web_scraper import _has_mx_record, is_non_business_domain

log = get_logger(__name__)

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
    try:
        reserved = sb.rpc(
            "reserve_premium_budget",
            {"p_tenant_id": tenant_id, "p_cost_cents": _HUNTER_CREDITS_PER_LOOKUP},
        ).execute()
        if not bool(reserved.data):
            return False, "budget_exhausted", 0
    except Exception as exc:  # noqa: BLE001 — budget RPC is a hard boundary
        log.warning("waterfall.budget_check_failed", err=type(exc).__name__)
        return False, "budget_check_failed", 0

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
    orders on)."""
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
    on the audit write (the outcome is still returned)."""
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


def _load_catch_all(sb: Any, domain: str) -> bool | None:
    try:
        row = (
            sb.table("domain_intel")
            .select("catch_all")
            .eq("domain", domain)
            .limit(1)
            .maybe_single()
            .execute()
        )
        if row and row.data and row.data.get("catch_all") is not None:
            return bool(row.data["catch_all"])
    except Exception as exc:  # noqa: BLE001 — cache read is best-effort
        log.debug("domain_intel.read_failed", domain=domain, err=type(exc).__name__)
    return None


async def resolve_best_contact(
    *,
    tenant_id: str,
    lead_id: str,
    name_hint: tuple[str, str] | None = None,  # noqa: ARG001 — used in Phase 2 (STEP 2)
    sector: str | None = None,  # noqa: ARG001 — used in Phase 2
    force: bool = False,
    client: httpx.AsyncClient | None = None,
) -> ContactOutcome:
    """Resolve the best deliverable contact for one qualified lead (STEP 0/1/3)."""
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

    # --- STEP 0 guards: PEC / non-business / MX ------------------------------
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

    # --- STEP 3: role ladder (gated on catch-all) ----------------------------
    catch_all = _load_catch_all(sb, domain)
    if catch_all is None and verifications < settings.max_verifications_per_lead:
        catch_all, pcost = await _detect_catch_all(
            domain, tenant_id=tenant_id, sb=sb, client=client
        )
        cost += pcost
        if pcost:
            verifications += 1
        if catch_all is not None:
            cache_domain_intel(sb, domain, catch_all=catch_all)
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
