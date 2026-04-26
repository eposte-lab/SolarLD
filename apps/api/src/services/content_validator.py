"""Task 16 — Email content validation (Phase 6 / spam gate).

Runs on every outbound email, immediately after Jinja2 rendering and
BEFORE the send is attempted. Pure function: no HTTP, no DB, no side
effects. Callers (OutreachAgent) own the DB writes and skip logic.

Why a dedicated module
----------------------
A standalone validator is much easier to unit-test (feed rendered HTML,
get a ValidationResult) and allows ops to tune the rules without touching
the send pipeline. The outreach agent calls it as a gate:

    result = validate_email_content(subject, html, text, ...)
    if not result.passed:
        await _log_quarantine(...)
        return _record_failure(reason="content_quarantined")

Validation layers
-----------------
1. **Subject rules** — length, all-caps ratio, excessive punctuation,
   spam-trigger words. Violations here almost always quarantine.

2. **Body rules** — spam-trigger phrases in the rendered text (not raw
   HTML), link count, image count. Violations accumulate a score.

3. **Score gate** — if cumulative score ≥ QUARANTINE_SCORE_THRESHOLD
   (0.50) the email is quarantined even without a hard "block" violation.

Severity system
---------------
``block`` — a single such violation quarantines the email immediately
            regardless of score.  Reserved for phrases that are *always*
            spam (e.g. "hai vinto", "soddisfatti o rimborsati").

``warn``  — adds WARN_WEIGHT to the cumulative score.  Three unrelated
            warn violations trigger quarantine just like one block.

Tuning
------
The lists below are conservative — they are tuned for **Italian B2B
solar emails**.  Words like "risparmio" (savings), "installazione",
"benefici" are deliberately NOT on the list because they appear in
legitimate solar copy.  Only unambiguous spam signals are blocked.

When a legitimate email is quarantined by mistake, add the specific
phrase to the ``EMAIL_STYLE_ALLOWLIST`` or simply shorten/rephrase the
copy.  Do NOT loosen the word lists without ops sign-off.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

# If cumulative warn score reaches this, quarantine.
QUARANTINE_SCORE_THRESHOLD: float = 0.50

# Score added per "warn" violation.
WARN_WEIGHT: float = 0.20

# Score added per "block" violation (also triggers immediate quarantine).
BLOCK_WEIGHT: float = 1.00

# Subject length above this is a warn (not block — long subjects hurt
# deliverability but don't prove spam).
MAX_SUBJECT_LENGTH = 65

# Link counts per email style.
MAX_LINKS_CONVERSATIONAL = 3
MAX_LINKS_VISUAL = 6

# Image counts per email style.
MAX_IMAGES_CONVERSATIONAL = 2
MAX_IMAGES_VISUAL = 8

# Minimum number of ALL-CAPS consecutive chars in a word to flag it.
_CAPS_WORD_RE = re.compile(r"\b[A-Z]{4,}\b")

# Excessive repeated punctuation (3+ same char in a row).
_EXCESSIVE_PUNCT_RE = re.compile(r"[!?]{3,}|\.{4,}")

# <a href> link count.
_LINK_RE = re.compile(r"<a\s[^>]*href\s*=", re.IGNORECASE)

# <img tag count.
_IMG_RE = re.compile(r"<img\s", re.IGNORECASE)

# Optout/unsubscribe links in HTML — we exclude these from the link count
# because an unsubscribe link is mandatory and should never count against us.
_OPTOUT_LINK_RE = re.compile(
    r"<a\s[^>]*href\s*=\s*['\"][^'\"]*(?:optout|unsubscrib|cancell)[^'\"]*['\"]",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Spam trigger word lists
# ---------------------------------------------------------------------------

# Subject-line triggers (case-insensitive partial match, word-boundary-aware).
# Being in the subject is a stronger spam signal than in the body.
SPAM_SUBJECT_PHRASES: list[tuple[str, Literal["block", "warn"]]] = [
    # Absolute giveaways — block immediately
    ("hai vinto",            "block"),
    ("vincitore",            "block"),
    ("congratulazioni",      "block"),
    ("soddisfatti o rimborsati", "block"),
    ("garanzia 100%",        "block"),
    ("garantito al 100%",    "block"),
    ("gratis",               "block"),
    ("gratuito",             "block"),
    ("gratuita",             "block"),
    # High-risk subjects — warn (accumulate score)
    ("urgente",              "warn"),
    ("solo oggi",            "warn"),
    ("ultima chance",        "warn"),
    ("ultima opportunità",   "warn"),
    ("non perdere",          "warn"),
    ("offerta speciale",     "warn"),
    ("offerta limitata",     "warn"),
    ("sconto esclusivo",     "warn"),
    ("acquista ora",         "warn"),
    ("clicca qui",           "warn"),
    ("guadagna",             "warn"),
    # English spam words (some Italian B2B mailers mix languages)
    ("free",                 "block"),
    ("you won",              "block"),
    ("guaranteed",           "warn"),
    ("click here",           "warn"),
]

# Body-text triggers (applied to the plain-text version for speed).
# The text body is stripped of HTML so regex doesn't catch tag attributes.
SPAM_BODY_PHRASES: list[tuple[str, Literal["block", "warn"]]] = [
    # Hard blocks
    ("hai vinto",               "block"),
    ("vincitore",               "block"),
    ("soddisfatti o rimborsati","block"),
    ("garanzia 100%",           "block"),
    ("garantito al 100%",       "block"),
    ("soldi facili",            "block"),
    ("reddito extra",           "block"),
    ("guadagna subito",         "block"),
    # Warns
    ("clicca qui",              "warn"),
    ("clicca adesso",           "warn"),
    ("clicca subito",           "warn"),
    ("acquista ora",            "warn"),
    ("ordina subito",           "warn"),
    ("offerta speciale",        "warn"),
    ("offerta limitata",        "warn"),
    ("solo oggi",               "warn"),
    ("sconto del",              "warn"),
    ("gratis",                  "warn"),
    ("gratuito",                "warn"),
    ("gratuita",                "warn"),
    ("urgente",                 "warn"),
]

# Phrases allowed unconditionally (body only, exact case-insensitive).
# Prevents false-positive on solar industry terminology.
BODY_ALLOWLIST: frozenset[str] = frozenset(
    {
        # These contain "gratis" as a substring but are legitimate.
        # (future additions go here)
    }
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

Severity = Literal["block", "warn"]
ValidationAction = Literal["allow", "quarantine"]


@dataclass(frozen=True)
class ValidationViolation:
    """One specific rule that was triggered."""

    rule: str           # machine-readable rule name, e.g. "spam_trigger_subject"
    field: str          # "subject" | "body" | "structure"
    detail: str         # human-readable description for ops review
    severity: Severity  # "block" or "warn"


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of running ``validate_email_content()``."""

    passed: bool                        # True → send; False → quarantine
    action: ValidationAction            # "allow" | "quarantine"
    score: float                        # 0.0 = clean, ≥ 0.5 = quarantine
    violations: list[ValidationViolation] = field(default_factory=list)

    @property
    def has_blocks(self) -> bool:
        return any(v.severity == "block" for v in self.violations)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_email_content(
    subject: str,
    html: str,
    text: str,
    *,
    email_style: str = "visual_preventivo",
    sequence_step: int = 1,
) -> ValidationResult:
    """Run all validation layers and return a unified result.

    Args:
        subject:      Rendered email subject (no HTML).
        html:         Rendered HTML body (after premailer inlining).
        text:         Rendered plain-text body.
        email_style:  "visual_preventivo" or "plain_conversational".
                      Affects link/image count limits.
        sequence_step: 1-4. Step 4 breakup emails are slightly more lenient
                       on word count (shorter is intentional).

    Returns:
        ``ValidationResult`` with ``passed=True`` when the email is clean,
        ``passed=False`` with ``action='quarantine'`` otherwise.
    """
    violations: list[ValidationViolation] = []
    score: float = 0.0

    # 1. Subject rules
    _check_subject(subject, violations)

    # 2. Body rules (operate on plain text to avoid HTML noise)
    plain = _normalize_text(text or _html_to_text(html))
    _check_body(plain, violations)

    # 3. Structure rules (link + image counts on HTML)
    _check_structure(html, email_style, violations)

    # 4. Compute score
    for v in violations:
        score += BLOCK_WEIGHT if v.severity == "block" else WARN_WEIGHT

    passed = score < QUARANTINE_SCORE_THRESHOLD
    action: ValidationAction = "allow" if passed else "quarantine"

    return ValidationResult(
        passed=passed,
        action=action,
        score=round(score, 3),
        violations=violations,
    )


