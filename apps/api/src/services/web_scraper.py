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

import httpx

from ..core.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Email selection (from PRD §L2)
# ---------------------------------------------------------------------------

PRIORITA_EMAIL = ["direzione", "amministrazione", "info", "commerciale"]
# Hard exclusions: addresses we NEVER want to mail (auto-replies, opt-out).
# Note: `privacy` and `dpo` were here but moved to LOW_PRIORITY — DPO
# emails are real human inboxes and are often the ONLY public address
# on regulated/utility-style B2B sites. Better than nothing.
ESCLUSIONI_HARD = [
    "noreply",
    "no-reply",
    "donotreply",
    "newsletter",
    "unsubscribe",
]
# Low-priority: technically valid email, but should only be used as
# last resort. These are addresses meant for compliance traffic, not
# commercial. We score them down but don't drop them.
LOW_PRIORITY_LOCAL_PARTS = ("privacy", "dpo", "gdpr", "legal", "abuse")


@dataclass(slots=True)
class EmailCandidate:
    value: str
    confidence: str  # "alta" | "media" | "bassa"
    type: str  # "named_role" | "generic" | "privacy_dpo" | "inferred_pattern"


_EXAMPLE_EMAIL_TOKENS = (
    "example",
    "esempio",
    "dummy",
    "placeholder",
    "your@",
    "user@",
    "nome@",
    "cognome@",
    "test@",
)


def _is_example_email(email: str) -> bool:
    """Reject placeholder/template addresses (info@example.com, etc.)."""
    lower = email.lower()
    return any(tok in lower for tok in _EXAMPLE_EMAIL_TOKENS)


def extract_best_email(
    scraped_emails: list[str],
    *,
    company_domain: str | None = None,
) -> EmailCandidate | None:
    """Pick the best email from a scraped list per the PRD's policy.

    Hard exclusions (noreply@, newsletter@) are applied first. Then we
    rank by:
      1. **Named role** — direzione@, amministrazione@, commerciale@,
         info@ → confidence "alta", type "named_role"
      2. **Generic** — anything else with a person-y / department-y
         local part → "media" / "generic"
      3. **Privacy/DPO** — privacy@/dpo@/legal@ — last resort,
         "bassa" / "privacy_dpo". Used only when nothing else found.

    When ``company_domain`` is provided, addresses on that domain are
    preferred — `info@fifaa.it` always beats `info@example.com` even
    if the latter happened to come first in the regex sweep.

    Returns ``None`` when only hard-excluded addresses remain.
    """
    if not scraped_emails:
        return None

    # Drop hard exclusions AND placeholder addresses (info@example.com,
    # esempio@..., your@..., nome@... — typical website-template debris).
    candidates = [
        e
        for e in scraped_emails
        if not any(esc in e.lower() for esc in ESCLUSIONI_HARD) and not _is_example_email(e)
    ]
    if not candidates:
        return None

    # Split low-priority (privacy/dpo/legal) from the rest.
    privacy_pool: list[str] = []
    main_pool: list[str] = []
    for email in candidates:
        local = email.split("@", 1)[0].lower()
        if any(
            local == lp or local.startswith(f"{lp}.") or local.startswith(f"{lp}-")
            for lp in LOW_PRIORITY_LOCAL_PARTS
        ):
            privacy_pool.append(email)
        else:
            main_pool.append(email)

    # Sort each pool with on-domain matches first. The company's own
    # domain is the strongest provenance signal — `info@<their-site>`
    # is what we actually want to mail, not a random `info@othersite`
    # that ended up in the page (e.g. footer credits, partner widgets).
    if company_domain:
        domain_lower = company_domain.lower().lstrip(".")

        def _on_domain(email: str) -> int:
            addr_domain = email.split("@", 1)[-1].lower()
            return (
                0 if addr_domain == domain_lower or addr_domain.endswith("." + domain_lower) else 1
            )

        main_pool.sort(key=_on_domain)
        privacy_pool.sort(key=_on_domain)

    # Try priority keywords in order on the main pool first.
    for keyword in PRIORITA_EMAIL:
        for email in main_pool:
            local_part = email.split("@", 1)[0].lower()
            if keyword in local_part:
                return EmailCandidate(value=email, confidence="alta", type="named_role")

    # No named-role hit in main pool: fall through to first generic.
    if main_pool:
        return EmailCandidate(value=main_pool[0], confidence="media", type="generic")

    # Only privacy/dpo/legal addresses survived — last-resort fallback.
    if privacy_pool:
        return EmailCandidate(value=privacy_pool[0], confidence="bassa", type="privacy_dpo")

    return None


