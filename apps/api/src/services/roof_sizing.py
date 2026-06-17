"""Realistic roof sizing — trims Google's optimistic *max array* to the roof
that would genuinely be installed.

Google's ``solarPotential.solarPanels`` is the MAXIMUM array: on dense/complex
urban roofs it fills every little structure and sliver (observed: 857 panels
across 102 segments on one Naples block), which inflates the quoted kWp / €
and over-promises to the client. This module selects a conservative subset —
the main installable roof — and recomputes the headline numbers from it, so the
deterministic layout AND the quoted figures stay honest.

Two entry points, sharing one core ("panel subset → recomputed sizing"):
  * :func:`apply_realistic_sizing` — AUTOMATIC: drop slivers + steep faces
    (Feature 1). Applied right inside ``_parse_building_insights`` so every
    consumer (L4 write, ROI derivations, render, the layout view) sees the
    realistic insight.
  * :func:`recompute_from_panels` — MANUAL: given an explicit kept-panel subset
    (e.g. the panels inside an operator-drawn polygon, Feature 2), recompute the
    sizing. Same math, different selection.

``raw`` and the whole-roof ``area_sqm`` are PRESERVED — ``area_sqm`` drives the
sector consumption estimate (building floor area), which the trim must not
change; only PRODUCTION (panels/kWp/kWh) is reduced. Everything here is pure +
fail-open: any error or missing segment data returns the input unchanged.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from typing import Any

from ..core.config import settings
from ..core.logging import get_logger
from .google_solar_service import RoofInsight, SolarPanel

log = get_logger(__name__)


def _segment_meta(raw: dict[str, Any]) -> dict[int, dict[str, float]]:
    """``segment_index -> {area, pitch}`` from the raw buildingInsights payload.

    Tolerates both shapes seen in storage: the bare payload and the one nested
    under a ``solar`` key. ``segment_index`` on a panel is the 0-based index into
    ``roofSegmentStats`` (how Google references segments), so enumeration order
    is the key.
    """
    potential = ((raw.get("solar") if isinstance(raw, dict) else None) or raw or {}).get(
        "solarPotential"
    ) or {}
    segs = potential.get("roofSegmentStats") or []
    out: dict[int, dict[str, float]] = {}
    for i, s in enumerate(segs):
        stats = s.get("stats") or {}
        out[i] = {
            "area": float(stats.get("areaMeters2", 0.0) or 0.0),
            "pitch": float(s.get("pitchDegrees", 0.0) or 0.0),
        }
    return out


def select_realistic_panels(
    panels: list[SolarPanel],
    raw: dict[str, Any],
    *,
    min_segment_fraction: float,
    max_pitch_deg: float,
) -> list[SolarPanel]:
    """Return the panels on the main installable roof planes.

    Keeps a segment when its panel count is at least ``min_segment_fraction`` of
    the LARGEST segment's count — so the main roof planes stay and the small
    segments Google scatters across the whole complex are dropped — AND its
    pitch is ≤ ``max_pitch_deg`` (near-vertical facades dropped). Never returns
    empty when there ARE panels: if every segment fails the bar (e.g. the
    largest is too steep), it falls back to the single segment with the most
    panels.
    """
    if not panels:
        return list(panels)
    meta = _segment_meta(raw)
    counts = Counter(p.segment_index for p in panels)
    largest = max(counts.values())
    threshold = min_segment_fraction * largest
    keep_segs = {
        seg
        for seg, n in counts.items()
        if n >= threshold and meta.get(seg, {}).get("pitch", 0.0) <= max_pitch_deg
    }
    if not keep_segs:
        keep_segs = {counts.most_common(1)[0][0]}
    return [p for p in panels if p.segment_index in keep_segs]


def extract_all_panels(payload: dict[str, Any]) -> list[SolarPanel]:
    """Every Google Solar panel from a raw buildingInsights payload — UNtrimmed.

    The manual delineation tool (Feature 2) filters the FULL panel set by the
    operator's polygon, so it needs the panels straight from ``raw_data`` rather
    than the automatically-trimmed ``insight.panels``. Tolerates the bare and
    ``solar``-nested payload shapes.
    """
    potential = (
        (payload.get("solar") if isinstance(payload, dict) else None) or payload or {}
    ).get("solarPotential") or {}
    out: list[SolarPanel] = []
    for p in potential.get("solarPanels") or []:
        c = p.get("center") or {}
        lat = float(c.get("latitude", 0.0) or 0.0)
        lng = float(c.get("longitude", 0.0) or 0.0)
        if not lat and not lng:
            continue
        out.append(
            SolarPanel(
                lat=lat,
                lng=lng,
                orientation=str(p.get("orientation", "LANDSCAPE")),
                segment_azimuth_deg=0.0,
                yearly_energy_kwh=float(p.get("yearlyEnergyDcKwh", 0.0) or 0.0),
                segment_index=int(p.get("segmentIndex", 0) or 0),
            )
        )
    return out


def panels_inside_polygon(
    panels: list[SolarPanel], polygon_geojson: dict[str, Any]
) -> list[SolarPanel]:
    """Panels whose centre falls inside the GeoJSON polygon (``[lng, lat]`` rings)."""
    from shapely.geometry import Point, shape

    poly = shape(polygon_geojson)
    return [p for p in panels if poly.contains(Point(p.lng, p.lat))]


def recompute_from_panels(insight: RoofInsight, kept: list[SolarPanel]) -> RoofInsight:
    """Return a copy of ``insight`` resized to the ``kept`` panel subset.

    Recomputes ``estimated_kwp`` / ``estimated_yearly_kwh`` / ``panels`` /
    ``max_panel_count`` from the subset; preserves ``raw`` + whole-roof
    ``area_sqm``. Used by both the automatic trim and the manual delineation.
    """
    new_kwp = round(len(kept) * insight.panel_capacity_w / 1000.0, 2)
    new_kwh = round(sum(p.yearly_energy_kwh for p in kept), 2)
    if new_kwh <= 0:
        # No per-panel energy in the payload → fall back to the Italian yield.
        new_kwh = round(new_kwp * 1300.0, 2)
    return dataclasses.replace(
        insight,
        panels=list(kept),
        estimated_kwp=new_kwp,
        estimated_yearly_kwh=new_kwh,
        max_panel_count=len(kept),
    )


def apply_realistic_sizing(insight: RoofInsight) -> RoofInsight:
    """Trim ``insight`` to the realistic installable layout (Feature 1).

    Idempotent + fail-open: returns the input unchanged when disabled, when
    there's nothing to trim, when segment data is missing, or on any error.
    """
    if not settings.realistic_sizing_enabled or not insight.panels:
        return insight
    try:
        kept = select_realistic_panels(
            insight.panels,
            insight.raw,
            min_segment_fraction=settings.realistic_sizing_min_segment_fraction,
            max_pitch_deg=settings.realistic_sizing_max_pitch_deg,
        )
        if not kept or len(kept) >= len(insight.panels):
            return insight  # nothing dropped
        trimmed = recompute_from_panels(insight, kept)
        log.info(
            "roof_sizing.trimmed",
            panels_before=len(insight.panels),
            panels_after=len(kept),
            kwp_before=insight.estimated_kwp,
            kwp_after=trimmed.estimated_kwp,
        )
        return trimmed
    except Exception as exc:  # noqa: BLE001 — never break the parse over sizing
        log.warning("roof_sizing.failed", err=str(exc)[:200])
        return insight
