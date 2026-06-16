"""discover_for_zone runs the keyword pass ADDITIVELY for type-bearing sectors.

Regression for the "re-mine finds ~0 new" bug: type-bearing sectors
(retail_gdo, food_production, …) used to fire ONLY the single 20-cap
Nearby call and IGNORE their curated `places_keywords`. Now the keyword
Text Search runs on top of the Nearby pass, merged + deduped, so the long
tail Google ranks below the type top-20 is surfaced.
"""

from __future__ import annotations

import pytest

from src.services import places_discovery as pd
from src.services.sector_target_service import SectorAreaMapping


def _raw(pid: str, name: str) -> dict:
    return {
        "id": pid,
        "displayName": {"text": name},
        "formattedAddress": f"{name} address",
        "location": {"latitude": 40.85, "longitude": 14.27},
        "types": ["supermarket"],
        "businessStatus": "OPERATIONAL",
    }


@pytest.mark.asyncio
async def test_types_sector_also_runs_keyword_pass(monkeypatch) -> None:
    """retail_gdo has a Google primary type AND keywords → BOTH passes run."""
    nearby_calls: list[tuple] = []
    text_calls: list[str] = []

    async def _fake_nearby(
        *, lat, lng, radius_m, included_types, excluded_types, max_results, client, api_key
    ):  # noqa: ANN001, ANN003, E501
        nearby_calls.append((lat, lng))
        return [_raw("NEARBY1", "Iper Nearby")]

    async def _fake_text(*, lat, lng, radius_m, keyword, max_results, client, api_key):  # noqa: ANN001, ANN003
        text_calls.append(keyword)
        # Each keyword surfaces a DISTINCT business the Nearby top-20 missed.
        return [_raw(f"TEXT-{keyword}", f"Store {keyword}")]

    monkeypatch.setattr(pd, "_places_nearby_call", _fake_nearby)
    monkeypatch.setattr(pd, "_places_text_call", _fake_text)

    cfg = SectorAreaMapping(
        wizard_group="retail_gdo",  # has includedPrimaryTypes in _SECTOR_TO_INCLUDED_TYPES
        places_keywords=["ipermercato", "cash and carry"],
        search_radius_m=1500,
    )

    candidates, calls = await pd.discover_for_zone(
        centroid_lat=40.85,
        centroid_lng=14.27,
        sector_config=cfg,
        api_key="test-key",
    )

    ids = {c.place_id for c in candidates}
    # Nearby pass ran once...
    assert len(nearby_calls) == 1
    assert "NEARBY1" in ids
    # ...AND the keyword pass ran for EACH keyword (this is the fix).
    assert text_calls == ["ipermercato", "cash and carry"]
    assert "TEXT-ipermercato" in ids
    assert "TEXT-cash and carry" in ids
    # 1 Nearby + 2 keyword calls.
    assert calls == 3


@pytest.mark.asyncio
async def test_nearby_error_still_runs_keyword_pass(monkeypatch) -> None:
    """A Nearby HTTP error no longer aborts the zone — keywords still run."""
    import httpx

    async def _boom_nearby(**_k):  # noqa: ANN003
        raise httpx.ConnectError("nearby down")

    async def _fake_text(*, keyword, **_k):  # noqa: ANN001, ANN003
        return [_raw(f"TEXT-{keyword}", f"Store {keyword}")]

    monkeypatch.setattr(pd, "_places_nearby_call", _boom_nearby)
    monkeypatch.setattr(pd, "_places_text_call", _fake_text)

    cfg = SectorAreaMapping(
        wizard_group="retail_gdo",
        places_keywords=["ipermercato"],
        search_radius_m=1500,
    )

    candidates, calls = await pd.discover_for_zone(
        centroid_lat=40.85, centroid_lng=14.27, sector_config=cfg, api_key="test-key"
    )

    ids = {c.place_id for c in candidates}
    assert "TEXT-ipermercato" in ids  # keyword pass salvaged the zone
    assert calls == 1  # nearby failed (not counted), 1 keyword call
