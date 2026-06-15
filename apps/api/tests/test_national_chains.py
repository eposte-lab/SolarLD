"""Tests for national-chain detection (precision-first)."""

from __future__ import annotations

import pytest

from src.services.national_chains import is_national_chain


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