# ---------------------------------------------------------------------------
# Layer checkers
# ---------------------------------------------------------------------------


def _check_subject(subject: str, violations: list[ValidationViolation]) -> None:
    """Apply subject-level rules (modifies violations in-place)."""
    subj_lower = subject.lower()

    # 1a. Length
    if len(subject) > MAX_SUBJECT_LENGTH:
        violations.append(
            ValidationViolation(
                rule="subject_too_long",
                field="subject",
                detail=f"Subject is {len(subject)} chars (max {MAX_SUBJECT_LENGTH})",
                severity="warn",
            )
        )

    # 1b. ALL-CAPS words (≥4 chars)
    caps_words = _CAPS_WORD_RE.findall(subject)
    if caps_words:
        violations.append(
            ValidationViolation(
                rule="subject_all_caps",
                field="subject",
                detail=f"ALL-CAPS word(s) in subject: {', '.join(caps_words)}",
                severity="warn",
            )
        )

    # 1c. Excessive punctuation
    if _EXCESSIVE_PUNCT_RE.search(subject):
        violations.append(
            ValidationViolation(
                rule="subject_excessive_punctuation",
                field="subject",
                detail="3+ repeated punctuation characters in subject",
                severity="warn",
            )
        )

    # 1d. Spam trigger phrases
    for phrase, severity in SPAM_SUBJECT_PHRASES:
        if phrase in subj_lower:
            violations.append(
                ValidationViolation(
                    rule="spam_trigger_subject",
                    field="subject",
                    detail=f"Spam trigger phrase in subject: '{phrase}'",
                    severity=severity,
                )
            )
            # Only flag the first block trigger — no need to pile on.
            if severity == "block":
                break


