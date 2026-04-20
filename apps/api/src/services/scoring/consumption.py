"""Consumption subscore — how much energy do they likely use?

A solar install's ROI scales with how much grid power the customer can
displace. For B2B we use ATECO-derived averages
(`ateco_consumption_profiles`); for B2C we use the roof area as a proxy
for dwelling size.

Score 0..100:
  * 100 = heavy industrial load (metallurgy, chemicals)
  * ~70 = medium commercial / hospitality
  * ~40 = small office / mid-size B2C home
  * ~20 = low intensity (logistics, warehouse)
  * 0   = no data at all
"""

from __future__ import annotations

from typing import Any

# energy_intensity_tier → baseline score before the per-employee boost
_TIER_BASELINE: dict[str, float] = {
    "high": 75.0,
    "medium": 50.0,
    "low": 30.0,
}


def consumption_score(
    subject: dict[str, Any],
    roof: dict[str, Any],
    ateco_profile: dict[str, Any] | None,
) -> int:
    """Compute 0..100 consumption score.

    ``ateco_profile`` is the row matched from ``ateco_consumption_profiles``
    for the subject's ateco_code (may be ``None`` when B2C or when the
    code isn't in our seed).
    """
    subject_type = (subject.get("type") or "unknown").lower()

    if subject_type == "b2b":
        return _b2b_score(subject, roof, ateco_profile)
    if subject_type == "b2c":
        return _b2c_score(roof)
    # unknown — use the roof geometry as a proxy
    return _b2c_score(roof)


def _b2b_score(
    subject: dict[str, Any],
    roof: dict[str, Any],
    ateco_profile: dict[str, Any] | None,
) -> int:
    if not ateco_profile:
        # No ATECO match → fall back on employee count + roof estimate.
        return _from_employees(subject.get("employees")) or _b2c_score(roof)

    tier = (ateco_profile.get("energy_intensity_tier") or "").lower()
    base = _TIER_BASELINE.get(tier, 40.0)

    # Employee boost: larger headcount ⇒ larger load.
    employees = subject.get("employees")
    if isinstance(employees, (int, float)) and employees > 0:
        if employees >= 100:
            base += 25.0
        elif employees >= 20:
            base += 15.0
        elif employees >= 5:
            base += 5.0

    # Match against the roof's expected PV yield: if consumption dwarfs
    # production the ROI motivation spikes.
    per_sqm = _to_float(ateco_profile.get("avg_yearly_kwh_per_sqm"))
    area = _to_float(roof.get("area_sqm"))
    yearly_kwh = _to_float(roof.get("estimated_yearly_kwh"))
    if per_sqm and area and yearly_kwh and yearly_kwh > 0:
        projected_consumption = per_sqm * area
        ratio = projected_consumption / yearly_kwh
        if ratio >= 1.5:
            base += 10.0  # demand far exceeds PV → big self-consumption win
        elif ratio <= 0.3:
            base -= 15.0  # oversized array for this load → less attractive

    return _clamp(base)


def _b2c_score(roof: dict[str, Any]) -> int:
    area = _to_float(roof.get("area_sqm")) or 0.0
    # B2C homes: a 150 m² roof ≈ comfortable family house ≈ ~5 MWh/yr
    # Score so that a 80 m² apartment block fraction lands at 40 and a
    # 300 m² villa lands at 80.
    if area <= 40:
        return 20
    if area <= 80:
        return int(round(20 + (area - 40) * 0.50))       # 40→40
    if area <= 150:
        return int(round(40 + (area - 80) * 0.4286))     # 150→70
    if area <= 300:
        return int(round(70 + (area - 150) * 0.0667))    # 300→80
    return 85


def _from_employees(employees: Any) -> int:
    if not isinstance(employees, (int, float)) or employees <= 0:
        return 0
    if employees >= 50:
        return 75
    if employees >= 10:
        return 55
    if employees >= 3:
        return 40
    return 25


def _clamp(val: float) -> int:
    return max(0, min(100, int(round(val))))


def _to_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
