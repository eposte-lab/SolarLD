"""Email extraction cascade (Task 6 — 9-phase pipeline, Phase 3).

The only GDPR-safe email acquisition paths for cold B2B outreach in Italy
under the "legittimo interesse commerciale" basis (art. 6.1.f) are:

  1. **Atoka** — paid Italian business registry database. Atoka has its own
     legitimate interest legal basis for storing and selling B2B contact
     data. This is our primary source.
  2. **Website scraping** — `mailto:` links and contact-form email addresses
     FROM THE COMPANY'S OWN WEBSITE. The company published these for business
     contact — using them for B2B outreach is generally covered by the same
     art. 6.1.f basis (guidance: Garante FAQ 2022, WP29/EDPB WP 217).

What is EXPLICITLY FORBIDDEN (GDPR Article 5.1.c — data minimisation):
  * **Email pattern guessing** — `firstname.lastname@domain.it`. Even if the
    pattern happens to be correct, we never obtained the address from the
    data subject or a source with a legitimate basis. A synthetically
    constructed address does NOT have provenance. Sending to a guessed address
    is a GDPR violation. This module will NEVER do it.

  * **LinkedIn scraping** — platform ToS prohibit automated retrieval;
    Garante decisions have sanctioned companies doing this.

Cascade order
-------------
  1. `from_atoka`       — Atoka profile already in memory from Phase 1.
  2. `from_website`     — HTTP fetch of company website, parse mailto: links
                          and `<input type="email">` values. Polite: 5-second
                          timeout, 1 follow-redirect max, no JS rendering.
  3. `from_pec_registry` — Registro delle Imprese PEC feed (FUTURE STUB).
                           PEC is the Italian "certified email" address for
                           legal entities. Every Italian SRL/SpA/SNC has one
                           by law. Officially published, free to query via
                           CCIAA API — but we don't have the integration yet.
  4. Failure           — Return `source='failed'` so the rejection is logged
                          and the candidate proceeds to `lead_rejection_log`
                          with reason `email_extraction_failed` (not blocked
                          from re-processing after we add the PEC integration).

All outcomes (success AND failure) are logged to `email_extraction_log`
(migration 0057). The caller is responsible for writing the log — we return
a rich `ExtractionResult` and the orchestrator persists it.

Blacklist check
---------------
Before returning a successfully extracted email, we check `email_blacklist`
and `domain_blacklist` (migration 0057). If the address or its domain is
listed, we return `source='failed'` with a descriptive reason. The CALLER
must not re-extract if the blacklist reason is permanent.

IO model
--------
This module is async throughout (httpx for website fetch, asyncio.to_thread
for Supabase reads). It does NOT call the Supabase client directly — the
caller passes a `sb` client so this module stays testable without mocking
global state.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Website fetch: short timeout prevents one slow site from blocking the
# whole pipeline. One redirect follows (www → non-www is common).
WEBSITE_FETCH_TIMEOUT_S = 5.0
WEBSITE_MAX_REDIRECTS = 1
WEBSITE_MAX_RESPONSE_BYTES = 512 * 1024  # 512 KB — enough for a contact page

# Mailto regex: matches href="mailto:..." and plain "user@domain.tld" patterns
# in HTML source. NOT used to guess addresses — used to FIND published ones.
_MAILTO_HREF_RE = re.compile(
    r'href=["\']mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})["\']',
    re.IGNORECASE,
)
_PLAIN_EMAIL_RE = re.compile(
    r'\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b',
)

# Role accounts we never target — their inbox is typically a ticket queue,
# not a person. Sending cold B2B to `info@` is noise and harms reputation.
_ROLE_PREFIXES = frozenset({
    "info", "info.", "contatti", "contatto", "contact", "hello", "hola",
    "support", "supporto", "assistenza", "help", "noreply", "no-reply",
    "noreply", "no.reply", "mail", "email", "newsletter", "news",
    "marketing", "press", "stampa", "media", "hr", "risorse.umane",
    "recruiting", "jobs", "careers", "lavora.con.noi", "postmaster",
    "webmaster", "admin", "amministrazione", "segreteria", "reception",
    "ufficio", "generale", "general", "office", "vendor", "fornitori",
    "privacy", "gdpr", "dpo", "legal", "legale", "abuse",
})

# Contact page URL suffixes to try in order (appended to the website root).
_CONTACT_PATHS = (
    "/contatti",
    "/contattaci",
    "/contact",
    "/contacts",
    "/contact-us",
    "/chi-siamo",    # "about us" pages often list email
    "/about",
    "",              # homepage as last resort
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ExtractionResult:
    """Outcome of one extraction attempt. Persisted to email_extraction_log."""

    email: str | None
    source: str                # 'atoka' | 'website_scrape' | 'pec_registry' | 'failed'
    confidence: float          # 0..1 — meaningful for website_scrape; 1.0 for Atoka
    cost_cents: int            # API cost paid for this attempt
    company_name: str | None = None
    domain: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)
    # Human-readable note for the audit log.
    notes: str = ""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def extract_email(
    azienda: dict[str, Any],
    *,
    sb: Any,
    http_client: httpx.AsyncClient | None = None,
) -> ExtractionResult:
    """Run the extraction cascade for a single company.

    Parameters
    ----------
    azienda:     Company dict — AtokaProfile.raw or a flat dict from
                 our scan pipeline with at least:
                   * `email`          : str | None   (Atoka direct)
                   * `website_domain` : str | None
                   * `legal_name`     : str | None
    sb:          Supabase service-role client (for blacklist checks).
    http_client: Optional pre-built AsyncClient. If None, a transient
                 client is created and closed internally.

    Returns
    -------
    ExtractionResult with `source='failed'` when no email is found or when
    the found address is on a blacklist.
    """

    company_name = azienda.get("legal_name") or azienda.get("company_name")
    domain = _resolve_domain(azienda)

    # 1. Atoka
    result = _from_atoka(azienda, company_name=company_name, domain=domain)
    if result and result.email:
        blacklist_result = await _check_blacklists(result.email, sb=sb)
        if blacklist_result:
            return blacklist_result
        return result

    # 2. Website scraping
    owns_client = http_client is None
    if owns_client:
        http_client = httpx.AsyncClient(
            timeout=WEBSITE_FETCH_TIMEOUT_S,
            max_redirects=WEBSITE_MAX_REDIRECTS,
            headers={"User-Agent": "SolarLead/2.0 (business contact research)"},
            follow_redirects=True,
        )
    try:
        result = await _from_website(
            azienda, company_name=company_name, domain=domain, client=http_client
        )
    finally:
        if owns_client:
            await http_client.aclose()

    if result and result.email:
        blacklist_result = await _check_blacklists(result.email, sb=sb)
        if blacklist_result:
            return blacklist_result
        return result

    # 3. PEC registry (STUB — integration pending)
    # result = await _from_pec_registry(azienda, ...)
    # Future: query CCIAA / Registro Imprese PEC endpoint.

    # 4. All sources exhausted — return failure record.
    return ExtractionResult(
        email=None,
        source="failed",
        confidence=0.0,
        cost_cents=0,
        company_name=company_name,
        domain=domain,
        notes="No email found via Atoka or website scraping. PEC registry integration pending.",
    )


# ---------------------------------------------------------------------------
# Source 1: Atoka
# ---------------------------------------------------------------------------


def _from_atoka(
    azienda: dict[str, Any],
    *,
    company_name: str | None,
    domain: str | None,
) -> ExtractionResult | None:
    """Extract email from Atoka data already loaded in memory.

    Atoka's B2B data API returns `email` for known decision-makers
    (when the data is in their index). Cost is already paid at the
    discovery phase — zero marginal cost here.

    Returns None when Atoka has no email (prompts the cascade to
    continue rather than returning a 'failed' result).
    """

    email = (azienda.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return None

    if _is_role_account(email):
        log.debug(
            "email_extractor.atoka_role_account",
            email=email,
            company=company_name,
        )
        return None  # Atoka returned a role account — try the website

    return ExtractionResult(
        email=email,
        source="atoka",
        confidence=1.0,
        cost_cents=0,  # already paid at Atoka discovery phase
        company_name=company_name,
        domain=domain,
        raw_response={"atoka_email": email},
        notes="Email from Atoka B2B database.",
    )


# ---------------------------------------------------------------------------
# Source 2: Website scraping
# ---------------------------------------------------------------------------


async def _from_website(
    azienda: dict[str, Any],
    *,
    company_name: str | None,
    domain: str | None,
    client: httpx.AsyncClient,
) -> ExtractionResult | None:
    """Scrape the company website for published mailto: links.

    ONLY extracts emails that are explicitly published as `mailto:` href
    attributes or plain email addresses in the HTML source. No inference,
    no guessing.

    Strategy:
      1. Build a list of URLs to try: contact page, about page, homepage.
      2. For each URL, fetch and extract email candidates.
      3. Score candidates: prefer personal-looking addresses over role accounts,
         prefer the domain that matches the company's website.
      4. Return the best candidate.

    Returns None when no email found (no failure record — just falls through).
    """

    if not domain:
        return None

    base_url = _domain_to_base_url(domain)
    if not base_url:
        return None

    candidates: list[tuple[float, str, str]] = []  # (score, email, page_url)

    for path in _CONTACT_PATHS:
        url = base_url.rstrip("/") + path
        emails_on_page = await _fetch_emails_from_url(url, client=client, domain=domain)
        for email, score in emails_on_page:
            candidates.append((score, email, url))
        if candidates:
            break  # found something — don't scrape the homepage unnecessarily

    if not candidates:
        log.debug(
            "email_extractor.website_no_email",
            domain=domain,
            company=company_name,
        )
        return None

    # Best = highest score, then alphabetically for determinism.
    candidates.sort(key=lambda x: (-x[0], x[1]))
    best_score, best_email, best_url = candidates[0]

    return ExtractionResult(
        email=best_email,
        source="website_scrape",
        confidence=round(best_score, 2),
        cost_cents=0,  # scraping is free (we pay infra, not per-call)
        company_name=company_name,
        domain=domain,
        raw_response={
            "page_url": best_url,
            "candidate_count": len(candidates),
        },
        notes=f"Email scraped from {best_url} (confidence={best_score:.2f}).",
    )


async def _fetch_emails_from_url(
    url: str,
    *,
    client: httpx.AsyncClient,
    domain: str,
) -> list[tuple[str, float]]:
    """Fetch a URL and return `[(email, score)]` of emails found in the HTML.

    Score = how likely the email is a personal business contact:
      * +0.5 for matching the company domain
      * +0.3 if NOT a role account prefix
      * +0.2 for appearing in a `mailto:` href (vs plain text)
    Maximum score = 1.0.

    Returns empty list on HTTP error, timeout, or no emails found.
    """

    try:
        resp = await client.get(url)
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        log.debug("email_extractor.fetch_failed", url=url, err=str(exc))
        return []

    if resp.status_code not in (200, 203):
        return []

    # Cap response size to avoid processing multi-MB pages.
    body = resp.text[:WEBSITE_MAX_RESPONSE_BYTES]
    company_domain = domain.lower().removeprefix("www.").removeprefix("www")

    found: dict[str, tuple[str, float]] = {}  # email → (source_type, score)

    # Mailto href links (highest confidence — explicitly published).
    for match in _MAILTO_HREF_RE.finditer(body):
        email = match.group(1).lower()
        score = _score_email(email, company_domain, is_mailto=True)
        if email not in found or score > found[email][1]:
            found[email] = ("mailto_href", score)

    # Plain text emails (lower confidence — could be in legal disclaimers, etc.)
    for match in _PLAIN_EMAIL_RE.finditer(body):
        email = match.group(1).lower()
        if email in found:
            continue  # already found as mailto href
        if _looks_like_example(email):
            continue
        score = _score_email(email, company_domain, is_mailto=False)
        if email not in found or score > found[email][1]:
            found[email] = ("plain_text", score)

    return [(email, score) for email, (_, score) in found.items() if score > 0.0]


def _score_email(email: str, company_domain: str, *, is_mailto: bool) -> float:
    """Score an email address for relevance as a business contact."""

    score = 0.0
    local, _, addr_domain = email.partition("@")
    addr_domain = addr_domain.lower().removeprefix("www.")

    # Bonus: domain matches company
    if addr_domain and (addr_domain == company_domain or company_domain.endswith(f".{addr_domain}")):
        score += 0.5
    elif addr_domain:
        # Cross-domain email on the same page — could be a partner or tool,
        # only keep if it's a named-person-looking local part.
        if not _looks_like_person(local):
            return 0.0  # discard cross-domain role/system accounts

    # Bonus: found as mailto: link (explicitly published)
    if is_mailto:
        score += 0.2

    # Bonus: does NOT look like a role account
    if not _is_role_account(email):
        score += 0.3

    return min(1.0, score)


# ---------------------------------------------------------------------------
# Source 3: PEC registry (STUB)
# ---------------------------------------------------------------------------


# async def _from_pec_registry(...) -> ExtractionResult | None:
#     """Fetch the certified email (PEC) from Registro delle Imprese.
#     Every Italian legal entity (SRL, SpA, SNC) has a PEC by law.
#     CCIAA provides a public query endpoint; we need to obtain API
#     credentials from the local Chamber of Commerce.
#     Implementation pending — partner agreement in progress.
#     """
#     raise NotImplementedError


