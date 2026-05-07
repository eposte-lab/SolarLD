"""Pre-L5 anti-spam validator (deterministic, no LLM).

Runs as a post-processing step on every L5 Haiku score to apply
deterministic penalties — and outright rejections — for candidates
with disposable email domains, role-account inboxes, free B2B email
providers (gmail/yahoo/libero/...), or invalid Italian VAT checksums.

Rationale: Haiku scoring is good at "is this the right ICP / sector"
but bad at deterministic checks (it'll happily score `info@gmail.com`
80/100 because the building looks great). Keeping these rules in pure
Python is faster (~5ms per batch of 10 vs ~$0.001/Haiku call), more
predictable, and doesn't burn LLM tokens on facts a regex can settle.

Hard rejects (`hard_reject=True`):
  • Disposable email (10minutemail, mailinator, ...) — score=0
  • Italian VAT failing the Luhn-IT checksum — score=0

Soft penalties (subtracted from Haiku's overall_score):
  • Free email provider on a B2B candidate: −20
  • Role-account local part (info@, admin@, sales@, ...): −10
  • Phone number with non-+39 country code (Italy companies): −5

The verdict is persisted on `scan_candidates.proxy_score_data.flags`
so /contatti can render badges and operators can audit why a lead
was scored down.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Curated lookup tables. The disposable list is the ~60 most common
# providers (covers >95% of real-world abuse). For broader coverage point
# this at the upstream `disposable-email-domains` JSON file in a future
# iteration — keeping in-process for now to avoid I/O on the L5 hot path.
# ---------------------------------------------------------------------------

DISPOSABLE_DOMAINS: frozenset[str] = frozenset(
    {
        "10minutemail.com",
        "10minutemail.net",
        "10minutemail.org",
        "20minutemail.com",
        "30minutemail.com",
        "guerrillamail.com",
        "guerrillamail.de",
        "guerrillamail.net",
        "mailinator.com",
        "mailinator.net",
        "mailinator2.com",
        "tempmail.com",
        "tempmail.net",
        "tempmailaddress.com",
        "tempr.email",
        "trashmail.com",
        "trashmail.net",
        "throwaway.email",
        "throwawaymail.com",
        "yopmail.com",
        "yopmail.fr",
        "yopmail.net",
        "fakemailgenerator.com",
        "fakeinbox.com",
        "getnada.com",
        "nadamail.com",
        "sharklasers.com",
        "spam4.me",
        "maildrop.cc",
        "mintemail.com",
        "burnermail.io",
        "burnermail.com",
        "dispostable.com",
        "discard.email",
        "discardmail.com",
        "moakt.com",
        "tempinbox.com",
        "spambox.us",
        "anonbox.net",
        "tempmailo.com",
        "emailondeck.com",
        "mohmal.com",
        "mailcatch.com",
        "mailnesia.com",
        "fakemail.net",
        "mytrashmail.com",
    }
)

FREE_EMAIL_PROVIDERS: frozenset[str] = frozenset(
    {
        # Global
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "ymail.com",
        "rocketmail.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "msn.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "fastmail.com",
        "protonmail.com",
        "proton.me",
        "zoho.com",
        "gmx.com",
        "gmx.net",
        # Italy
        "yahoo.it",
        "hotmail.it",
        "outlook.it",
        "live.it",
        "libero.it",
        "virgilio.it",
        "tiscali.it",
        "alice.it",
        "tin.it",
        "iol.it",
        "email.it",
        "interfree.it",
        "tim.it",
        "inwind.it",
        "tiscalinet.it",
        "fastwebnet.it",
        "katamail.com",
    }
)

ROLE_PREFIXES: frozenset[str] = frozenset(
    {
        "info",
        "contact",
        "contatti",
        "contatto",
        "sales",
        "vendite",
        "commerciale",
        "support",
        "supporto",
        "assistenza",
        "admin",
        "amministrazione",
        "ufficio",
        "office",
        "noreply",
        "no-reply",
        "donotreply",
        "newsletter",
        "marketing",
        "privacy",
        "dpo",
        "compliance",
        "hr",
        "jobs",
        "lavoro",
        "careers",
        "accounting",
        "billing",
        "fatturazione",
        "help",
        "service",
    }
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class QualityVerdict:
    """Outcome of running every deterministic quality check on a candidate."""

    score_delta: int = 0
    """Signed integer to add to the Haiku overall_score. Negative = penalty."""

    flags: list[str] = field(default_factory=list)
    """Audit trail of which checks fired. Persisted on
    `proxy_score_data.flags` so /contatti can render badges."""

    hard_reject: bool = False
    """When True the caller must force `recommended_for_rendering=False`
    and clamp `overall_score=0`. Disposable email + invalid VAT trigger
    this — wasting rendering credits + outreach budget on those is a
    pure burn."""


def validate(
    *,
    email: str | None,
    vat_number: str | None,
    phone: str | None = None,
    business_name: str | None = None,  # reserved for future heuristics
) -> QualityVerdict:
    """Run all deterministic anti-spam checks. Idempotent, no I/O."""
    _ = business_name  # placeholder; not used today

    verdict = QualityVerdict()

    # ── Email checks ────────────────────────────────────────────────────
    if email and "@" in email:
        local, _, domain = email.lower().rpartition("@")
        domain = domain.strip()
        local = local.strip()

        if domain in DISPOSABLE_DOMAINS:
            verdict.hard_reject = True
            verdict.score_delta = -100
            verdict.flags.append("disposable_email")
            return verdict  # short-circuit; nothing else matters

        if domain in FREE_EMAIL_PROVIDERS:
            verdict.score_delta -= 20
            verdict.flags.append("free_email_provider_b2b")

        # Role account — exact match on the local-part. Avoids false
        # positives like "marioinformatico@..." (substring match would
        # incorrectly fire role on it).
        if local in ROLE_PREFIXES:
            verdict.score_delta -= 10
            verdict.flags.append("role_account_email")

    # ── Italian VAT checksum (P.IVA = 11 digits, Luhn-IT) ───────────────
    if vat_number:
        cleaned = "".join(ch for ch in vat_number if ch.isdigit())
        if len(cleaned) == 11 and not _is_valid_italian_vat(cleaned):
            verdict.hard_reject = True
            verdict.score_delta = -100
            if "invalid_vat_checksum" not in verdict.flags:
                verdict.flags.append("invalid_vat_checksum")

    # ── Phone country mismatch (best-effort) ────────────────────────────
    if phone:
        normalized = phone.replace(" ", "").replace("-", "").replace(".", "")
        if normalized.startswith("+") and not normalized.startswith("+39"):
            verdict.score_delta -= 5
            verdict.flags.append("phone_country_mismatch")

    return verdict


# ---------------------------------------------------------------------------
# Italian VAT (P.IVA) checksum
# ---------------------------------------------------------------------------


def _is_valid_italian_vat(vat: str) -> bool:
    """Validate an Italian P.IVA via the standard checksum.

    Rules (Agenzia delle Entrate):
      - Exactly 11 digits.
      - Sum: odd-position (1-indexed) digits as-is; even-position digits
        doubled, then if the doubled value > 9 subtract 9 (== sum digits).
      - Total mod 10 must be 0.
    """
    if len(vat) != 11 or not vat.isdigit():
        return False
    s = 0
    for i in range(11):  # 0-indexed
        d = int(vat[i])
        position_1indexed = i + 1
        if position_1indexed % 2 == 0:  # even 1-indexed position → double
            d *= 2
            if d > 9:
                d -= 9
        s += d
    return s % 10 == 0
