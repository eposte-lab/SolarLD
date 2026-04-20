"""Technical subscore — how good is the roof physically for PV?

Inputs come from the `roofs` table (already filled by the Hunter Agent):
  - estimated_kwp      → main driver (bigger array = bigger lead)
  - shading_score      → 0..1 multiplier from Google Solar / Vision
  - exposure           → cardinal; S/SE/SW dominate for Italian latitudes
  - pitch_degrees      → ideal band 20–35°
  - has_existing_pv    → show-stopper; they already went solar
  - area_sqm           → tiebreaker when kWp estimate is missing

The scale is 0–100 so all five subscores live on the same axis and the
combiner can take a plain weighted average.
"""

from __future__ import annotations

from typing import Any

# Exposure multiplier in [0, 1]. Italian latitudes (roughly 36°–47°N)
# strongly favour south-facing arrays. Values taken from PVGIS monthly
# irradiation ratios — we don't need more precision than this here.
_EXPOSURE_FACTOR: dict[str, float] = {
    "S": 1.00,
    "SE": 0.95,
    "SW": 0.95,
    "E": 0.80,
    "W": 0.80,
    "NE": 0.55,
    "NW": 0.55,
    "N": 0.25,
}


def _kwp_term(kwp: float | None, area_sqm: float | None) -> float | None:
    """0..100 mapping. Returns ``None`` if we genuinely have no size info
    (both ``kwp`` and ``area_sqm`` missing) — the caller uses that to
    short-circuit the whole score to 0 rather than coasting on the pitch
    term alone.

    Calibration (post 2026-Q2 re-fit):
      * 2 kWp  → 30
      * 10 kWp → 70
      * 15 kWp → 95
      * 50+kWp → 100
    """
    if kwp is None or kwp <= 0:
        if area_sqm and area_sqm > 0:
            # Rough industry ratio: 6 m²/kWp.
            kwp = float(area_sqm) / 6.0
        else:
            return None
    if kwp >= 50.0:
        return 100.0
    if kwp >= 15.0:
        return 95.0 + (kwp - 15.0) * (5.0 / 35.0)     # 15→95, 50→100
    if kwp >= 10.0:
        return 70.0 + (kwp - 10.0) * 5.0              # 10→70, 15→95
    # [0, 10] → max(0, 30 + (kwp-2)*5)  (2→30, 10→70, <2 decays toward 0)
    return max(0.0, 30.0 + (kwp - 2.0) * 5.0)


def _pitch_term(pitch: float | None) -> float:
    """0..100 — peak at 25°–35°, decays either side."""
    if pitch is None:
        return 60.0  # unknown → neutral
    p = float(pitch)
    if p < 0 or p > 90:
        return 20.0
    if 20.0 <= p <= 40.0:
        return 100.0
    if 10.0 <= p < 20.0:
        return 60.0 + (p - 10.0) * 4.0   # 10→60, 20→100
    if 40.0 < p <= 55.0:
        return 100.0 - (p - 40.0) * (40.0 / 15.0)  # 55→60
    if p < 10.0:
        return 30.0 + p * 3.0  # 0→30, 10→60
    # p > 55
    return max(10.0, 60.0 - (p - 55.0) * 1.5)  # 90→7ish, clamp 10


def technical_score(roof: dict[str, Any]) -> int:
    """Return 0..100 technical subscore for a roofs-table row.

    Degrades gracefully when fields are missing — an unknown shading
    score is treated as 0.75 (a 'decent' default) rather than zeroing
    the whole term.
    """
    if roof.get("has_existing_pv"):
        return 0

    kwp = _to_float(roof.get("estimated_kwp"))
    area = _to_float(roof.get("area_sqm"))
    shading = _to_float(roof.get("shading_score"))
    pitch = _to_float(roof.get("pitch_degrees"))
    exposure = (roof.get("exposure") or "").strip().upper() or None

    kwp_term = _kwp_term(kwp, area)
    if kwp_term is None:
        # No size info at all — refuse to score. Better a 0 than a
        # confident-looking number based only on pitch/exposure.
        return 0
    shading_factor = shading if shading is not None else 0.75   # 0..1
    shading_factor = max(0.0, min(1.0, shading_factor))
    exposure_factor = _EXPOSURE_FACTOR.get(exposure or "", 0.75)
    pitch_term = _pitch_term(pitch)                              # 0..100

    # Geometry base: kwp dominates (it's the actual energy capacity),
    # pitch only nudges. Shading + exposure apply as multipliers since
    # they can genuinely destroy an otherwise-good roof (a north-facing
    # array at a 45° pitch is basically unusable regardless of size).
    base = kwp_term * 0.85 + pitch_term * 0.15
    scaled = base * shading_factor * exposure_factor
    return max(0, min(100, int(round(scaled))))


def _to_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
