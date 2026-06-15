"""Company-website decision-maker name discovery (Phase 2 — STEP 2 input).

``find_decision_maker_name`` fetches a few low-cost pages (chi-siamo / azienda /
team / homepage) and extracts the owner/leader's name via two signals:

  1. **JSON-LD** (schema.org ``Person`` / ``Organization.founder|employee|ceo``)
     — the cleanest when present; modern Italian SME sites with SEO plugins
     publish it.
  2. **Italian leadership-title regex** (Titolare / Amministratore (Unico|
     Delegato) / Direttore / Presidente / Fondatore / Socio / CEO) anchored to a
     capitalised full name, in both orders ("Mario Rossi, Titolare" and
     "Titolare: Mario Rossi").

No HTML parser is available in apps/api (no bs4/lxml), so this is stdlib regex +
``json`` only. **Fail-open**: every failure path returns ``None`` — the waterfall
just falls through to the role ladder.

The result feeds STEP 2's email guessing: ``render_pattern`` / ``it_permutations``
turn a ``PersonName`` into candidate local-parts (ascii-folded, lowercase).
Guessed addresses are ALWAYS strict-``valid``-verified before use, so a wrong
name cannot cause a send — an unverifiable guess simply falls through.
"""

from __future__ import annotations

import html as _html
import json
import re
import unicodedata
from dataclasses import dataclass

import httpx

from ..core.logging import get_logger
from .web_scraper import _fetch_html, is_non_business_domain

log = get_logger(__name__)

# Pages most likely to name the owner of an Italian SME, best-first. Capped by
# ``max_pages`` — we stop early on a strong hit.
_NAME_PATHS: tuple[str, ...] = (
    "/chi-siamo",
    "/azienda",
    "/team",
    "",  # homepage (often carries Organization JSON-LD)
    "/about",
    "/chi-siamo.html",
    "/la-nostra-azienda",
)

# Leadership titles → (compiled regex, rank, canonical label). Higher rank = a
# stronger ownership signal. 'commerciale'/'vendite' deliberately absent (the
# waterfall targets the energy buyer, not sales — consistent with the ladder).
_TITLE_RANKS: tuple[tuple[re.Pattern[str], int, str], ...] = (
    (re.compile(r"\btitolare\b", re.I), 100, "Titolare"),
    (re.compile(r"\bamministratore\s+unico\b", re.I), 96, "Amministratore Unico"),
    (re.compile(r"\bamministratore\s+delegato\b", re.I), 94, "Amministratore Delegato"),
    (re.compile(r"\b(?:socio\s+)?fondatore\b|\bfounder\b", re.I), 92, "Fondatore"),
    (re.compile(r"\bpresidente\b", re.I), 88, "Presidente"),
    (re.compile(r"\bC\.?\s?E\.?\s?O\.?\b", re.I), 86, "CEO"),
    (re.compile(r"\bamministratore\b", re.I), 80, "Amministratore"),
    (re.compile(r"\bdirettore\s+generale\b", re.I), 78, "Direttore Generale"),
    (re.compile(r"\bdirettore(?:\s+tecnico)?\b", re.I), 70, "Direttore"),
    (re.compile(r"\bresponsabile\b", re.I), 50, "Responsabile"),
)

# A hit at or above this rank is trustworthy enough to stop fetching more pages.
_STRONG_RANK = 86
# Baseline rank for a JSON-LD Person whose jobTitle doesn't match a known title.
_JSONLD_BASE_RANK = 60

_JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# A capitalised Italian full name: 2-3 tokens, each starting uppercase (accents
# allowed), allowing internal apostrophes/hyphens (D'Angelo, Lo-Russo).
_NAME_SRC = r"[A-ZÀ-Þ][a-zà-ÿ'’\-]+(?:\s+[A-ZÀ-Þ][a-zà-ÿ'’\-]+){1,2}"
_NAME_RE = re.compile(_NAME_SRC)
# Name sitting IMMEDIATELY before a title (only punctuation/space between). A
# period is NOT an allowed separator — it marks a sentence boundary, i.e. a
# different context ("Sede di Reggio Emilia. Il presidente…" must not yield a
# name), which kills place-name false positives.
_NAME_BEFORE_RE = re.compile(_NAME_SRC + r"\s*[,:;–—\-]?\s*$")
# A title followed by "<connector> NAME" — the person the title introduces.
_NAME_AFTER_RE = re.compile(
    r"^\s*[,:;–—\-]?\s*(?:è\s+)?(?:il|la|lo|nostro|nostra)?\s*"
    r"(?:sig\.?(?:ra)?|dott\.?(?:ssa)?|ing\.?|geom\.?|arch\.?|avv\.?|rag\.?|dr\.?)?\s*"
    r"(" + _NAME_SRC + r")"
)
# A title immediately followed by "di/della/presso <Org>" belongs to ANOTHER
# entity ("direttore di Confindustria") — not the site owner; skip it.
_EXTERNAL_ORG_RE = re.compile(r"^\s*[,:]?\s*(?:di|della|del|dell'|presso|in)\s+[A-ZÀ-Þ]", re.I)
# Hard ceilings against pathological JSON-LD (fail-open, never crash the task).
_JSONLD_MAX_BLOCK = 512_000
_JSONLD_MAX_DEPTH = 40

# Tokens that look like a name token but never are — kill the match if present.
_NAME_STOPWORDS = frozenset(
    {
        "via",
        "viale",
        "piazza",
        "corso",
        "strada",
        "località",
        "localita",
        "privacy",
        "cookie",
        "policy",
        "partita",
        "iva",
        "codice",
        "fiscale",
        "sede",
        "legale",
        "capitale",
        "sociale",
        "registro",
        "imprese",
        "telefono",
        "email",
        "copyright",
        "tutti",
        "diritti",
        "riservati",
        "powered",
        "credits",
        "azienda",
        "società",
        "societa",
        "gruppo",
        "team",
        "staff",
        "contatti",
        "contattaci",
        "newsletter",
        "italia",
        "italy",
        # leadership-title words — never part of a person name, and a guard
        # against a title being glued onto / mistaken for a name.
        "titolare",
        "amministratore",
        "amministratrice",
        "unico",
        "unica",
        "delegato",
        "delegata",
        "direttore",
        "direttrice",
        "generale",
        "presidente",
        "fondatore",
        "fondatrice",
        "socio",
        "socia",
        "responsabile",
        "ceo",
        "tecnico",
        "tecnica",
        "ufficio",
        # honorifics that shouldn't be read as a first name ("Mr Nicola").
        "mr",
        "mrs",
        "ms",
        "miss",
        # web-agency credits / map embeds / theme placeholders the regex
        # otherwise glues into a "name" ("Great Web", "Esposito Google").
        "web",
        "google",
        "maps",
        "agency",
        "agenzia",
        "studio",
        "design",
        "marketing",
        "hosting",
        "seo",
        "wordpress",
        "theme",
        "template",
        "realizzazione",
        "sviluppo",
    }
)

_HONORIFICS = frozenset(
    {
        "sig",
        "sig.ra",
        "sigra",
        "dott",
        "dott.ssa",
        "dottssa",
        "dr",
        "ing",
        "geom",
        "arch",
        "avv",
        "rag",
        "p.i",
        "pi",
    }
)
# Surname particles that belong to the surname ("De Rosa", "Di Marco", "Lo Russo").
_SURNAME_PARTICLES = frozenset(
    {"de", "di", "lo", "la", "del", "della", "dello", "dei", "degli", "da", "van", "von", "san"}
)


@dataclass(slots=True)
class PersonName:
    first: str
    last: str
    role: str | None = None


# --------------------------------------------------------------------------- #
# Name parsing + email local-part generation
# --------------------------------------------------------------------------- #
def _ascii_fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _clean_localpart(text: str) -> str:
    """ascii-fold, lowercase, drop everything but [a-z0-9] (spaces/'/- removed)."""
    return re.sub(r"[^a-z0-9]", "", _ascii_fold(text).lower())


# Unambiguous demo/placeholder names left in website themes — never a real owner.
_PLACEHOLDER_NAMES = frozenset(
    {"john doe", "jane doe", "jonathan doe", "nome cognome", "name surname", "lorem ipsum"}
)


def _is_plausible_name(full: str) -> bool:
    tokens = [t for t in _WS_RE.split(full.strip()) if t]
    if not (2 <= len(tokens) <= 3):
        return False
    if _ascii_fold(" ".join(tokens)).lower() in _PLACEHOLDER_NAMES:
        return False
    for tok in tokens:
        bare = tok.lower().strip(".'’-")
        if bare in _NAME_STOPWORDS:
            return False
        if len(_ascii_fold(bare)) < 2:
            return False
    return True


