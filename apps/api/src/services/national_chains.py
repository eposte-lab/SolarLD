"""National retail/hotel/dealer CHAIN detection.

A national chain's web domain resolves to a corporate HQ contact (a Product
Owner, an Optical Manager, a national purchasing office) — NOT the person who
decides solar for a single local store. Pitching them is futile and, worse,
many store-leads collapse onto the *same* HQ mailbox (duplicate sends). So we
exclude national chains everywhere: discovery/promotion (don't create the lead),
the dry-run sample, and the send path (never email an existing chain lead).

This is distinct from ``web_scraper.NON_BUSINESS_DOMAINS`` (social networks,
marketplaces, free webmail) — chains ARE businesses, just the wrong *target*.

Matching is precision-first to avoid false-positiving legitimate local SMEs:
  - exact domain hits (``conad.it``), plus
  - distinctive brand tokens matched as whole dot/hyphen-separated components of
    the domain or words of the business name (so ``clienti-multicedi.com`` and
    "Conad City Napoli" match, but "Cooperativa Agricola" does not — ambiguous
    words like *coop*/*sigma* are deliberately NOT tokens; those rely on the
    exact-domain list).
"""

from __future__ import annotations

import re
import unicodedata

# Exact domains observed / known for national chains (registrable form, no www).
NATIONAL_CHAIN_DOMAINS: frozenset[str] = frozenset(
    {
        # grocery / discount
        "conad.it",
        "despar.it",
        "lidl.it",
        "eurospin.it",
        "crai-supermercati.it",
        "sole365.it",
        "e-coop.it",
        "coopmastercampania.it",
        "supermercatideco.it",
        "supersigma.com",
        "multicedi.com",
        "clienti-multicedi.com",
        "cedispa.com",
        "disisacentrosud.it",
        "md-spa.it",
        "pennymarket.it",
        # electronics / retail
        "unieuro.it",
        "upim.it",
        "mediaworld.it",
        "euronics.it",
        # automotive (national brands / captive networks)
        "peugeot.it",
        "stellantisandyou.com",
        # hotel groups
        "hilton.com",
        "marriott.com",
        "accor.com",
        "nh-hotels.com",
        "starhotels.it",
        "bwhhotels.com",
    }
)

# Distinctive brand tokens — matched as a whole component of the domain
# (split on non-alphanumeric) OR a whole word of the business name. Ambiguous
# words (coop, sigma, sara, deco, sole, md) are intentionally EXCLUDED to avoid
# hitting legitimate local SMEs; those are covered by NATIONAL_CHAIN_DOMAINS.
NATIONAL_CHAIN_TOKENS: frozenset[str] = frozenset(
    {
        "conad",
        "eurospin",
        "lidl",
        "despar",
        "sole365",
        "multicedi",
        "unieuro",
        "upim",
        "decathlon",
        "carrefour",
        "esselunga",
        "ipercoop",
        "pennymarket",
        "mediaworld",
        "euronics",
        "trony",
        "leroymerlin",
        "bricoman",
        "decathlonitalia",
        "hilton",
        "marriott",
        "accor",
        "starhotels",
        "nhhotels",
        "bestwestern",
        "peugeot",
        "stellantis",
    }
)


def _ascii_lower(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()


def _norm_domain(domain: str | None) -> str:
    if not domain:
        return ""
    d = _ascii_lower(domain).strip()
    # tolerate a full email or URL being passed in
    if "@" in d:
        d = d.split("@", 1)[1]
    d = re.sub(r"^https?://", "", d)
    d = d.split("/", 1)[0].strip().strip(".")
    if d.startswith("www."):
        d = d[4:]
    return d


def is_national_chain(business_name: str | None = None, domain: str | None = None) -> bool:
    """True when the business/domain is a national chain (HQ contact, wrong
    target for a per-store solar pitch). Precision-first — see module docstring."""
    d = _norm_domain(domain)
    if d:
        if d in NATIONAL_CHAIN_DOMAINS:
            return True
        if NATIONAL_CHAIN_TOKENS & {p for p in re.split(r"[^a-z0-9]+", d) if p}:
            return True
    if business_name:
        words = {w for w in re.split(r"[^a-z0-9]+", _ascii_lower(business_name)) if w}
        if NATIONAL_CHAIN_TOKENS & words:
            return True
    return False