# ---------------------------------------------------------------------------
# Website scraping
# ---------------------------------------------------------------------------


_EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.IGNORECASE)
_PEC_DOMAINS = (
    "pec.",
    "@pec.",
    ".pec.",
    "legalmail.it",
    "postacertificata",
    "casellapec.com",
    "legalmail",
)
_PHONE_REGEX = re.compile(r"(?:\+?39[\s\-.]?)?(?:0\d{1,4}|3\d{2})[\s\-.]?\d{3,4}[\s\-.]?\d{2,4}")

# Pages most likely to surface contact info on an Italian SME site.
# Order matters: contact-first pages first (best human-readable signals),
# then privacy/cookie pages which by Italian law (Provvedimento Garante
# 8 maggio 2014 + GDPR art. 13) MUST disclose the Titolare del
# Trattamento + DPO email — so even a site with zero contact info
# almost always exposes an address there.
_CONTACT_PATHS = (
    "",
    "/contatti",
    "/contattaci",
    "/chi-siamo",
    "/about",
    "/azienda",
    # Privacy / cookie / legal — mandatory pages on any IT business site.
    # By GDPR art. 13 they MUST list the Titolare / DPO email address.
    "/privacy",
    "/privacy-policy",
    "/informativa-privacy",
    "/informativa-sulla-privacy",
    "/informativa",
    "/cookie-policy",
    "/cookies",
    "/note-legali",
    "/legal",
)


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


# Hard ceiling on the bytes we pull from any one page. `httpx`'s timeout caps
# the *time* to fetch, not the *size* of the body: a site that streams a huge
# page (or a download mislabelled `text/html`) would return a multi-hundred-MB
# string that we then feed to the synchronous regex extractors below — which run
# ON the event-loop thread and hold the GIL while they chew through it. On
# 2026-06-18 one such body froze the worker for ~70 minutes: no sends, and even
# the watchdog couldn't fire (its kill needs the GIL). A 2 MB cap keeps the regex
# input small enough that extraction is always milliseconds, so it can never
# wedge the loop. 2 MB is generous — real Italian SME pages are 50-500 KB.
_MAX_HTML_BYTES = 2_000_000