def split_name(full: str) -> tuple[str, str] | None:
    """('Mario Rossi') -> ('Mario','Rossi'); handles honorifics + particle surnames."""
    tokens = [t for t in _WS_RE.split(full.strip()) if t]
    while tokens and tokens[0].lower().rstrip(".") in _HONORIFICS:
        tokens.pop(0)
    if len(tokens) < 2:
        return None
    first = tokens[0]
    if len(tokens) >= 3 and tokens[-2].lower() in _SURNAME_PARTICLES:
        last = f"{tokens[-2]} {tokens[-1]}"
    else:
        last = tokens[-1]
    if not _clean_localpart(first) or not _clean_localpart(last):
        return None
    return first, last


def render_pattern(pattern: str, person: PersonName) -> str | None:
    """Render a Hunter pattern ('{first}.{last}', '{f}{last}', …) into a
    local-part. Returns None if a token is left unresolved or the result is empty.
    """
    first = _clean_localpart(person.first)
    last = _clean_localpart(person.last)
    if not first or not last:
        return None
    local = (
        pattern.replace("{first}", first)
        .replace("{last}", last)
        .replace("{f}", first[0])
        .replace("{l}", last[0])
    )
    if "{" in local or "}" in local:  # unsupported token left over
        return None
    local = re.sub(r"[^a-z0-9._\-]", "", local.lower())
    return local or None


def it_permutations(person: PersonName) -> list[str]:
    """Italian SME local-part guesses, best-first (deduped, order preserved):
    mario.rossi, mrossi, mario, mariorossi, marior."""
    first = _clean_localpart(person.first)
    last = _clean_localpart(person.last)
    if not first or not last:
        return []
    raw = [
        f"{first}.{last}",
        f"{first[0]}{last}",
        first,
        f"{first}{last}",
        f"{first}{last[0]}",
    ]
    seen: set[str] = set()
    out: list[str] = []
    for c in raw:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def _rank_for_title(title: str | None) -> int:
    if not title:
        return _JSONLD_BASE_RANK
    for pat, rank, _label in _TITLE_RANKS:
        if pat.search(title):
            return rank
    return _JSONLD_BASE_RANK


def _label_for_title(title: str | None) -> str | None:
    if not title:
        return None
    for pat, _rank, label in _TITLE_RANKS:
        if pat.search(title):
            return label
    return title.strip() or None


def _walk_jsonld_persons(node: object, _depth: int = 0):  # noqa: ANN201 — recursive generator
    """Yield (name, jobTitle|None) for Person nodes + Organization founder/ceo.
    Depth-bounded so pathological/adversarial nesting can't overflow the stack."""
    if _depth > _JSONLD_MAX_DEPTH:
        return
    if isinstance(node, list):
        for item in node:
            yield from _walk_jsonld_persons(item, _depth + 1)
        return
    if not isinstance(node, dict):
        return
    types = node.get("@type")
    type_set = {
        t.lower() for t in (types if isinstance(types, list) else [types]) if isinstance(t, str)
    }
    if "person" in type_set:
        name = node.get("name")
        if isinstance(name, str) and name.strip():
            jt = node.get("jobTitle")
            yield name.strip(), (jt.strip() if isinstance(jt, str) else None)
    # Organization-ish nodes: descend into leadership keys.
    for key in ("founder", "founders", "employee", "employees", "ceo", "member"):
        if key in node:
            yield from _walk_jsonld_persons(node[key], _depth + 1)
    if "@graph" in node:
        yield from _walk_jsonld_persons(node["@graph"], _depth + 1)


def _extract_from_jsonld(html: str) -> list[tuple[int, str, str | None]]:
    out: list[tuple[int, str, str | None]] = []
    for block in _JSONLD_RE.findall(html):
        block = block.strip()
        if not block or len(block) > _JSONLD_MAX_BLOCK:
            continue
        try:
            data = json.loads(block)
            persons = list(_walk_jsonld_persons(data))
        except Exception as exc:  # noqa: BLE001 — pathological JSON-LD is just skipped
            log.debug("name_discovery.jsonld_skip", err=type(exc).__name__)
            continue
        for name, jobtitle in persons:
            if _is_plausible_name(name):
                out.append((_rank_for_title(jobtitle), name, _label_for_title(jobtitle)))
    return out


