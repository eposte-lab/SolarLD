"""Tests for `filter_proprieta` — building-ownership rejection.

The filter has three input shapes (post ADR-002):
  • Atoka native boolean ``proprieta_immobile_sede``
  • Legacy string ``building_ownership`` / ``proprieta_immobile``
  • Field absent → permissive PASS (most of the historic funnel)

These tests pin the priority order (boolean checked first) so a
future refactor can't silently swap the branches.
"""

from __future__ import annotations

from src.services.offline_filters import filter_proprieta


def test_passes_when_no_ownership_data_present() -> None:
    """Permissive default — empty Atoka rows must not be wiped."""
    assert filter_proprieta({}) is None
    assert filter_proprieta({"ragione_sociale": "ACME"}) is None


def test_rejects_when_atoka_boolean_is_false() -> None:
    """Native Atoka all-in-one signal: explicit False = rented."""
    result = filter_proprieta({"proprieta_immobile_sede": False})
    assert result is not None
    assert result.rule == "building_not_owned"
    assert result.candidate_value == {"proprieta_immobile_sede": False}


def test_passes_when_atoka_boolean_is_true() -> None:
    """True is the happy path — owns the building."""
    assert filter_proprieta({"proprieta_immobile_sede": True}) is None


def test_boolean_takes_priority_over_legacy_string() -> None:
    """If both fields disagree, the structured boolean wins.
    Atoka tutto-in-uno is more authoritative than a free-form label
    we may have stored from an older crawl."""
    payload = {
        "proprieta_immobile_sede": True,  # owns
        "building_ownership": "affittato",  # legacy says rented
    }
    # Boolean True short-circuits; the legacy string is ignored.
    assert filter_proprieta(payload) is None


def test_rejects_when_legacy_string_in_reject_set() -> None:
    """Backward compat with the historic free-form labels."""
    for label in ["affittato", "leased", "rented", "comodato"]:
        result = filter_proprieta({"building_ownership": label})
        assert result is not None, f"expected reject for {label}"
        assert result.rule == "building_not_owned"


def test_passes_when_legacy_string_is_owned() -> None:
    """Anything not in the reject set passes — including 'proprietà'."""
    assert filter_proprieta({"building_ownership": "proprieta"}) is None
    assert filter_proprieta({"proprieta_immobile": "owned"}) is None


def test_legacy_string_case_insensitive() -> None:
    """OCR / scraping returns inconsistent casing; the filter
    normalises before lookup."""
    result = filter_proprieta({"building_ownership": "AFFITTATO"})
    assert result is not None
