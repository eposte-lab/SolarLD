"""Address-extraction tests for the website scraper.

Three Italian-website fixture patterns:
  1. JSON-LD ``schema.org/Organization`` block (highest confidence)
  2. ``<address>`` HTML element with inline street regex
  3. Free-text inline regex hit on the page body

Each fixture is asserted to land on the right strategy and produce
the expected (street, cap, city, province) tuple. The
``scan_website_for_address`` integration test mocks the HTTP fetch
so we don't reach out to a real domain.
"""

from __future__ import annotations

import pytest

from src.services.email_extractor import (
    ScrapedAddress,
    _extract_address_from_address_tag,
    _extract_address_from_json_ld,
    _extract_address_from_regex,
    scan_website_for_address,
)


JSON_LD_FIXTURE = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Organization",
  "name": "Multilog SpA",
  "address": {
    "@type": "PostalAddress",
    "streetAddress": "Agglomerato ASI Pascarola",
    "postalCode": "80023",
    "addressLocality": "Caivano",
    "addressRegion": "IT-NA"
  }
}
</script>
</head><body><h1>Multilog</h1></body></html>
"""

ADDRESS_TAG_FIXTURE = """
<html><body>
<address>
  <strong>Sede operativa</strong><br>
  Via Industria 12, 20100 Milano (MI)<br>
  Tel: +39 02 1234567
</address>
</body></html>
"""

REGEX_FIXTURE = """
<html><body>
<p>Vieni a trovarci in Via Roma 5, 00184 Roma (RM) tutti i giorni!</p>
<p>Spedizioni in tutta Italia.</p>
</body></html>
"""


# ---------------------------------------------------------------------------
# Strategy 1 — schema.org JSON-LD
# ---------------------------------------------------------------------------


def test_extract_from_json_ld_returns_full_address() -> None:
    addr = _extract_address_from_json_ld(JSON_LD_FIXTURE)
    assert addr is not None
    assert isinstance(addr, ScrapedAddress)
    assert addr.address == "Agglomerato ASI Pascarola"
    assert addr.cap == "80023"
    assert addr.city == "Caivano"
    assert addr.province == "NA"
    assert addr.source_strategy == "json_ld"
    assert addr.confidence == 0.9


def test_extract_from_json_ld_returns_none_on_garbage() -> None:
    assert _extract_address_from_json_ld("<html>no json-ld here</html>") is None


def test_extract_from_json_ld_tolerates_invalid_json() -> None:
    body = (
        "<script type='application/ld+json'>{not valid json}</script>"
        "<script type='application/ld+json'>"
        '{"@type":"Organization","address":{"streetAddress":"Via X 1",'
        '"postalCode":"10100","addressLocality":"Torino","addressRegion":"TO"}}'
        "</script>"
    )
    addr = _extract_address_from_json_ld(body)
    assert addr is not None
    assert addr.address == "Via X 1"
    assert addr.province == "TO"


# ---------------------------------------------------------------------------
# Strategy 2 — <address> HTML element
# ---------------------------------------------------------------------------


def test_extract_from_address_tag_with_inline_regex() -> None:
    addr = _extract_address_from_address_tag(ADDRESS_TAG_FIXTURE)
    assert addr is not None
    assert addr.source_strategy == "address_tag"
    assert addr.cap == "20100"
    assert "Via Industria 12" in addr.address
    assert addr.city == "Milano"
    assert addr.province == "MI"


def test_address_tag_without_regex_match_keeps_raw_text() -> None:
    """Semantic <address> tag with no Italian-street shape still yields
    a low-confidence record so the resolver can free-text geocode it."""
    body = "<address>Headquarters in central London</address>"
    addr = _extract_address_from_address_tag(body)
    assert addr is not None
    assert addr.source_strategy == "address_tag"
    assert addr.confidence == 0.5
    assert "Headquarters" in addr.address


# ---------------------------------------------------------------------------
# Strategy 3 — inline regex over body text
# ---------------------------------------------------------------------------


def test_extract_from_regex_pulls_italian_street() -> None:
    addr = _extract_address_from_regex(REGEX_FIXTURE)
    assert addr is not None
    assert addr.source_strategy == "regex"
    assert "Via Roma 5" in addr.address
    assert addr.cap == "00184"
    assert addr.city == "Roma"
    assert addr.province == "RM"


def test_regex_returns_none_when_no_italian_address() -> None:
    body = "<html><body><p>Welcome to our office</p></body></html>"
    assert _extract_address_from_regex(body) is None


# ---------------------------------------------------------------------------
# scan_website_for_address — integration with mocked HTTP
# ---------------------------------------------------------------------------


class _FakeAsyncClient:
    """Minimal stand-in for httpx.AsyncClient supporting async ``get``."""

    def __init__(self, body_for_path: dict[str, str]) -> None:
        self._bodies = body_for_path

    async def get(self, url: str):  # noqa: D401 - mock
        # Return the first matching path; default 404 otherwise.
        for path, body in self._bodies.items():
            if url.endswith(path):
                return _FakeResponse(200, body)
        return _FakeResponse(404, "")


class _FakeResponse:
    def __init__(self, status: int, text: str) -> None:
        self.status_code = status
        self.text = text


@pytest.mark.asyncio
async def test_scan_website_short_circuits_on_json_ld() -> None:
    """Tier 1 (JSON-LD) found on /contatti → don't keep crawling."""
    client = _FakeAsyncClient({"/contatti": JSON_LD_FIXTURE})
    result = await scan_website_for_address("stub.it", http_client=client)  # type: ignore[arg-type]
    assert result is not None
    assert result.source_strategy == "json_ld"
    assert result.cap == "80023"
    assert result.page_url is not None
    assert "/contatti" in result.page_url


@pytest.mark.asyncio
async def test_scan_website_picks_best_across_pages() -> None:
    """Address-tag on /contatti, JSON-LD on /chi-siamo → keeps JSON-LD."""
    client = _FakeAsyncClient({
        "/contatti": ADDRESS_TAG_FIXTURE,
        "/chi-siamo": JSON_LD_FIXTURE,
    })
    result = await scan_website_for_address("stub.it", http_client=client)  # type: ignore[arg-type]
    assert result is not None
    # JSON-LD has the highest confidence (0.9); address_tag is 0.75.
    assert result.source_strategy == "json_ld"


@pytest.mark.asyncio
async def test_scan_website_returns_none_when_nothing_found() -> None:
    client = _FakeAsyncClient({"/contatti": "<html>nothing here</html>"})
    result = await scan_website_for_address("stub.it", http_client=client)  # type: ignore[arg-type]
    assert result is None
