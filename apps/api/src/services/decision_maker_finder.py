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

from dataclasses import dataclass
from typing import Any

import httpx

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from .hunter_io_service import HUNTER_COST_PER_CALL_CENTS, domain_search
from .neverbounce_service import NeverBounceError, verify_email
from .web_scraper import is_non_business_domain

log = get_logger(__name__)

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

    # Validate — fail CLOSED: never promote an unverified personal guess.
    try:
        verification = await verify_email(best.email, client=client)
    except NeverBounceError:
        return None
    if not verification.result.sendable or verification.role_address:
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