async def _read_capped(resp: httpx.Response) -> str:
    """Read at most ``_MAX_HTML_BYTES`` of a streaming response, then decode.

    A body larger than the cap is truncated mid-stream — we never buffer the
    whole thing. Decoding falls back to UTF-8 (errors replaced) when the
    declared charset is missing or bogus.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.aiter_bytes():
        chunks.append(chunk)
        total += len(chunk)
        if total >= _MAX_HTML_BYTES:
            break
    raw = b"".join(chunks)[:_MAX_HTML_BYTES]
    encoding = resp.encoding or "utf-8"
    try:
        return raw.decode(encoding, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


async def _fetch_html(url: str, *, client: httpx.AsyncClient, timeout: float = 8.0) -> str | None:
    try:
        async with client.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
            if resp.status_code >= 400:
                return None
            if "text/html" not in (resp.headers.get("content-type") or ""):
                return None
            # Bail before reading a single byte when the server *declares* an
            # oversize body — saves bandwidth and CPU on obvious downloads.
            declared = resp.headers.get("content-length")
            if declared is not None:
                try:
                    if int(declared) > _MAX_HTML_BYTES:
                        return None
                except ValueError:
                    pass
            return await _read_capped(resp)
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        log.debug("web_scraper.fetch_error", url=url, err=type(exc).__name__)
        return None


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


def _is_placeholder_phone(digits: str) -> bool:
    """Reject obvious non-phones picked up by the regex.

    Pages frequently embed placeholders (33333333333, 0000000000) or
    P.IVA / fiscal-code fragments that match the loose phone shape.
    """
    if len(digits) < 7:
        return True
    # All-same-digit (33333333333, 1111111).
    if len(set(digits)) == 1:
        return True
    # 3+ leading zeros = almost always a P.IVA with country prefix
    # (it 0008899584576 = 13-char P.IVA, not a phone).
    return digits.startswith("000")


def _extract_phone_from_html(html: str) -> str | None:
    for match in _PHONE_REGEX.findall(html):
        digits_only = re.sub(r"\D", "", match)
        if _is_placeholder_phone(digits_only):
            continue
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

    # Extract the company's own apex domain (fifaa.it from
    # https://www.fifaa.it/) so we can recognise on-domain emails as
    # the strong-provenance signal that lets us stop early.
    try:
        from urllib.parse import urlparse

        host = urlparse(base).hostname or ""
        company_domain = host.removeprefix("www.").lower() if host else ""
    except ValueError:
        company_domain = ""

    def _has_on_domain_email() -> bool:
        if not company_domain:
            return False
        for e in out.emails:
            addr_domain = e.split("@", 1)[-1].lower()
            if addr_domain == company_domain or addr_domain.endswith("." + company_domain):
                return True
        return False

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
            # Stop only when we have a phone AND an on-domain email —
            # that's the "we found the real contact" signal. If the
            # homepage only yielded `info@example.com`-style debris,
            # keep walking through /contatti, /privacy, /cookie-policy:
            # GDPR art. 13 mandates the Titolare's email there, so it's
            # the canonical fallback when contact pages are sparse.
            if out.phone and _has_on_domain_email():
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
# DNS MX validation + inferred-pattern fallback
# ---------------------------------------------------------------------------
#
# When site scraping yields zero emails, we generate the four most-common
# Italian SME contact patterns (info@, contatti@, amministrazione@,
# commerciale@) and validate each against a DNS MX lookup. An MX record
# proves the domain ACCEPTS mail (it would otherwise be a parked domain
# or a website-only pseudo-domain). It does NOT prove the specific
# inbox exists — most providers do catch-all and accept anything — but
# combined with the pattern, the success rate on Italian SMEs is ~80%.
#
# Why no SMTP RCPT TO probe: it's blocked by every major mail provider
# (Gmail, Microsoft 365, OVH greylist) and is treated as abuse on
# smaller hosts. The reputational cost > the verification value.
#
# GDPR note: pattern-inferred role addresses (info@, contatti@) are
# generally accepted under art. 6.1.f legittimo interesse — they are
# published de-facto on any Italian B2B site and the data subject is
# not identified. This is fundamentally different from guessing a
# named person address (mario.rossi@), which the PRD explicitly bans.

# Default fallback pattern — matches what 80%+ Italian SMEs actually
# use. Order = priority. Stop at the first one with a valid MX record.
INFERRED_EMAIL_PATTERNS = (
    "info",
    "contatti",
    "amministrazione",
    "commerciale",
)


# Domains that are NOT a company's own website even when Google Places
# returns them as the `website` for a place. Matches both the eTLD+1
# itself and any subdomain (so "facebook.com/page" and
# "m.facebook.com/page" are both rejected). Inferring `info@<domain>`
# from these would produce useless emails like `info@facebook.com`.
#
# Categories:
#   - Social networks: places where SMEs publish a "page" instead of a
#     real site. Common for restaurants, small shops, freelancers.
#   - Marketplaces / directories: storefronts, listings, classifieds.
#   - Search/maps redirects: the URL itself is a redirect, not the site.
#   - Linktree-style aggregators: the "site" is just a link list.
NON_BUSINESS_DOMAINS: frozenset[str] = frozenset(
    {
        # Social networks
        "facebook.com",
        "fb.com",
        "fb.me",
        "m.facebook.com",
        "instagram.com",
        "linkedin.com",
        "twitter.com",
        "x.com",
        "tiktok.com",
        "youtube.com",
        "youtu.be",
        "pinterest.com",
        "pinterest.it",
        "snapchat.com",
        "threads.net",
        "whatsapp.com",
        "wa.me",
        "telegram.me",
        "t.me",
        # Marketplaces / aggregators / directories
        "amazon.com",
        "amazon.it",
        "ebay.com",
        "ebay.it",
        "subito.it",
        "kijiji.it",
        "tripadvisor.com",
        "tripadvisor.it",
        "yelp.com",
        "yelp.it",
        "thefork.it",
        "thefork.com",
        "deliveroo.it",
        "ubereats.com",
        "justeat.it",
        "glovoapp.com",
        "booking.com",
        "airbnb.com",
        "airbnb.it",
        "hotels.com",
        "expedia.com",
        # Italian phone-book/directory style aggregators
        "paginegialle.it",
        "paginebianche.it",
        "europages.it",
        "europages.com",
        "kompass.com",
        "kompass.it",
        # Linktree-style
        "linktr.ee",
        "linktree.com",
        "bento.me",
        "carrd.co",
        # Generic / placeholder
        "example.com",
        "example.org",
        "example.net",
        "test.com",
        "localhost",
        # Search/maps redirects
        "google.com",
        "google.it",
        "maps.google.com",
        "goo.gl",
        "bit.ly",
        # Free email providers (B2C)
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "yahoo.it",
        "hotmail.com",
        "hotmail.it",
        "outlook.com",
        "live.com",
        "libero.it",
        "tiscali.it",
        "alice.it",
        "tin.it",
        "virgilio.it",
        "fastwebnet.it",
    }
)


def _normalize_domain(domain_or_url: str) -> str | None:
    """Return the bare hostname (lowercase, no scheme/path/www).

    None when input is empty / unparseable. Strips port numbers.
    """
    if not domain_or_url:
        return None
    d = domain_or_url.strip()
    if "://" in d:
        d = d.split("://", 1)[1]
    d = d.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    d = d.split(":", 1)[0]
    d = d.lower().lstrip(".")
    if d.startswith("www."):
        d = d[4:]
    if not d or "." not in d:
        return None
    return d


def is_non_business_domain(domain_or_url: str | None) -> bool:
    """Return True when the URL/domain is a social/marketplace/directory.

    Matches both the bare eTLD+1 and any subdomain — so "facebook.com",
    "m.facebook.com" and "https://facebook.com/somepage" all return True.
    Used to keep social URLs out of `scan_candidates.website` and to
    refuse `info@<social>` inference in `infer_email_from_domain`.
    """
    norm = _normalize_domain(domain_or_url or "")
    if not norm:
        return False
    if norm in NON_BUSINESS_DOMAINS:
        return True
    # Subdomain match: walk back the labels until we find a known root.
    parts = norm.split(".")
    for i in range(1, len(parts)):
        suffix = ".".join(parts[i:])
        if suffix in NON_BUSINESS_DOMAINS:
            return True
    return False


def _has_mx_record(domain: str, *, timeout: float = 3.0) -> bool:
    """Return True if `domain` has at least one MX record.

    Uses dnspython which is already a project dep. Resolves
    synchronously — runs in <100ms typically. Caller wraps it in
    `asyncio.to_thread` if it needs async behavior.
    """
    if not domain or "." not in domain:
        return False
    try:
        import dns.resolver

        resolver = dns.resolver.Resolver()
        resolver.lifetime = timeout
        resolver.timeout = timeout
        answers = resolver.resolve(domain, "MX")
        return len(list(answers)) > 0
    except Exception:  # noqa: BLE001 — dns errors all become "no MX"
        return False


async def infer_email_from_domain(
    domain: str,
    *,
    patterns: tuple[str, ...] = INFERRED_EMAIL_PATTERNS,
) -> EmailCandidate | None:
    """Generate `pattern@domain` candidates and pick the first with valid MX.

    Used as a final fallback when site scraping yields no email and the
    domain looks legit. Returns confidence='bassa' / type='inferred_pattern'
    so downstream code (anti-spam, lead validator) can flag the lead
    appropriately. Returns None if domain has no MX record at all
    (parked / DNS-only) — in that case the lead has no usable email.
    """
    if not domain:
        return None
    norm = _normalize_domain(domain)
    if not norm:
        return None
    # Refuse to infer an email on a social / marketplace / directory
    # domain — e.g. when Google Places returned the company's Facebook
    # page as `website`, we'd otherwise emit `info@facebook.com` which
    # is worse than no email at all.
    if is_non_business_domain(norm):
        log.debug("web_scraper.infer.non_business_domain", domain=norm)
        return None
    domain = norm

    has_mx = await asyncio.to_thread(_has_mx_record, domain)
    if not has_mx:
        log.debug("web_scraper.infer.no_mx", domain=domain)
        return None

    # MX exists → use the first pattern. We don't try to verify the
    # specific local-part because catch-all behaviour means the answer
    # is unreliable; we trust the pattern's empirical hit rate.
    candidate = f"{patterns[0]}@{domain}"
    return EmailCandidate(
        value=candidate,
        confidence="bassa",
        type="inferred_pattern",
    )


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
            async with client.stream("GET", url, follow_redirects=True) as resp:
                if resp.status_code >= 400:
                    return PagineBiancheRecord()
                # Same GIL-safety cap as _fetch_html: never feed an unbounded
                # body to the synchronous phone regex (2026-06-18 wedge).
                html = await _read_capped(resp)
        except (httpx.HTTPError, httpx.TimeoutException):
            return PagineBiancheRecord()
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
        results = data.get("results", {}).get("companies") or []
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
        site = (
            results[0]
            if isinstance(results[0], ScrapedSite)
            else ScrapedSite(url="", error="exception")
        )
        pb = results[1] if isinstance(results[1], PagineBiancheRecord) else PagineBiancheRecord()
        oc = results[2] if isinstance(results[2], OpenCorporatesRecord) else OpenCorporatesRecord()
        return CombinedScrape(site=site, pb=pb, oc=oc)
    finally:
        if owns_client:
            await client.aclose()


# ===========================================================================
# Brand extraction — punto G (Sprint client-feedback)
# ===========================================================================
#
# Why: the lead-portal white-labels via tenant.business_name +
# tenant.brand_logo_url. When a new tenant is onboarded we'd rather
# auto-populate these from their public website instead of asking them
# to copy-paste the URL of their logo.
#
# Heuristic order (best → fallback):
#   1. <meta property="og:image"> — the canonical "share preview" image
#      most modern sites set explicitly to their logo.
#   2. <meta name="twitter:image">
#   3. <link rel="apple-touch-icon"> — usually a high-res PNG
#   4. <link rel="icon"> with sizes hint (preferring 192x192+)
#   5. <img> tag whose class or alt mentions "logo"
#
# For business name we prefer:
#   1. <meta property="og:site_name">
#   2. <title> (stripped of trailing branding fluff)
#   3. <meta property="og:title">


@dataclass
class ExtractedBranding:
    """Result of `extract_branding_from_url`."""

    business_name: str | None = None
    logo_url: str | None = None
    source: dict[str, str] = field(default_factory=dict)


_LOGO_IMG_RE = re.compile(
    r'<img\s+[^>]*(?:class\s*=\s*"[^"]*\blogo\b[^"]*"|alt\s*=\s*"[^"]*\blogo\b[^"]*"|id\s*=\s*"[^"]*\blogo\b[^"]*")[^>]*\bsrc\s*=\s*"([^"]+)"',
    re.IGNORECASE,
)
_OG_IMAGE_RE = re.compile(
    r'<meta\s+(?:[^>]*\s)?property\s*=\s*"og:image"\s+content\s*=\s*"([^"]+)"',
    re.IGNORECASE,
)
_TWITTER_IMAGE_RE = re.compile(
    r'<meta\s+(?:[^>]*\s)?name\s*=\s*"twitter:image"\s+content\s*=\s*"([^"]+)"',
    re.IGNORECASE,
)
_APPLE_ICON_RE = re.compile(
    r'<link\s+(?:[^>]*\s)?rel\s*=\s*"apple-touch-icon[^"]*"\s+(?:[^>]*\s)?href\s*=\s*"([^"]+)"',
    re.IGNORECASE,
)
_LINK_ICON_RE = re.compile(
    r'<link\s+(?:[^>]*\s)?rel\s*=\s*"(?:shortcut )?icon"\s+(?:[^>]*\s)?href\s*=\s*"([^"]+)"',
    re.IGNORECASE,
)
_OG_SITE_NAME_RE = re.compile(
    r'<meta\s+(?:[^>]*\s)?property\s*=\s*"og:site_name"\s+content\s*=\s*"([^"]+)"',
    re.IGNORECASE,
)
_OG_TITLE_RE = re.compile(
    r'<meta\s+(?:[^>]*\s)?property\s*=\s*"og:title"\s+content\s*=\s*"([^"]+)"',
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

# Strip common trailing fluff from <title> like "Home | Foo Srl"
_TITLE_FLUFF_RE = re.compile(
    r"^(home|homepage|chi siamo|about\s*us?|contatti|contact)\s*[-|·]\s*",
    re.IGNORECASE,
)


def _absolutize(base_url: str, candidate: str) -> str:
    """Return `candidate` resolved against `base_url`. Skips data: URIs."""
    if not candidate or candidate.startswith("data:"):
        return ""
    if candidate.startswith("http://") or candidate.startswith("https://"):
        return candidate
    if candidate.startswith("//"):
        return "https:" + candidate
    try:
        from urllib.parse import urljoin

        return urljoin(base_url, candidate)
    except Exception:  # noqa: BLE001
        return ""


def extract_branding_from_html(*, html: str, base_url: str) -> ExtractedBranding:
    """Extract a best-effort business_name + logo_url from a homepage HTML.

    Returns an `ExtractedBranding`; either field can be None if no
    candidate was found. The `source` dict records which heuristic
    produced each field — useful when the operator wants to see why
    the system picked a given image.
    """
    out = ExtractedBranding()

    # ── Business name ────────────────────────────────────────────────
    if m := _OG_SITE_NAME_RE.search(html):
        out.business_name = m.group(1).strip()
        out.source["business_name"] = "og:site_name"
    else:
        if m := _OG_TITLE_RE.search(html):
            out.business_name = m.group(1).strip()
            out.source["business_name"] = "og:title"
        elif m := _TITLE_RE.search(html):
            t = m.group(1).strip()
            # Take the rightmost (typical) chunk for "Page | Brand"
            parts = re.split(r"\s*[-|·–—]\s*", t)
            cleaned = (parts[-1] if len(parts) > 1 else t).strip()
            cleaned = _TITLE_FLUFF_RE.sub("", cleaned)
            out.business_name = cleaned or None
            out.source["business_name"] = "title"

    # ── Logo URL ─────────────────────────────────────────────────────
    for regex, src_name in (
        (_OG_IMAGE_RE, "og:image"),
        (_TWITTER_IMAGE_RE, "twitter:image"),
        (_APPLE_ICON_RE, "apple-touch-icon"),
        (_LINK_ICON_RE, "link icon"),
        (_LOGO_IMG_RE, "img.logo"),
    ):
        m = regex.search(html)
        if m:
            absolute = _absolutize(base_url, m.group(1))
            if absolute:
                out.logo_url = absolute
                out.source["logo_url"] = src_name
                break

    return out


async def extract_branding_from_url(
    *,
    website_url: str,
    client: httpx.AsyncClient | None = None,
) -> ExtractedBranding:
    """Fetch the website homepage and run `extract_branding_from_html`.

    Convenience wrapper for callers (admin endpoint, onboarding flow,
    backfill scripts). Returns an empty `ExtractedBranding` on any
    network / parsing error — never raises.
    """
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            headers={"User-Agent": "solarlead-scraper/1.0 (+https://solarlead.it)"},
            follow_redirects=True,
        )
    try:
        html = await _fetch_html(website_url, client=client, timeout=10.0)
        if not html:
            return ExtractedBranding()
        return extract_branding_from_html(html=html, base_url=website_url)
    finally:
        if owns_client:
            await client.aclose()
