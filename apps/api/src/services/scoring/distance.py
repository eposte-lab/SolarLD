"""Distance subscore — closer roof = cheaper install = higher margin.

The tenant's HQ lat/lng is read from ``tenants.settings.hq_lat`` /
``settings.hq_lng`` (optional — tenants can leave it out during
onboarding and set it later). When missing we return a neutral 50 so
distance stops influencing the score rather than corrupting it.

Banded score:
  * <= 10 km → 100
  * <= 30 km → 80
  * <= 60 km → 60
  * <= 100 km → 40
  * >  100 km → 20
"""

from __future__ import annotations

from .geo import haversine_km


def distance_score(
    roof_lat: float | None,
    roof_lng: float | None,
    hq_lat: float | None,
    hq_lng: float | None,
) -> int:
    if roof_lat is None or roof_lng is None:
        return 50
    if hq_lat is None or hq_lng is None:
        return 50

    km = haversine_km(float(roof_lat), float(roof_lng), float(hq_lat), float(hq_lng))

    if km <= 10:
        return 100
    if km <= 30:
        return 80
    if km <= 60:
        return 60
    if km <= 100:
        return 40
    return 20
