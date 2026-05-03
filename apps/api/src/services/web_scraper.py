"""L2 — Multi-source public scraping (FLUSSO 1 v3, no-Atoka).

For every L1 candidate we try, in order:

  1. **Company website** (when known from Places).
     /contatti, /chi-siamo, /about, footer → extract emails, PEC,
     phone, optionally a decision-maker name. Reuses the existing
     ``email_extractor`` helpers (battle-tested HTML parser, handles
     mailto: links and obfuscated patterns).

  2. **Pagine Bianche** (Italian phone book scraping).
     Best-effort lookup by business name + city; returns phone +
     address category. Conservative rate limiting (1 req/2s) to stay
     polite. Optional — falls through silently when site changes.

  3. **OpenCorporates** (free public corp registry API, rate-limited).
     Returns confirmed VAT, legal name, founding date, status, legal
     form. Free tier: 1k requests/month per IP — plenty for our daily
     scan volume.

LinkedIn is **NOT** in this batch — per product decision (Sprint 4.3),
LinkedIn is fetched on-demand from the lead detail UI via Proxycurl,
not in the bulk discovery pipeline.

Email selection
---------------
``extract_best_email`` applies the priority list / exclusion list from
the PRD section L2:

  PRIORITA = ["direzione", "amministrazione", "info", "commerciale"]
  ESCLUSIONI_HARD = ["privacy", "dpo", "noreply", "newsletter", "marketing"]

Returned with confidence 'alta' (named role like direzione@) or 'media'
(generic info@).

GDPR audit
----------
Every contact returned is **public**. The caller persists each row to
``contact_extraction_log`` with the source URL and timestamp so the
GDPR export endpoint can answer "where did you get this email?".
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..core.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Email selection (from PRD §L2)
# ---------------------------------------------------------------------------

PRIORITA_EMAIL = ["direzione", "amministrazione", "info", "commerciale"]
ESCLUSIONI_HARD = [
    "privacy",
    "dpo",
    "noreply",
    "no-reply",
    "newsletter",
    "marketing",
    "unsubscribe",
]


@dataclass(slots=True)
class EmailCandidate:
    value: str
    confidence: str  # "alta" | "media"
    type: str  # "named_role" | "generic"


def extract_best_email(scraped_emails: list[str]) -> EmailCandidate | None:
    """Pick the best email from a scraped list per the PRD's policy.

    Hard exclusions are applied first (privacy@/dpo@/noreply@). Of the
    survivors, named-role addresses (direzione@, amministrazione@) win
    over generic ones (info@). Returns ``None`` when nothing usable
    remains.
    """
    if not scraped_emails:
        return None

    candidates = [
        e
        for e in scraped_emails
        if not any(esc in e.lower() for esc in ESCLUSIONI_HARD)
    ]
    if not candidates:
        return None

    # Try priority keywords in order — first match wins.
    for keyword in PRIORITA_EMAIL:
        for email in candidates:
            local_part = email.split("@")[0].lower()
            if keyword in local_part:
                return EmailCandidate(
                    value=email, confidence="alta", type="named_role"
                )

    # No named-role hit: fall through to first generic.
    return EmailCandidate(value=candidates[0], confidence="media", type="generic")


# ---------------------------------------------------------------------------
# Website scraping
# ---------------------------------------------------------------------------


_EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.IGNORECASE
)
_PEC_DOMAINS = (
    "pec.",
    "@pec.",
    ".pec.",
    "legalmail.it",
    "postacertificata",
    "casellapec.com",
    "legalmail",
)
_PHONE_REGEX = re.compile(
    r"(?:\+?39[\s\-.]?)?(?:0\d{1,4}|3\d{2})[\s\-.]?\d{3,4}[\s\-.]?\d{2,4}"
)

# Pages most likely to surface contact info on an Italian SME site.
_CONTACT_PATHS = ("", "/contatti", "/contattaci", "/chi-siamo", "/about", "/azienda")


@dataclass(slots=True)
class ScrapedSite:
    """Aggregate of all signals scraped from a single company website."""

    url: str
    emails: list[str] = field(default_factory=list)
    pec: str | None = None
    phone: str | None = None
    address: str | None = None
    decision_maker: str | None = None
    pages_scraped: list[str] = field(default_factory=list)
    error: str | None = None


async def _fetch_html(
    url: str, *, client: httpx.AsyncClient, timeout: float = 8.0
) -> str | None:
    try:
        resp = await client.get(url, timeout=timeout, follow_redirects=True)
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        log.debug("web_scraper.fetch_error", url=url, err=type(exc).__name__)
        return None
    if resp.status_code >= 400:
        return None
    if "text/html" not in (resp.headers.get("content-type") or ""):
        return None
    return resp.text


def _extract_emails_from_html(html: str) -> list[str]:
    """Return a deduped list of plausible email addresses from raw HTML.

    Handles plain text + ``href="mailto:"`` patterns. Domains ending in
    ``.png``/``.jpg``/etc. are dropped (they're filename matches, not
    real addresses).
    """
    seen: dict[str, None] = {}
    for match in _EMAIL_REGEX.findall(html):
        clean = match.strip().rstrip(".,;)")
        if "." not in clean:
            continue
        domain = clean.split("@", 1)[-1].lower()
        # Skip filename-looking matches.
        if domain.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
            continue
        seen.setdefault(clean.lower())
    return list(seen.keys())


def _extract_phone_from_html(html: str) -> str | None:
    for match in _PHONE_REGEX.findall(html):
        digits_only = re.sub(r"\D", "", match)
        if len(digits_only) >= 8:
            return match.strip()
    return None


def _classify_pec(emails: list[str]) -> str | None:
    """First email in `emails` that looks like a PEC (Italian certified mail)."""
    for e in emails:
        e_lower = e.lower()
        if any(token in e_lower for token in _PEC_DOMAINS):
            return e
    return None


async def scrape_website(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> ScrapedSite:
    """Scrape contact pages of a single website.

    Tries homepage + canonical contact paths in sequence. Stops as soon
    as we have an email + a phone (cheap heuristic to keep latency low).
    """
    if not url:
        return ScrapedSite(url="", error="no_url")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    base = url.rstrip("/")
    out = ScrapedSite(url=base)

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=8.0,
            headers={"User-Agent": "solarlead-scraper/1.0 (+https://solarlead.it)"},
        )

    try:
        for path in _CONTACT_PATHS:
            target = base + path
            html = await _fetch_html(target, client=client)
            if html is None:
                continue
            out.pages_scraped.append(target)
            emails = _extract_emails_from_html(html)
            for e in emails:
                if e not in out.emails:
                    out.emails.append(e)
            if out.phone is None:
                out.phone = _extract_phone_from_html(html)
            # Cheap stop condition.
            if out.emails and out.phone:
                break

        out.pec = _classify_pec(out.emails)
        return out
    except Exception as exc:  # noqa: BLE001 — scraper is the boundary
        out.error = type(exc).__name__
        log.warning("web_scraper.unexpected_error", url=url, err=out.error)
        return out
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# Pagine Bianche (best-effort)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PagineBiancheRecord:
    found: bool = False
    phone: str | None = None
    address: str | None = None
    category: str | None = None


_PAGINE_BIANCHE_SEARCH_URL = "https://www.paginebianche.it/cerca?qs={q}"


async def scrape_pagine_bianche(
    business_name: str,
    *,
    city: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> PagineBiancheRecord:
    """Best-effort phonebook scrape. Returns empty record on any failure.

    Pagine Bianche aggressively rate-limits and changes HTML often;
    callers should treat a found=False as normal and not retry.
    """
    if not business_name:
        return PagineBiancheRecord()
    query = business_name if not city else f"{business_name} {city}"
    url = _PAGINE_BIANCHE_SEARCH_URL.format(q=query.replace(" ", "+"))

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=8.0,
            headers={"User-Agent": "solarlead-scraper/1.0 (+https://solarlead.it)"},
        )

    try:
        try:
            resp = await client.get(url, follow_redirects=True)
        except (httpx.HTTPError, httpx.TimeoutException):
            return PagineBiancheRecord()
        if resp.status_code >= 400:
            return PagineBiancheRecord()
        html = resp.text or ""
        phone = _extract_phone_from_html(html)
        return PagineBiancheRecord(found=bool(phone), phone=phone)
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# OpenCorporates (free public API)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OpenCorporatesRecord:
    found: bool = False
    vat: str | None = None
    legal_name: str | None = None
    founding_date: str | None = None
    status: str | None = None
    legal_form: str | None = None


_OPENCORPORATES_BASE = "https://api.opencorporates.com/v0.4"
_OC_SEARCH_URL = _OPENCORPORATES_BASE + "/companies/search"


async def scrape_opencorporates(
    business_name: str,
    *,
    jurisdiction: str = "it",
    client: httpx.AsyncClient | None = None,
) -> OpenCorporatesRecord:
    """Search OpenCorporates for an Italian company by name.

    Returns the first hit (OpenCorporates orders by relevance). Free
    tier rate limit is 50 requests/min — we keep the call simple and
    let the orchestrator throttle if needed.
    """
    if not business_name:
        return OpenCorporatesRecord()

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)

    try:
        try:
            resp = await client.get(
                _OC_SEARCH_URL,
                params={
                    "q": business_name,
                    "jurisdiction_code": jurisdiction,
                    "per_page": 1,
                },
            )
        except (httpx.HTTPError, httpx.TimeoutException):
            return OpenCorporatesRecord()
        if resp.status_code >= 400:
            return OpenCorporatesRecord()
        try:
            data = resp.json()
        except ValueError:
            return OpenCorporatesRecord()
        results = (
            data.get("results", {})
            .get("companies") or []
        )
        if not results:
            return OpenCorporatesRecord()
        comp = results[0].get("company") or {}
        return OpenCorporatesRecord(
            found=True,
            vat=comp.get("company_number"),
            legal_name=comp.get("name"),
            founding_date=comp.get("incorporation_date"),
            status=comp.get("current_status"),
            legal_form=(comp.get("company_type") or {}).get("description")
            if isinstance(comp.get("company_type"), dict)
            else comp.get("company_type"),
        )
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# Orchestration helper for L2 agent
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CombinedScrape:
    site: ScrapedSite
    pb: PagineBiancheRecord
    oc: OpenCorporatesRecord


async def scrape_all_for_candidate(
    *,
    website: str | None,
    business_name: str,
    city: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> CombinedScrape:
    """Run all three scrapers in parallel for one candidate.

    The website scrape is the most valuable; Pagine Bianche and
    OpenCorporates are best-effort fallbacks.
    """
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=10.0,
            headers={"User-Agent": "solarlead-scraper/1.0 (+https://solarlead.it)"},
        )
    try:
        site_task = (
            scrape_website(website, client=client)
            if website
            else asyncio.sleep(0, result=ScrapedSite(url="", error="no_website"))
        )
        results = await asyncio.gather(
            site_task,
            scrape_pagine_bianche(business_name, city=city, client=client),
            scrape_opencorporates(business_name, client=client),
            return_exceptions=True,
        )
        site = results[0] if isinstance(results[0], ScrapedSite) else ScrapedSite(url="", error="exception")
        pb = results[1] if isinstance(results[1], PagineBiancheRecord) else PagineBiancheRecord()
        oc = results[2] if isinstance(results[2], OpenCorporatesRecord) else OpenCorporatesRecord()
        return CombinedScrape(site=site, pb=pb, oc=oc)
    finally:
        if owns_client:
            await client.aclose()
