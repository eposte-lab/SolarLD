"""Guard the empty-palette fallback in L1 discovery.

When `ateco_google_types` reads back empty (the documented catastrophic-silent
failure), `run_level1_places` falls back to the HARDCODED sector -> Google
primary-type map so discovery keeps draining zones instead of silently
stalling. This test pins the assumption the fallback relies on: every
type-bearing sector in play actually has hardcoded types to fall back to.
"""

from __future__ import annotations

import pytest

from src.services.places_to_sector import included_types_for_sector

# The type-bearing sectors Total Trade targets (zones exist for these).
_TYPE_BEARING = [
    "food_production",
    "retail_gdo",
    "hospitality_large",
    "automotive",
    "logistics",
    "horeca",
    "healthcare",
]


@pytest.mark.parametrize("sector", _TYPE_BEARING)
def test_fallback_covers_type_bearing_sectors(sector: str) -> None:
    # The fallback builds a minimal SectorAreaMapping for any in-play sector
    # with a hardcoded primary type → discovery runs the Nearby pass even with
    # an empty DB palette. If this map ever loses a sector, the fallback would
    # silently skip it, so pin it.
    assert included_types_for_sector(sector), (
        f"{sector} must carry hardcoded Google types for the empty-palette fallback"
    )


@pytest.mark.parametrize("sector", ["industry_heavy", "industry_light"])
def test_industry_is_keyword_only(sector: str) -> None:
    # Heavy/light industry have NO Google leaf type by design (Places rejects
    # `factory`/`warehouse`). The fallback can't help them — documented
    # limitation, asserted so the contract is explicit.
    assert included_types_for_sector(sector) == []
