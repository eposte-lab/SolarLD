"""Map Google Places ``place.types[]`` → wizard_group sector.

Background
----------
v3 L1 used to assign ``predicted_sector = zone.primary_sector`` blindly,
so a butcher shop ("Da Gigione Macelleria") that happened to fall inside
a polygon OSM-tagged ``industry_heavy`` got tagged industry_heavy too.
That mis-classification propagated downstream:

  * L4 wasted Solar API calls on businesses outside the tenant's targets
  * L5 Haiku scored them as if they were industrial when they were not
  * L6 promoted them to leads even though they would never qualify for
    a 60+ kW PV install in the operator's funnel.

The fix is to look at the actual ``place.types`` Google returned for the
business and map them to a sector using the table below. We keep the
zone's sector as a fallback for the cases where Places returns generic
types like ``establishment``/``point_of_interest`` only.

Source for the type taxonomy:
  https://developers.google.com/maps/documentation/places/web-service/place-types

Maintenance
-----------
The mapping is intentionally conservative — when in doubt, return None
and let the zone fallback decide. Adding a new sector is just one entry
in ``_TYPE_TO_SECTOR``; removing a noisy type is one line.
"""

from __future__ import annotations

from collections import Counter

# ---------------------------------------------------------------------------
# Place type → sector
# ---------------------------------------------------------------------------
#
# Order in the iteration of place.types matters less than the count of
# matches (we tally and pick the most-voted sector), but we still keep
# strong signals (e.g. butcher_shop → food_production) explicit so a
# single decisive type wins over the noise of generic ones.

_TYPE_TO_SECTOR: dict[str, str] = {
    # ---- Manifatturiero pesante ----
    "factory": "industry_heavy",
    "manufacturer": "industry_heavy",
    "industrial": "industry_heavy",
    "metal_workshop": "industry_heavy",
    "steel_mill": "industry_heavy",
    "cement_plant": "industry_heavy",
    # ---- Logistica & magazzinaggio ----
    "warehouse": "logistics",
    "shipping_and_mailing_service": "logistics",
    "moving_company": "logistics",
    "freight_forwarder": "logistics",
    "courier_service": "logistics",
    "trucking_company": "logistics",
    "logistics_service": "logistics",
    # ---- Grande distribuzione ----
    "supermarket": "retail_gdo",
    "hypermarket": "retail_gdo",
    "wholesaler": "retail_gdo",
    "shopping_mall": "retail_gdo",
    "department_store": "retail_gdo",
    "warehouse_store": "retail_gdo",
    # ---- Automotive ----
    "car_dealer": "automotive",
    "car_repair": "automotive",
    "auto_parts_store": "automotive",
    "car_rental": "automotive",
    "auto_body_shop": "automotive",
    "auto_machine_shop": "automotive",
    "tire_shop": "automotive",
    "truck_repair": "automotive",
    # ---- Horeca (escluso dai target attuali — segna comunque per filtraggio) ----
    "restaurant": "horeca",
    "hamburger_restaurant": "horeca",
    "american_restaurant": "horeca",
    "italian_restaurant": "horeca",
    "pizza_restaurant": "horeca",
    "sandwich_shop": "horeca",
    "deli": "horeca",
    "bar": "horeca",
    "cafe": "horeca",
    "fast_food_restaurant": "horeca",
    # ---- Food production ----
    "butcher_shop": "food_production",
    "bakery": "food_production",
    "food_store": "food_production",
    # ---- Hospitality ----
    "hotel": "hospitality_large",
    "lodging": "hospitality_large",
    "resort_hotel": "hospitality_large",
    "motel": "hospitality_large",
    # ---- Healthcare ----
    "hospital": "healthcare",
    "medical_clinic": "healthcare_private",
    # ---- Education ----
    "school": "education",
    "university": "education",
    # ---- Agricultural ----
    "farm": "agricultural_intensive",
    "agriculture": "agricultural_intensive",
}


# Generic / noise types that don't carry sector signal — we ignore them
# in the vote so they don't dilute the count.
_NOISE_TYPES: frozenset[str] = frozenset(
    {
        "establishment",
        "point_of_interest",
        "store",
        "service",
        "food",
        "place_of_worship",
        "premise",
        "geocode",
        "political",
    }
)


def classify_place(types: list[str] | None) -> str | None:
    """Return the wizard_group sector for a Google Places business.

    The vote is the most-frequent sector across the place's types; ties
    fall back to the FIRST type that maps (preserving Google's order,
    which puts the most relevant type first). Returns None when no type
    matches a sector — caller should fall back to the zone's primary
    sector or skip the candidate.

    Examples
    --------
    >>> classify_place(["butcher_shop", "restaurant", "store"])
    'food_production'  # butcher_shop is the strongest food signal
    >>> classify_place(["warehouse", "establishment"])
    'logistics'
    >>> classify_place(["establishment", "point_of_interest"])
    None
    """
    if not types:
        return None

    votes: Counter[str] = Counter()
    first_match: str | None = None
    for t in types:
        if t in _NOISE_TYPES:
            continue
        sector = _TYPE_TO_SECTOR.get(t)
        if sector is None:
            continue
        if first_match is None:
            first_match = sector
        votes[sector] += 1

    if not votes:
        return None

    # Pick the sector with the most votes; on ties pick the first match
    # (preserves Google's "most-relevant first" order in place.types).
    top_count = votes.most_common(1)[0][1]
    top_sectors = [s for s, c in votes.items() if c == top_count]
    if len(top_sectors) == 1:
        return top_sectors[0]
    return first_match


