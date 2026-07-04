"""Thermal-map spec for the "technical" dossier video (Fase 2 foundation).

The technical/thermal video variant visualises the roof's SOLAR YIELD as a
heat-map — each roof plane tinted by how much sun it gets — plus the ROI
numbers. Google Solar's flux GeoTIFF (``dataLayers.annualFluxUrl``) is NOT
stored (it needs a separate, billable Solar call that has been 403-ing), so we
derive the heat from data we ALREADY have in ``roofs.raw_data.solarPotential``:
each ``roofSegmentStats`` entry carries a ``sunshineQuantiles`` distribution and
a ``boundingBox``/``center``.

This module is PURE (no I/O, no API) so it is fully unit-testable. The video
sidecar renders the heat-map from the returned spec; that visual assembly is a
follow-up (and needs the render pipeline back up to verify the look).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ThermalSegment:
    """One roof plane, positioned in normalised aerial coords with its heat."""

    x: float  # 0..1 centre from West→East across the roof's bounding box
    y: float  # 0..1 centre from North→South (0 = top/north)
    intensity: float  # 0..1 relative sun vs the roof's best plane
    sunshine_hours: float  # the plane's representative (median) yearly sun
    area_m2: float


@dataclass(frozen=True)
class ThermalSpec:
    max_sunshine_hours: float
    segments: list[ThermalSegment]
    # KPI passed straight through from roofs.derivations — the video's number
    # cards pick whatever fields they show (kwp, savings, payback, CO2…).
    derivations: dict[str, Any] = field(default_factory=dict)


def _median(values: list[float]) -> float:
    """Median of an already-implicitly-ordered quantile list (defensive)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return (float(ordered[mid - 1]) + float(ordered[mid])) / 2.0


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def build_thermal_spec(roof: dict[str, Any]) -> ThermalSpec | None:
    """Derive the per-plane heat-map spec from a roof's stored Solar data.

    Returns ``None`` when the roof lacks the Google Solar buildingInsights we
    need (no ``solarPotential.roofSegmentStats`` or no roof bounding box) — the
    caller then falls back to the plain (non-thermal) render.
    """
    raw = roof.get("raw_data") or {}
    sp = raw.get("solarPotential") or {}
    segments_raw = sp.get("roofSegmentStats") or []
    bbox = raw.get("boundingBox") or {}
    ne = bbox.get("ne") or {}
    sw = bbox.get("sw") or {}
    if not segments_raw or ne.get("latitude") is None or sw.get("latitude") is None:
        return None

    lat_n = float(ne["latitude"])
    lat_s = float(sw["latitude"])
    lng_e = float(ne["longitude"])
    lng_w = float(sw["longitude"])
    lat_span = lat_n - lat_s
    lng_span = lng_e - lng_w
    if lat_span <= 0 or lng_span <= 0:
        return None

    # Normalise heat against the roof's sunniest plane so the gradient always
    # uses the full colour range (roof-relative, not absolute), then keep the
    # absolute hours for the "your position gets X sun" callout.
    max_sun = float(sp.get("maxSunshineHoursPerYear") or 0.0)
    seg_suns: list[float] = []
    for seg in segments_raw:
        q = ((seg.get("stats") or {}).get("sunshineQuantiles")) or []
        seg_suns.append(_median([float(v) for v in q]))
    heat_ref = max_sun if max_sun > 0 else (max(seg_suns) if seg_suns else 0.0)

    out: list[ThermalSegment] = []
    for seg, sun in zip(segments_raw, seg_suns, strict=True):
        centre = seg.get("center") or {}
        lat = centre.get("latitude")
        lng = centre.get("longitude")
        if lat is None or lng is None:
            continue
        x = _clamp01((float(lng) - lng_w) / lng_span)
        y = _clamp01((lat_n - float(lat)) / lat_span)  # north at the top
        area = float((seg.get("stats") or {}).get("areaMeters2") or 0.0)
        intensity = _clamp01(sun / heat_ref) if heat_ref > 0 else 0.0
        out.append(
            ThermalSegment(
                x=round(x, 4),
                y=round(y, 4),
                intensity=round(intensity, 4),
                sunshine_hours=round(sun, 1),
                area_m2=round(area, 1),
            )
        )

    if not out:
        return None

    return ThermalSpec(
        max_sunshine_hours=round(heat_ref, 1),
        segments=out,
        derivations=dict(roof.get("derivations") or {}),
    )
