"""Tests for national-chain detection (precision-first)."""

from __future__ import annotations

import pytest

from src.services.national_chains import is_generic_localpart, is_national_chain


@pytest.mark.parametrize(
    "domain",
    [
        "conad.it",
        "eurospin.it",
        "sole365.it",
        "clienti-multicedi.com",
        "multicedi.com",
        "lidl.it",
        "hilton.com",
        "marriott.com",
        "starhotels.it",
        "unieuro.it",
        "www.conad.it",  # www stripped
        "EUROSPIN.IT",  # case-insensitive
        "eurospin.com",  # sibling domain via brand token
        "info@conad.it",  # full email tolerated
    ],
)
def test_chain_domains_detected(domain):
    assert is_national_chain(domain=domain) is True


@pytest.mark.parametrize(
    "domain",
    [
        "tecnotesta.it",
        "steelsud.it",
        "mecfondspa.it",
        "sigmasrl.it",  # local "sigma" must NOT match (ambiguous token excluded)
        "solare-srl.it",  # 'sole'/'sole365' not a substring component
        "coopedile.it",  # 'coop' is intentionally not a token
        "",
        None,
    ],
)
def test_local_smes_not_flagged(domain):
    assert is_national_chain(domain=domain) is False


def test_business_name_token_match():
    assert is_national_chain(business_name="Conad City Napoli") is True
    assert is_national_chain(business_name="Eurospin Pompei") is True


def test_business_name_ambiguous_not_flagged():
    assert is_national_chain(business_name="Cooperativa Agricola Sannita") is False
    assert is_national_chain(business_name="Sigma Impianti Srl") is False


def test_domain_wins_even_with_clean_name():
    # the email domain is the reliable signal even if the display name is generic
    assert is_national_chain(business_name="Supermercato di Mario", domain="conad.it") is True


# --------------------------------------------------------------------------- #
# is_generic_localpart — the second half of the chain-AND-generic exclusion
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "email",
    ["info@supersigma.com", "contatti@x.it", "INFO@conad.it", "info", "segreteria@y.it"],
)
def test_generic_localparts_flagged(email):
    assert is_generic_localpart(email) is True


@pytest.mark.parametrize(
    "email",
    [
        "deco5620@clienti-multicedi.com",  # per-store code → keep
        "filiale51@medistor.it",  # per-branch → keep
        "amministrazione@maraca.it",  # role → keep (not "generic")
        "mario.rossi@x.it",  # named → keep
        "deco188merola@gmail.com",  # franchisee personal → keep
        "",
        None,
    ],
)
def test_targeted_localparts_not_flagged(email):
    assert is_generic_localpart(email) is False


def test_chain_generic_combination():
    # the operative exclusion rule = chain AND generic
    assert is_national_chain(domain="supersigma.com") is True
    assert is_generic_localpart("info@supersigma.com") is True
    # per-store on a chain domain → chain True but generic False → KEPT
    assert is_national_chain(domain="clienti-multicedi.com") is True
    assert is_generic_localpart("deco5620@clienti-multicedi.com") is False
    # normal SME info@ → not a chain → KEPT
    assert is_national_chain(domain="tecnotesta.it") is False