def _extract_from_titles(html: str) -> list[tuple[int, str, str | None]]:
    text = _html.unescape(_WS_RE.sub(" ", _TAG_RE.sub(" ", html)))
    out: list[tuple[int, str, str | None]] = []
    for pat, rank, label in _TITLE_RANKS:
        for m in pat.finditer(text):
            after = text[m.end() : m.end() + 55]
            if _EXTERNAL_ORG_RE.match(after):
                continue  # title belongs to another org ("… di Confindustria")
            before = text[max(0, m.start() - 55) : m.start()]
            # The name must sit IMMEDIATELY next to the title (no sentence break),
            # else a place-name or unrelated capitalised pair leaks in.
            cand: str | None = None
            mb = _NAME_BEFORE_RE.search(before)
            if mb:
                cand = re.sub(r"[\s,:;–—\-]+$", "", mb.group(0)).strip()
            if not cand:
                ma = _NAME_AFTER_RE.match(after)
                if ma:
                    cand = ma.group(1).strip()
            if cand and _is_plausible_name(cand):
                out.append((rank, cand, label))
    return out


# Reachability variants. Many Italian SME sites serve only ``http://`` or only
# the ``www`` host; the production scraper reaches them via the canonical URL,
# but here we derive from the email domain, so probe scheme/host variants to
# find the live base instead of assuming ``https://<apex>``.
_BASE_VARIANTS: tuple[str, ...] = (
    "https://{d}",
    "https://www.{d}",
    "http://{d}",
    "http://www.{d}",
)


async def _resolve_base(domain: str, client: httpx.AsyncClient) -> tuple[str, str] | None:
    """Return ``(base_url, homepage_html)`` for the first reachable scheme/host
    variant, or ``None`` if the site is unreachable on all of them."""
    for tmpl in _BASE_VARIANTS:
        base = tmpl.format(d=domain)
        try:
            html = await _fetch_html(base, client=client)
        except Exception as exc:  # noqa: BLE001 — probing is best-effort
            log.debug("name_discovery.base_probe_failed", base=base, err=type(exc).__name__)
            continue
        if html:
            return base, html
    return None


async def find_decision_maker_name(
    *,
    domain: str,
    client: httpx.AsyncClient | None = None,
    max_pages: int = 4,
) -> PersonName | None:
    """Discover the owner/leader name from the company website. Fail-open → None.

    Resolves a reachable base URL (https/http × apex/www), then fetches up to
    ``max_pages`` low-cost pages (no API credits, just HTTP), preferring JSON-LD
    then Italian title regex, stopping early on a strong hit.
    """
    domain = (domain or "").strip().lower()
    if not domain or is_non_business_domain(domain):
        return None

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=8.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; SolarLeadBot/1.0)"},
        )
    candidates: list[tuple[int, str, str | None]] = []
    try:
        resolved = await _resolve_base(domain, client)
        if resolved is None:
            return None
        base, home_html = resolved
        for path in _NAME_PATHS[: max(1, max_pages)]:
            if path == "":
                html = home_html  # reuse the homepage already fetched to probe base
            else:
                url = f"{base}{path}"
                try:
                    html = await _fetch_html(url, client=client)
                except Exception as exc:  # noqa: BLE001 — discovery is best-effort
                    log.debug("name_discovery.fetch_failed", url=url, err=type(exc).__name__)
                    continue
            if not html:
                continue
            try:
                candidates.extend(_extract_from_jsonld(html))
                candidates.extend(_extract_from_titles(html))
            except Exception as exc:  # noqa: BLE001 — extraction is best-effort
                log.debug("name_discovery.extract_failed", domain=domain, err=type(exc).__name__)
                continue
            if candidates and max(c[0] for c in candidates) >= _STRONG_RANK:
                break
    finally:
        if own_client and client is not None:
            await client.aclose()

    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0], reverse=True)
    for _rank, name, role in candidates:
        parts = split_name(name)
        if parts:
            log.info("name_discovery.found", domain=domain, role=role)
            return PersonName(first=parts[0], last=parts[1], role=role)
    return None
