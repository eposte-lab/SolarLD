"""Prospect-list validation — the send-contact email precedence.

Locks the operator decision for the energivori (openapi_it) channel: the
verified OpenAPI company email is the PRIMARY send contact, overriding the
scraped address; every other channel keeps the scraped email untouched.
"""

from __future__ import annotations

from src.services.prospect_list_validation import _primary_send_email


def test_openapi_email_overrides_scrape() -> None:
    # openapi_it: the company email wins even when scraping found one.
    assert _primary_send_email("info@azienda.it", "scraped@site.it") == "info@azienda.it"


def test_openapi_email_used_when_scrape_empty() -> None:
    # The whole point: a company with no scrapeable email is still sendable.
    assert _primary_send_email("info@azienda.it", None) == "info@azienda.it"


def test_falls_back_to_scrape_when_no_openapi_email() -> None:
    # Other channels pass openapi_email=None → scraped address unchanged.
    assert _primary_send_email(None, "scraped@site.it") == "scraped@site.it"


def test_none_when_neither() -> None:
    assert _primary_send_email(None, None) is None
