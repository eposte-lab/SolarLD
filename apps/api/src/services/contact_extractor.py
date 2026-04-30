"""ADR-003 — contact-channel cascade wrapper.

Thin layer over ``email_extractor.extract_email`` that broadens the
output from a single channel ("email") to three: ``email``, ``whatsapp``,
``phone_only``. Required by the post-Atoka pipeline so a lead with no
business email but a known WhatsApp number (or, last resort, a phone
number) can still enter a non-email outreach queue instead of being
dropped.

Cascade order — pinned by tests, do NOT reorder casually
--------------------------------------------------------

1. **Atoka email** — ``azienda["email"]``, role-account filtered. The
   ~85 % of rows where Atoka returns a personal address short-circuit
   here.

2. **Atoka WhatsApp** — best-effort probe of the raw Atoka payload for
   a WhatsApp number. Atoka does not have a dedicated WhatsApp field
   yet (see ``AtokaProfile``), so we look at a few common keys
   (``whatsapp``, ``whatsapp_phone``, ``contacts[type=whatsapp]``).

3. **Website scrape** — the existing ``_from_website`` logic embedded
   in ``extract_email``. Same code path as today; no new HTTP calls.

4. **Atoka phone-only** — ``azienda["phone"]``, used to enqueue a
   manual-call task when no digital channel exists.

Performance contract (called out in the ADR review)
---------------------------------------------------

The wrapper MUST NOT add overhead to the dominant email path. The
85 % case (Atoka email present, not a role account, not blacklisted)
returns after exactly the same work as ``extract_email`` plus one
``ContactResult`` allocation. We achieve this by reusing the private
helpers ``_from_atoka`` and ``_check_blacklists`` directly — calling
``extract_email`` first would force a website-scrape attempt before the
WhatsApp branch ever runs, wasting up to 5 s on rows where WA was the
right answer.

Logging
-------

The wrapper does NOT write to ``email_extraction_log`` itself; it
returns a ``ContactResult`` and the caller (orchestrator) writes one
row per attempt with the new ``channel`` column from migration 0087.
This keeps the log-write transaction in the place that already owns it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
import structlog

# Reach into email_extractor's private helpers on purpose — see the
# "Performance contract" note above. Both helpers are pure / well-tested
# and changing them would be a breaking change to the email path anyway,
# so the coupling is acceptable.
from .email_extractor import (
    ExtractionResult,
    _check_blacklists,  # type: ignore[reportPrivateUsage]
    _from_atoka,        # type: ignore[reportPrivateUsage]
    extract_email,
)

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


Channel = Literal["email", "whatsapp", "phone_only"]


@dataclass(frozen=True)
class ContactResult:
    """Outcome of one cascade attempt.

    Frozen so callers can safely cache it without worrying about a
    downstream consumer mutating ``raw``.

    Attributes
    ----------
    channel:
        Which medium the lead should be contacted on. Pinned to the
        three values allowed by ``email_extraction_log.channel`` CHECK
        constraint (migration 0087).
    value:
        The contact identifier — an email address, an E.164 phone /
        WhatsApp number, or ``None`` when the cascade failed entirely.
    source:
        The provider tag, mirroring ``email_extraction_log.source``:
        ``'atoka'`` | ``'website_scrape'`` | ``'failed'``.
    confidence:
        ``0.0 .. 1.0`` — meaningful for ``website_scrape``; ``1.0`` for
        Atoka direct hits.
    cost_cents:
        API cost paid for this attempt. Atoka credits are paid upstream
        in the discovery phase, so this is ``0`` for every Atoka branch.
    company_name / domain:
        Pass-through copies of the lead identity for the audit log.
    raw:
        Provider-specific payload kept for debugging and re-derivation.
    notes:
        Human-readable note for the audit log.
    """

    channel: Channel
    value: str | None
    source: str
    confidence: float
    cost_cents: int = 0
    company_name: str | None = None
    domain: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    @classmethod
    def failed(
        cls,
        *,
        company_name: str | None,
        domain: str | None,
        notes: str,
    ) -> ContactResult:
        """Sentinel result for a fully-exhausted cascade. Channel is
        ``email`` because the audit log treats failures as belonging to
        the default (and historic) channel — there is no separate
        ``failed`` channel value, only a ``failed`` source."""
        return cls(
            channel="email",
            value=None,
            source="failed",
            confidence=0.0,
            cost_cents=0,
            company_name=company_name,
            domain=domain,
            notes=notes,
        )


# ---------------------------------------------------------------------------
# WhatsApp probe
# ---------------------------------------------------------------------------


# Atoka does not have a first-class WhatsApp field today. We look at a
# small set of keys that have appeared in raw payloads / partner feeds
# and fall back to a contact-list scan. The probe is intentionally
# conservative — false positives here would route a real customer to
# a WhatsApp template they never opted into, which is reputationally
# worse than missing the channel and falling through to the next branch.
_WA_TOP_LEVEL_KEYS = (
    "whatsapp",
    "whatsapp_phone",
    "whatsapp_number",
    "wa_phone",
)
# Italian mobile shape used to validate a probed number — phone
# numbers used for WhatsApp must be mobile (3xx prefix) per Meta.
_E164_MOBILE_IT_RE = re.compile(r"^\+?39?3\d{8,9}$")
# Loose digit shape for normalisation pre-validation.
_NON_DIGITS_RE = re.compile(r"[^\d+]")


def _probe_whatsapp(azienda: dict[str, Any]) -> str | None:
    """Best-effort search for a WhatsApp number in the candidate dict.

    Returns the number normalised to ``+39…`` form, or ``None`` if
    nothing usable is found. Performance: O(small constant) — at most
    ~10 dict lookups + one regex per candidate value. Negligible
    compared to even the cheapest HTTP call.
    """

    candidates: list[str] = []

    for key in _WA_TOP_LEVEL_KEYS:
        v = azienda.get(key)
        if isinstance(v, str) and v.strip():
            candidates.append(v.strip())

    # Atoka raw payloads sometimes carry a ``contacts: [{type, value}]``
    # list. Only the ``type='whatsapp'`` entry counts here — a generic
    # phone in that list is handled by the phone-only branch.
    contacts = azienda.get("contacts")
    if isinstance(contacts, list):
        for c in contacts:
            if not isinstance(c, dict):
                continue
            ctype = (c.get("type") or "").strip().lower()
            if ctype == "whatsapp":
                value = c.get("value") or c.get("number")
                if isinstance(value, str) and value.strip():
                    candidates.append(value.strip())

    for raw in candidates:
        normalised = _normalise_wa_number(raw)
        if normalised:
            return normalised
    return None


def _normalise_wa_number(raw: str) -> str | None:
    """Return ``+39…`` form of an Italian mobile number, or ``None``.

    We deliberately reject fixed-line and unknown-shape numbers — Meta
    only delivers WhatsApp templates to mobile numbers, so accepting a
    landline here would just generate a downstream send-failure.
    """

    digits = _NON_DIGITS_RE.sub("", raw)
    if not digits:
        return None

    # Strip leading 00 (international "trunk" prefix) so 0039… becomes 39…
    if digits.startswith("00"):
        digits = digits[2:]

    # Accept any of: +39…, 39…, or bare 3xx (assume IT default).
    if digits.startswith("+"):
        candidate = digits
    elif digits.startswith("39") and len(digits) >= 11:
        candidate = "+" + digits
    elif digits.startswith("3"):
        candidate = "+39" + digits
    else:
        return None

    if _E164_MOBILE_IT_RE.match(candidate):
        return candidate
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def extract_contact(
    azienda: dict[str, Any],
    *,
    sb: Any,
    http_client: httpx.AsyncClient | None = None,
) -> ContactResult:
    """Run the four-step cascade and return one ``ContactResult``.

    Parameters mirror ``email_extractor.extract_email`` exactly so this
    wrapper can be a drop-in replacement at the orchestrator boundary.

    Notes
    -----
    * Never raises — HTTP / Supabase exceptions inside the email
      extractor are swallowed there and surface as ``source='failed'``.
    * Logging to ``email_extraction_log`` (with the new ``channel``
      column) is the caller's responsibility.
    """

    company_name = azienda.get("legal_name") or azienda.get("company_name")
    domain = (azienda.get("website_domain") or "").strip().lower() or None

    # 1. Atoka email — the dominant happy path. We replicate the
    #    in-memory probe that ``extract_email`` does first; it is
    #    essentially free (one dict lookup + a role-account regex) and
    #    lets us short-circuit *before* invoking the website scraper.
    atoka_email = _from_atoka(azienda, company_name=company_name, domain=domain)
    if atoka_email and atoka_email.email:
        blacklist = await _check_blacklists(atoka_email.email, sb=sb)
        if blacklist is None:
            return _from_extraction(atoka_email, channel="email")
        # Blacklisted — record the failure with the email channel intact
        # so the audit log shows why we did NOT route to WA / phone for
        # this lead (the blacklist is a hard stop across channels).
        return _from_extraction(blacklist, channel="email")

    # 2. Atoka WhatsApp — explicit ADR-003 priority over scraping.
    wa_number = _probe_whatsapp(azienda)
    if wa_number:
        log.debug(
            "contact_extractor.atoka_whatsapp",
            number=wa_number,
            company=company_name,
        )
        return ContactResult(
            channel="whatsapp",
            value=wa_number,
            source="atoka",
            confidence=1.0,
            cost_cents=0,
            company_name=company_name,
            domain=domain,
            raw={"atoka_whatsapp": wa_number},
            notes="WhatsApp number from Atoka B2B database (raw probe).",
        )

    # 3. Website scrape — delegate to extract_email. It will re-check
    #    the Atoka email branch (a no-op because we already established
    #    Atoka has no usable address) and proceed to the website fetch.
    #    The re-check costs one dict lookup; far cheaper than duplicating
    #    the scraping logic here.
    scrape_result = await extract_email(azienda, sb=sb, http_client=http_client)
    if scrape_result.email and scrape_result.source != "failed":
        return _from_extraction(scrape_result, channel="email")

    # 4. Atoka phone-only — fallback to manual call queue.
    phone = (azienda.get("phone") or "").strip()
    if phone:
        log.debug(
            "contact_extractor.atoka_phone_only",
            phone=phone,
            company=company_name,
        )
        return ContactResult(
            channel="phone_only",
            value=phone,
            source="atoka",
            confidence=1.0,
            cost_cents=0,
            company_name=company_name,
            domain=domain,
            raw={"atoka_phone": phone},
            notes="Phone-only fallback (no email, no WhatsApp).",
        )

    # Cascade exhausted.
    return ContactResult.failed(
        company_name=company_name,
        domain=domain,
        notes=(
            "No contact channel found: Atoka email/WhatsApp empty, "
            "website scrape failed, no phone on record."
        ),
    )


def _from_extraction(result: ExtractionResult, *, channel: Channel) -> ContactResult:
    """Adapt an ``ExtractionResult`` into the wrapper's ``ContactResult``.

    Kept as a thin function rather than a method on ContactResult so
    importing ``ExtractionResult`` doesn't leak into callers of
    ``contact_extractor``.
    """

    return ContactResult(
        channel=channel,
        value=result.email,
        source=result.source,
        confidence=float(result.confidence),
        cost_cents=int(result.cost_cents),
        company_name=result.company_name,
        domain=result.domain,
        raw=dict(result.raw_response or {}),
        notes=result.notes,
    )


__all__ = [
    "Channel",
    "ContactResult",
    "extract_contact",
]
