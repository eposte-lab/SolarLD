"""Energivori Delta 2 — the contact GATE (Changes C + D).

Decide, BEFORE spending on the roof (Google Solar) / render (Replicate), whether
we have a real *personal* decision-maker email for a company. PASS → the company
proceeds through the existing pipeline; DROP → it is excluded (with a reason) and
never reaches the costly steps.

Two halves:
  * ``build_candidates`` / ``evaluate_gate`` — PURE, unit-tested logic.
  * ``resolve_contact_gate`` — async orchestration: registro decision-maker →
    Hunter domain pattern → email permutations → NeverBounce batch verify →
    ``evaluate_gate``.

PASS rule (Change D):
  - a NAMED personal email that verifies ``valid`` → PASS (alta, verified); or
  - on a catch-all/accept-all domain (smart mode), the most-probable permutation
    → PASS (media, pattern); else
  - DROP with ``funnel_excluded_reason`` ∈ {no_decision_maker, no_domain,
    generic_email_only, unverifiable_strict}.
PEC / role / generic addresses never PASS (a PEC needs ``allow_pec_as_pass``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from ..core.logging import get_logger
from .decision_maker_name import PersonName, it_permutations, render_pattern
from .hunter_io_service import domain_search
from .neverbounce_service import EmailVerification, VerificationResult, batch_verify
from .openapi_company_service import (
    RegistroManager,
    resolve_registro_decision_maker,
)

log = get_logger(__name__)

_MAX_CANDIDATES = 6


@dataclass(frozen=True)
class GateResult:
    passed: bool
    email: str | None = None  # the chosen personal email (PASS only)
    email_status: str = "none"  # valid | accept_all | invalid | none
    email_confidence: str = "nessuna"  # alta | media | nessuna
    email_source: str | None = None  # verified | pattern | None
    excluded_reason: str | None = None  # None on PASS; else the DROP reason
    decision_maker_name: str | None = None
    decision_maker_source: str | None = None  # 'registro' | None
    candidates: list[str] = field(default_factory=list)


def _domain_from(email: str | None, website: str | None) -> str | None:
    """Mail domain to build personal addresses on: the email's domain (that's
    where mail actually lives), else the website host."""
    mail = (email or "").strip().lower()
    if "@" in mail:
        return mail.split("@", 1)[1] or None
    site = (website or "").strip().lower()
    site = site.replace("https://", "").replace("http://", "").replace("www.", "")
    site = site.strip("/").split("/", 1)[0]
    return site or None


def build_candidates(
    person: PersonName, domain: str, hunter_pattern: str | None
) -> list[tuple[str, str]]:
    """Named-email candidates for ``person@domain``, best-first, deduped.

    Returns ``[(email, source)]`` where source is ``pattern`` (rendered from the
    Hunter domain pattern) or ``permutation``. The Hunter-pattern address comes
    first (most probable); then the standard Italian permutations.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    if hunter_pattern:
        lp = render_pattern(hunter_pattern, person)
        if lp:
            email = f"{lp}@{domain}"
            seen.add(email)
            out.append((email, "pattern"))
    for lp in it_permutations(person):
        if not lp:
            continue
        email = f"{lp}@{domain}"
        if email in seen:
            continue
        seen.add(email)
        out.append((email, "permutation"))
    return out[:_MAX_CANDIDATES]


def evaluate_gate(
    *,
    decision_maker: str | None,
    dm_source: str | None,
    domain: str | None,
    candidates: list[tuple[str, str]],
    verifications: dict[str, EmailVerification],
    accept_all: bool,
    acceptall_as_medium: bool,
) -> GateResult:
    """PURE gate decision from resolved inputs (Change D)."""
    tried = [e for e, _ in candidates]
    base = {
        "decision_maker_name": decision_maker,
        "decision_maker_source": dm_source,
        "candidates": tried,
    }
    if not decision_maker:
        return GateResult(passed=False, excluded_reason="no_decision_maker", **base)
    if not domain:
        return GateResult(passed=False, excluded_reason="no_domain", **base)

    # 1) a named email that verified VALID → strongest PASS.
    for email, _source in candidates:
        v = verifications.get(email)
        if v and v.result == VerificationResult.VALID:
            return GateResult(
                passed=True,
                email=email,
                email_status="valid",
                email_confidence="alta",
                email_source="verified",
                **base,
            )

    # 2) catch-all/accept-all domain (smart mode): the most-probable permutation
    #    is deliverable but unconfirmed → PASS at medium confidence. Trust
    #    NeverBounce over Hunter's accept_all flag for the SPECIFIC address:
    #    skip any candidate NB explicitly marked INVALID (it would bounce), and
    #    if every candidate is invalid the domain isn't really catch-all → DROP.
    is_catch_all = accept_all or any(
        v.result == VerificationResult.CATCHALL for v in verifications.values()
    )
    if candidates and is_catch_all and acceptall_as_medium:
        for email, _source in candidates:
            v = verifications.get(email)
            if v is None or v.result != VerificationResult.INVALID:
                return GateResult(
                    passed=True,
                    email=email,
                    email_status="accept_all",
                    email_confidence="media",
                    email_source="pattern",
                    **base,
                )

    # 3) DROP — a personal email could not be established.
    reason = "unverifiable_strict" if not acceptall_as_medium else "generic_email_only"
    return GateResult(passed=False, email_status="invalid", excluded_reason=reason, **base)


async def resolve_contact_gate(
    *,
    email: str | None,
    website: str | None,
    managers: list[RegistroManager],
    client: httpx.AsyncClient,
    acceptall_as_medium: bool = True,
) -> GateResult:
    """Async orchestration: registro decision-maker → Hunter pattern → email
    permutations → NeverBounce batch verify → gate decision (Changes C+D).

    ``email``/``website`` supply the mail domain to build personal addresses on
    (the company email's domain, else the website host)."""
    dm = resolve_registro_decision_maker(managers)
    domain = _domain_from(email, website)
    if dm is None or not dm.first_name or not dm.last_name:
        return evaluate_gate(
            decision_maker=None,
            dm_source=None,
            domain=domain,
            candidates=[],
            verifications={},
            accept_all=False,
            acceptall_as_medium=acceptall_as_medium,
        )
    if not domain:
        return evaluate_gate(
            decision_maker=dm.full_name,
            dm_source="registro",
            domain=None,
            candidates=[],
            verifications={},
            accept_all=False,
            acceptall_as_medium=acceptall_as_medium,
        )

    person = PersonName(first=dm.first_name, last=dm.last_name, role=dm.role)
    hunter_pattern: str | None = None
    accept_all = False
    try:
        ds = await domain_search(domain, seniority=None, department=None, client=client)
        hunter_pattern = ds.pattern
        accept_all = bool(ds.accept_all)
    except Exception as exc:  # noqa: BLE001 — Hunter is best-effort (pattern optional)
        log.warning("gate.hunter_failed", domain=domain, err=type(exc).__name__)

    candidates = build_candidates(person, domain, hunter_pattern)
    verifications = await batch_verify([e for e, _ in candidates], client=client)
    return evaluate_gate(
        decision_maker=dm.full_name,
        dm_source="registro",
        domain=domain,
        candidates=candidates,
        verifications=verifications,
        accept_all=accept_all,
        acceptall_as_medium=acceptall_as_medium,
    )