def _check_body(plain_text: str, violations: list[ValidationViolation]) -> None:
    """Apply body-level rules to plain text (modifies violations in-place)."""
    text_lower = plain_text.lower()

    for phrase, severity in SPAM_BODY_PHRASES:
        if phrase in BODY_ALLOWLIST:
            continue
        if phrase in text_lower:
            violations.append(
                ValidationViolation(
                    rule="spam_trigger_body",
                    field="body",
                    detail=f"Spam trigger phrase in body: '{phrase}'",
                    severity=severity,
                )
            )
            if severity == "block":
                # One block is enough to quarantine; don't flood the violation list.
                break


def _check_structure(
    html: str,
    email_style: str,
    violations: list[ValidationViolation],
) -> None:
    """Check link and image counts (modifies violations in-place)."""
    is_conversational = email_style == "plain_conversational"
    max_links = MAX_LINKS_CONVERSATIONAL if is_conversational else MAX_LINKS_VISUAL
    max_images = MAX_IMAGES_CONVERSATIONAL if is_conversational else MAX_IMAGES_VISUAL

    # Count links, excluding optout links (mandatory, should not penalise).
    all_links = _LINK_RE.findall(html)
    optout_links = _OPTOUT_LINK_RE.findall(html)
    live_links = max(0, len(all_links) - len(optout_links))

    if live_links > max_links:
        violations.append(
            ValidationViolation(
                rule="too_many_links",
                field="structure",
                detail=(
                    f"{live_links} links (excl. optout) exceeds max {max_links} "
                    f"for style '{email_style}'"
                ),
                severity="warn",
            )
        )

    # Count images.
    image_count = len(_IMG_RE.findall(html))
    if image_count > max_images:
        violations.append(
            ValidationViolation(
                rule="too_many_images",
                field="structure",
                detail=(
                    f"{image_count} <img> tags exceeds max {max_images} "
                    f"for style '{email_style}'"
                ),
                severity="warn",
            )
        )


# ---------------------------------------------------------------------------
# Internal text helpers
# ---------------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _html_to_text(html: str) -> str:
    """Very cheap HTML → plain-text strip (only used when text body is missing)."""
    if not html:
        return ""
    stripped = _HTML_TAG_RE.sub(" ", html)
    return _WHITESPACE_RE.sub(" ", stripped).strip()


def _normalize_text(text: str) -> str:
    """Collapse whitespace and strip leading/trailing space."""
    return _WHITESPACE_RE.sub(" ", text).strip()