# ---------------------------------------------------------------------------
# Sector → Google Places includedPrimaryTypes
# ---------------------------------------------------------------------------
#
# Used by L1 discovery: instead of sending a free-text "keyword" (which
# Places New Nearby ignores) we send the exact Google primary types that
# match the sector. The API will only return businesses with one of
# those primary types, dramatically narrowing the candidate funnel.
#
# Keep these tight — over-broad lists (e.g. adding "store" everywhere)
# bring back the same all-POI flood the keyword approach did.

_SECTOR_TO_INCLUDED_TYPES: dict[str, list[str]] = {
    # Heavy/light industry: the Places API (New) has NO valid primary type
    # for factories — `warehouse` is rejected with HTTP 400 ("Unsupported
    # types"). Leaving these empty routes discovery through keyword Text
    # Search (places_keywords: "carpenteria metallica", "acciaieria",
    # "lavorazione plastica", …) in places_discovery.discover_for_zone,
    # which actually finds the `manufacturer`-typed B2B companies.
    "industry_heavy": [],
    "industry_light": [],
    "logistics": [
        # `warehouse` removed — invalid Places (New) type that would HTTP 400
        # the whole request. The remaining two are valid Table A types.
        "moving_company",
        "shipping_and_mailing_service",
    ],
    "retail_gdo": [
        "supermarket",
        "wholesaler",
        "shopping_mall",
        "department_store",
        "warehouse_store",
    ],
    "automotive": [
        "car_dealer",
        "car_repair",
        "auto_parts_store",
    ],
    "horeca": ["restaurant", "bar", "cafe"],
    "hospitality_food_service": [
        # Catering, mensa aziendale, food-services-on-site. Overlaps with
        # HORECA but the segment targets B2B kitchens (mensa, catering
        # industriali) rather than the public-facing restaurant funnel.
        "catering_service",
        "meal_takeaway",
        "meal_delivery",
    ],
    "food_production": ["bakery", "butcher_shop"],
    "agricultural_intensive": [
        # Serre, vivai, allevamenti intensivi — high HVAC + lighting
        # load, big roofs (= big PV potential).
        "farm",
    ],
    "hospitality_large": ["hotel", "resort_hotel"],
    "healthcare": ["hospital"],
    "healthcare_private": [
        # Cliniche private, ambulatori, dentisti — smaller than hospitals
        # but with continuous HVAC + sterilisation load.
        "dental_clinic",
        "doctor",
        "medical_lab",
        "physiotherapist",
        "veterinary_care",
    ],
    "education": ["school", "university"],
    "personal_services": [
        # Palestre, lavanderie, parrucchieri, centri estetici — small
        # commercial with HVAC/heating loads suitable for sub-30 kW PV.
        "gym",
        "spa",
        "hair_salon",
        "beauty_salon",
        "laundry",
    ],
    "professional_offices": [
        # Studi legali, commercialisti, consulenze, agenzie immobiliari
        # (escluse da amministratori_condominio), studi ingegneria.
        # Office-only consumption: lighting + HVAC + electronics.
        "lawyer",
        "accounting",
        "consultant",
        "insurance_agency",
    ],
    "amministratori_condominio": ["real_estate_agency"],
}


def included_types_for_sector(sector: str) -> list[str]:
    """Return the Google Places ``includedPrimaryTypes`` for a sector.

    Empty list means "no narrowing" — caller should NOT pass the field
    to the API in that case (the API requires a non-empty array when the
    field is present).
    """
    return list(_SECTOR_TO_INCLUDED_TYPES.get(sector, []))


# Sectors where the Google Places category alone is too broad and the
# search needs a textual narrowing. Selecting "amministratori_condominio"
# without these keywords returns every real_estate_agency in the radius
# (Tecnocasa, Gabetti, Regus, …) because Google has no dedicated
# "condominium administrator" category in Italy. We force a default
# textQuery in those cases so Nearby Search becomes Text Search and the
# results are filtered by name/description.
_SECTOR_DEFAULT_KEYWORD: dict[str, str] = {
    "amministratori_condominio": "amministratore condominio",
}


def default_keyword_for_sector(sector: str) -> str | None:
    """Default text-query for sectors where the category alone is too broad.

    The prospector pipeline calls this when the user hasn't typed a
    keyword. Returning a non-None value flips the search from Nearby to
    Text Search.
    """
    return _SECTOR_DEFAULT_KEYWORD.get(sector)


# Sectors that bypass Google Places entirely and query the Italian
# business registry instead. Keyed by `wizard_group`; value is the
# list of ATECO codes (without dots) to ask OpenAPI.it for.
#
# Why bypass Google: for these sectors the Places category is either
# missing (no equivalent type in Italy) or so broad that filtering
# noise out costs more than the registry call itself. The registry
# is exhaustive: every administrator/clinic/etc. is registered by
# law, so we get the actual cohort, not a Google-curated subset.
_SECTOR_TO_REGISTRY_ATECO: dict[str, list[str]] = {
    "amministratori_condominio": ["68.32.00", "81.10.00"],
}


def registry_ateco_for_sector(sector: str) -> list[str]:
    """Return the ATECO codes to query OpenAPI.it for, or [] when the
    sector should keep using Google Places."""
    return list(_SECTOR_TO_REGISTRY_ATECO.get(sector, []))