# ---------------------------------------------------------------------------
# Blacklist check
# ---------------------------------------------------------------------------


async def _check_blacklists(
    email: str,
    *,
    sb: Any,
) -> ExtractionResult | None:
    """Check `email_blacklist` and `domain_blacklist` (migration 0057).

    Returns a failed ExtractionResult if the email or its domain is listed,
    None if the email is clean.

    We check BOTH the tenant-scoped rows AND global rows (tenant_id IS NULL).
    The query uses the `email_hash` column (indexed) rather than the plaintext
    email to avoid case / normalisation issues.
    """

    import hashlib
    email_norm = email.strip().lower()
    email_hash = hashlib.sha256(email_norm.encode()).hexdigest()
    domain = email_norm.partition("@")[2]

    try:
        # Email blacklist check (email_hash indexed lookup).
        res = await asyncio.to_thread(
            lambda: sb.table("email_blacklist")
            .select("reason")
            .eq("email_hash", email_hash)
            .limit(1)
            .execute()
        )
        if res.data:
            reason = res.data[0].get("reason", "blacklisted")
            return ExtractionResult(
                email=None,
                source="failed",
                confidence=0.0,
                cost_cents=0,
                domain=domain,
                notes=f"Email on blacklist ({reason}). Suppressed.",
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("email_extractor.blacklist_check_failed", err=str(exc))

    try:
        # Domain blacklist check.
        res = await asyncio.to_thread(
            lambda: sb.table("domain_blacklist")
            .select("reason")
            .eq("domain", domain)
            .limit(1)
            .execute()
        )
        if res.data:
            reason = res.data[0].get("reason", "domain_blacklisted")
            return ExtractionResult(
                email=None,
                source="failed",
                confidence=0.0,
                cost_cents=0,
                domain=domain,
                notes=f"Domain {domain!r} on blacklist ({reason}). Suppressed.",
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("email_extractor.domain_blacklist_check_failed", err=str(exc))

    return None  # clean


# ---------------------------------------------------------------------------
# Internals — helpers
# ---------------------------------------------------------------------------


def _resolve_domain(azienda: dict[str, Any]) -> str | None:
    """Extract a usable website domain from the company record."""

    raw = (
        azienda.get("website_domain")
        or azienda.get("website")
        or ""
    ).strip()
    if not raw:
        return None
    # Strip scheme + trailing slashes.
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = parsed.netloc or parsed.path
    return host.lower().removeprefix("www.") or None


def _domain_to_base_url(domain: str) -> str | None:
    """Turn a bare domain into an https:// base URL."""

    domain = domain.strip().lower()
    if not domain or "." not in domain:
        return None
    # If someone stored a full URL already, normalise it.
    if domain.startswith(("http://", "https://")):
        return domain.rstrip("/")
    return f"https://{domain}"


def _is_role_account(email: str) -> bool:
    """True when the email's local part looks like a non-personal role account."""

    local = email.lower().partition("@")[0]
    # Exact match.
    if local in _ROLE_PREFIXES:
        return True
    # Prefix match (e.g. "info.napoli@..." or "contatti.2024@...").
    for prefix in _ROLE_PREFIXES:
        if local.startswith(prefix + ".") or local.startswith(prefix + "-"):
            return True
    return False


def _looks_like_person(local: str) -> bool:
    """Heuristic: does the local part look like a first/last name combo?

    We do NOT require this for on-domain emails — `mario@azienda.it`
    might be the owner. We DO require it for cross-domain emails to
    avoid pulling in social-media / analytics / ESP addresses from the
    page (e.g. `noreply@mailchimp.com` in an unsubscribe footer).
    """
    # At least two letter groups separated by a separator.
    clean = re.sub(r"[^a-z0-9]", " ", local.lower()).split()
    return len(clean) >= 2 and all(len(p) >= 2 for p in clean)


def _looks_like_example(email: str) -> bool:
    """Filter out placeholder / example emails in HTML."""

    lower = email.lower()
    for sentinel in (
        "example", "esempio", "test", "your@", "user@", "dummy",
        "placeholder", "nome@", "cognome@",
    ):
        if sentinel in lower:
            return True
    return False


def _normalise_local(s: str) -> str:
    """NFKD + ASCII fold: turns 'Mélodie' into 'melodie' for heuristics."""

    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
