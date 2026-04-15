"""Technical quality filters for roof insights.

Hunter produces a raw stream of buildings discovered by Google Solar; most
are noise (too small, bad orientation, heavy shading, already has PV). The
filters here reject them *before* hitting the roofs table so we don't waste
Identity + Scoring cycles.

Thresholds are deliberately conservative; they can be relaxed per-tenant
later via `scoring_weights` overrides.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..google_solar_service import RoofInsight

# Reject roofs smaller than 20 m² — can't host a profitable plant.
MIN_AREA_SQM = 20.0
# Reject roofs smaller than 2 kWp — below self-consumption payback threshold.
MIN_KWP = 2.0
# Reject heavily shaded roofs (< 0.45 sunshine ratio).
MIN_SHADING_SCORE = 0.45
# Reject north-facing roofs in the northern hemisphere (< −45° azimuth
# means "facing pole"); we instead allow any non-N facing for Italy.
REJECTED_EXPOSURES = {"N"}
# Pitch window 5°–60° — flat roofs allowed, near-vertical rejected.
PITCH_MIN = 5.0
PITCH_MAX = 60.0


@dataclass(slots=True)
class FilterVerdict:
    accepted: bool
    reason: str | None = None


def apply_technical_filters(insight: RoofInsight) -> FilterVerdict:
    """Return accept/reject + human-readable reason.

    Callers should `insight.raw` through to `roofs.raw_data` regardless of
    verdict — rejected rows are still written with `status='rejected'` for
    analytics.
    """
    if insight.area_sqm < MIN_AREA_SQM:
        return FilterVerdict(False, f"area<{MIN_AREA_SQM}m²")
    if insight.estimated_kwp < MIN_KWP:
        return FilterVerdict(False, f"kwp<{MIN_KWP}")
    if insight.shading_score < MIN_SHADING_SCORE:
        return FilterVerdict(False, f"shading<{MIN_SHADING_SCORE}")
    if insight.dominant_exposure in REJECTED_EXPOSURES:
        return FilterVerdict(False, f"exposure={insight.dominant_exposure}")
    if not (PITCH_MIN <= insight.pitch_degrees <= PITCH_MAX):
        return FilterVerdict(False, f"pitch={insight.pitch_degrees}°")
    return FilterVerdict(True, None)
