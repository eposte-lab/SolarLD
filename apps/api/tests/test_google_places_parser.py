"""Unit tests for the Google Places (New) v1 payload parsers.

We don't hit the network — the tests feed fixture JSON captured from
the real API into the `parse_nearby_payload` / `parse_details_payload`
public helpers and assert the resulting typed dataclasses.

Covered edge cases:
  - `displayName` as a `{text, languageCode}` dict (normal case)
  - `displayName` as a bare string (older/edge responses)
  - Missing optional fields (address, phone, website, business_status)
  - Missing required fields (id, location) → skipped in Nearby list
  - `primaryType` + `types` preserved as tuple (immutable)
"""

from __future__ import annotations

from src.services.google_places_service import (
    PlaceDetails,
    PlaceSummary,
    parse_details_payload,
    parse_nearby_payload,
)


# ---------------------------------------------------------------------------
# Nearby Search payloads
# ---------------------------------------------------------------------------


def _nearby_fixture() -> dict:
    """Approximate response shape of POST /v1/places:searchNearby."""
    return {
        "places": [
            {
                "id": "ChIJAAAAA",
                "displayName": {"text": "Supermercato Alpha", "languageCode": "it"},
                "formattedAddress": "Via Roma 1, Napoli NA",
                "location": {"latitude": 40.8518, "longitude": 14.2681},
                "businessStatus": "OPERATIONAL",
                "types": ["supermarket", "grocery_or_supermarket", "store"],
                "primaryType": "supermarket",
            },
            {
                # displayName as a bare string (edge case).
                "id": "ChIJBBBBB",
                "displayName": "Bar Bravo",
                "location": {"latitude": 40.85, "longitude": 14.27},
                # No address, no businessStatus, no types.
            },
            {
                # Missing id → must be skipped.
                "displayName": {"text": "Ghost"},
                "location": {"latitude": 40.0, "longitude": 14.0},
            },
            {
                # Missing location → must be skipped.
                "id": "ChIJNOCOORDS",
                "displayName": {"text": "No Coords"},
            },
        ]
    }


def test_parse_nearby_returns_summaries_and_skips_incomplete() -> None:
    places = parse_nearby_payload(_nearby_fixture())
    assert [p.place_id for p in places] == ["ChIJAAAAA", "ChIJBBBBB"]
    assert all(isinstance(p, PlaceSummary) for p in places)


def test_parse_nearby_extracts_all_fields() -> None:
    [alpha, bravo] = parse_nearby_payload(_nearby_fixture())
    assert alpha.name == "Supermercato Alpha"
    assert alpha.address == "Via Roma 1, Napoli NA"
    assert alpha.lat == 40.8518
    assert alpha.lng == 14.2681
    assert alpha.business_status == "OPERATIONAL"
    assert alpha.primary_type == "supermarket"
    assert alpha.types == ("supermarket", "grocery_or_supermarket", "store")
    assert alpha.is_operational is True

    # Bravo: missing optional fields tolerated, types defaults to ().
    assert bravo.name == "Bar Bravo"
    assert bravo.address is None
    assert bravo.business_status is None
    assert bravo.types == ()
    assert bravo.primary_type is None
    # None is treated as operational by is_operational.
    assert bravo.is_operational is True


def test_parse_nearby_empty_payload() -> None:
    assert parse_nearby_payload({}) == []
    assert parse_nearby_payload({"places": []}) == []
    assert parse_nearby_payload({"places": None}) == []


def test_parse_nearby_closed_status_flagged_not_operational() -> None:
    payload = {
        "places": [
            {
                "id": "ChIJCLOSED",
                "displayName": {"text": "Ex Bar"},
                "location": {"latitude": 40.0, "longitude": 14.0},
                "businessStatus": "CLOSED_PERMANENTLY",
            }
        ]
    }
    [p] = parse_nearby_payload(payload)
    assert p.business_status == "CLOSED_PERMANENTLY"
    assert p.is_operational is False


# ---------------------------------------------------------------------------
# Place Details payloads
# ---------------------------------------------------------------------------


def _details_fixture() -> dict:
    """Approximate response of GET /v1/places/{id} with the Basic SKU fields."""
    return {
        "id": "ChIJDETAILS",
        "displayName": {"text": "Pizzeria Charlie", "languageCode": "it"},
        "formattedAddress": "Piazza Dante 5, Napoli NA",
        "location": {"latitude": 40.851, "longitude": 14.251},
        "businessStatus": "OPERATIONAL",
        "types": ["restaurant", "food"],
        "primaryType": "restaurant",
        "websiteUri": "https://pizzeriacharlie.it",
        "internationalPhoneNumber": "+39 081 123 4567",
        "nationalPhoneNumber": "081 123 4567",
    }


def test_parse_details_extracts_all_fields() -> None:
    d = parse_details_payload(_details_fixture())
    assert isinstance(d, PlaceDetails)
    assert d.place_id == "ChIJDETAILS"
    assert d.name == "Pizzeria Charlie"
    assert d.address == "Piazza Dante 5, Napoli NA"
    assert d.lat == 40.851
    assert d.lng == 14.251
    assert d.business_status == "OPERATIONAL"
    assert d.types == ("restaurant", "food")
    assert d.primary_type == "restaurant"
    assert d.website == "https://pizzeriacharlie.it"
    assert d.phone_international == "+39 081 123 4567"
    assert d.phone_national == "081 123 4567"


def test_parse_details_missing_optional_fields_tolerated() -> None:
    d = parse_details_payload(
        {
            "id": "ChIJMIN",
            "displayName": {"text": "Minimal"},
            "location": {"latitude": 40.0, "longitude": 14.0},
        }
    )
    assert d.place_id == "ChIJMIN"
    assert d.name == "Minimal"
    assert d.address is None
    assert d.website is None
    assert d.phone_international is None
    assert d.phone_national is None
    assert d.types == ()
    assert d.primary_type is None


def test_parse_details_location_falls_back_to_zero_when_missing() -> None:
    d = parse_details_payload({"id": "ChIJEMPTY"})
    assert d.lat == 0.0
    assert d.lng == 0.0
    assert d.name == ""
