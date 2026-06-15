"""Tests for company-website decision-maker name discovery (Phase 2)."""

from __future__ import annotations

import pytest

from src.services import decision_maker_name as dn
from src.services.decision_maker_name import (
    PersonName,
    it_permutations,
    render_pattern,
    split_name,
)

_JSONLD_FOUNDER = """<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Organization","name":"Acme Srl",
 "founder":{"@type":"Person","name":"Mario Rossi","jobTitle":"Titolare"}}
</script></head><body>Benvenuti</body></html>"""

_JSONLD_GRAPH = """<script type="application/ld+json">
{"@graph":[{"@type":"WebSite","name":"x"},
 {"@type":"Person","name":"Giulia Bianchi","jobTitle":"Amministratore Delegato"}]}
</script>"""

_TITLES_HTML = (
    "<div class=team><h3>Luca Esposito</h3><p>Amministratore Unico</p></div>"
    "<p>Il nostro Direttore Generale è Anna Verdi.</p>"
)


# --------------------------------------------------------------------------- #
# local-part generation
# --------------------------------------------------------------------------- #
def test_it_permutations_exact_order():
    p = PersonName(first="Mario", last="Rossi")
    assert it_permutations(p) == ["mario.rossi", "mrossi", "mario", "mariorossi", "marior"]


def test_permutations_ascii_fold_apostrophe_accent():
    assert it_permutations(PersonName(first="Niccolò", last="D'Angelo"))[0] == "niccolo.dangelo"
    assert it_permutations(PersonName(first="José", last="Muñoz"))[0] == "jose.munoz"


def test_render_pattern_variants():
    p = PersonName(first="Mario", last="Rossi")
    assert render_pattern("{first}.{last}", p) == "mario.rossi"
    assert render_pattern("{f}{last}", p) == "mrossi"
    assert render_pattern("{first}_{l}", p) == "mario_r"


def test_render_pattern_unresolved_token_is_none():
    # an unsupported token must not leak braces into an address
    assert render_pattern("{middle}.{last}", PersonName(first="A", last="B")) is None


def test_split_name_honorific_and_particle():
    assert split_name("Dott. Giuseppe De Rosa") == ("Giuseppe", "De Rosa")
    assert split_name("Ing. Luca Bianchi") == ("Luca", "Bianchi")
    assert split_name("Mario") is None


# --------------------------------------------------------------------------- #
# extraction
# --------------------------------------------------------------------------- #
def test_jsonld_founder_person():
    out = dn._extract_from_jsonld(_JSONLD_FOUNDER)
    assert (100, "Mario Rossi", "Titolare") in out


def test_jsonld_graph_person():
    out = dn._extract_from_jsonld(_JSONLD_GRAPH)
    assert any(name == "Giulia Bianchi" and rank == 94 for rank, name, _r in out)


def test_titles_name_not_glued_to_title():
    out = dn._extract_from_titles(_TITLES_HTML)
    names = {name for _r, name, _l in out}
    assert "Luca Esposito" in names
    assert "Anna Verdi" in names
    # the title word must never be folded into the name
    assert not any("Amministratore" in n or "Direttore" in n for n in names)


def test_titles_reject_address_and_boilerplate():
    noise = (
        "<footer>Via Roma Napoli — Privacy Policy — Partita IVA 0123 — Direttore Generale</footer>"
    )
    assert dn._extract_from_titles(noise) == []


def test_titles_reject_place_name_after_sentence_break():
    # "Reggio Emilia" precedes the title but across a sentence boundary (period)
    # → must NOT be taken as a person name.
    html = "Sede di Reggio Emilia. Il presidente del consiglio comunale ha aperto."
    names = {name for _r, name, _l in dn._extract_from_titles(html)}
    assert "Reggio Emilia" not in names


def test_titles_reject_external_org_person():
    # the title belongs to another entity ("direttore di Confindustria")
    html = "Come dichiarato da Giuseppe Bianchi, direttore di Confindustria locale."
    names = {name for _r, name, _l in dn._extract_from_titles(html)}
    assert "Giuseppe Bianchi" not in names


# --------------------------------------------------------------------------- #
# fail-open robustness (pathological JSON-LD)
# --------------------------------------------------------------------------- #
def test_walk_jsonld_is_depth_bounded():
    node = {"@type": "Person", "name": "Deep Person", "jobTitle": "Titolare"}
    for _ in range(200):  # nest the person far past the depth guard
        node = {"@type": "Organization", "employee": node}
    # terminates without RecursionError, and the too-deep person is not yielded
    assert list(dn._walk_jsonld_persons(node)) == []


def test_extract_jsonld_pathological_nesting_does_not_raise():
    deep = (
        '{"@type":"Organization","employee":' * 1500
        + '{"@type":"Person","name":"Z Z"}'
        + "}" * 1500
    )
    block = f'<script type="application/ld+json">{deep}</script>'
    assert dn._extract_from_jsonld(block) == []  # skipped, never raises


def test_extract_jsonld_oversized_block_skipped():
    big = f'<script type="application/ld+json">{"x" * (dn._JSONLD_MAX_BLOCK + 10)}</script>'
    assert dn._extract_from_jsonld(big) == []


# --------------------------------------------------------------------------- #
# find_decision_maker_name (network mocked)
# --------------------------------------------------------------------------- #
def _fetch_factory(html_by_suffix: dict[str, str]):
    async def _fetch(url, *, client, timeout=8.0):  # noqa: ANN001, ANN202
        for suffix, html in html_by_suffix.items():
            if url.endswith(suffix):
                return html
        return None

    return _fetch


@pytest.mark.asyncio
async def test_find_returns_person_from_jsonld(monkeypatch):
    monkeypatch.setattr(dn, "_fetch_html", _fetch_factory({"/chi-siamo": _JSONLD_FOUNDER}))
    p = await dn.find_decision_maker_name(domain="acme.it", client=object())
    assert p is not None
    assert (p.first, p.last) == ("Mario", "Rossi")
    assert p.role == "Titolare"


@pytest.mark.asyncio
async def test_find_skips_non_business_domain(monkeypatch):
    monkeypatch.setattr(dn, "is_non_business_domain", lambda d: True)

    async def _boom(*_a, **_k):
        raise AssertionError("must not fetch a non-business domain")

    monkeypatch.setattr(dn, "_fetch_html", _boom)
    assert await dn.find_decision_maker_name(domain="facebook.com", client=object()) is None


@pytest.mark.asyncio
async def test_find_returns_none_when_no_name(monkeypatch):
    monkeypatch.setattr(
        dn, "_fetch_html", _fetch_factory({"/chi-siamo": "<p>Solo prodotti, nessun nome.</p>"})
    )
    assert await dn.find_decision_maker_name(domain="acme.it", client=object()) is None
