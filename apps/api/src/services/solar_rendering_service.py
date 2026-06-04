"""Solar-native rendering pipeline.

Replaces the Replicate/Mapbox path in the Creative agent with a
deterministic, high-quality approach:

  1. Download the Google Solar aerial RGB GeoTIFF (10 cm/pixel).
  2. Parse GeoTIFF georeference tags to build a lat/lng ↔ pixel transform.
  3. Crop a square around the building centre (1024×1024 px).
  4. "Before" image  = the plain aerial crop.
  5. "After" image   = same crop + deterministic solar-panel overlay drawn
                       with PIL in exact Google Solar panel positions.

No AI, no Replicate, no hallucinations.  Panel positions, orientations and
count come directly from ``RoofInsight.panels`` (Google Solar API) so they
correspond perfectly to the kWp figure quoted in the email.
"""

from __future__ import annotations

import io
import math
import struct
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageSequence

from ..core.config import settings
from ..core.logging import get_logger
from .google_solar_service import (
    RoofInsight,
    SolarApiError,
    SolarApiNotFound,
    SolarPanel,
    download_geotiff,
    fetch_building_insight,
    fetch_data_layers,
)
from .google_static_service import (
    GoogleStaticError,
    fetch_google_static_satellite,
    maps_static_key,
)
from .mapbox_service import MapboxError
from .mapbox_service import fetch_static_satellite as fetch_mapbox_satellite

log = get_logger(__name__)

# Output image size in pixels — 16:9 landscape (1536×864).  Le foto
# start/end nascono già in 16:9 così non vengono tagliate quando il
# rendering è mostrato a 16:9 sul portale e sulla dashboard.  A 1536
# di larghezza la trama per-pannello (CELL_COLS × CELL_ROWS) resta
# nitida dopo il sampler del modello e nella GIF.
OUTPUT_W = 1536
OUTPUT_H = 864  # 1536 * 9 / 16
OUTPUT_ASPECT = OUTPUT_W / OUTPUT_H  # 16:9

# Default composition — DOWNWARD SCROLL applied to EVERY render: lift the
# roof so its centre sits at this fraction of the frame HEIGHT (above the
# geometric middle), leaving the lower band clear for the blue "Risparmio
# annuo" data strip. 0.50 would be dead-centre; 0.42 lifts it up.
_ROOF_CENTER_Y = 0.42
# Minimum crop half-height as a multiple of the roof half-height, so there
# is always vertical headroom to perform the lift above without clipping a
# roof that would otherwise fill the frame. Only grows the crop for tall
# roofs; wide capannoni are already diagonal-sized so it's a no-op.
_VLIFT_HEADROOM = 1.45
# Safety margin (fraction of crop half-height) kept between the lifted roof
# edge and the frame edge so the building is never clipped.
_FOCUS_SAFETY = 0.05

# Padding around the roof footprint, expressed as a multiplier of the
# roof's bounding-box half-diagonal (computed from panel cluster bounds
# when available, falls back to sqrt(area) otherwise).
#
# We pick the multiplier ADAPTIVELY based on the size of the panel
# cluster — see ``_adaptive_padding_factor`` below. The crop must keep
# the WHOLE roof comfortably inside the 16:9 frame with breathing room
# on every side: a tight crop clips the roof corners when the building
# is shown at 16:9 on the portal/dashboard, which looks broken. We err
# on the side of zooming OUT — extra context never clips the roof.
#
# This is the value used as a fallback when ``insight.panels`` is
# empty (e.g. a non-eligible roof we still want to render before).
PADDING_FACTOR_FALLBACK = 1.35


def _adaptive_padding_factor(cluster_half_diag_m: float) -> float:
    """Pick a crop padding multiplier proportional to roof size.

    The multiplier sets how much context surrounds the panel cluster.
    Too tight (~1.10) clipped the roof at 16:9; too loose (~1.8) left
    the building as a small subject lost in a sea of field. The curve
    below keeps the building the clear subject while leaving a margin
    so the roof is never clipped.

    Curve (half-diagonal in metres → padding multiplier). Raised across the
    board (operator request) because the B2B target is LARGE manufacturing
    capannoni — more standoff reads better and never clips:
      ≤  8 m (≈ <150 m² roof)   → 1.60  — small house, generous context
      ≈ 20 m (≈ 1000 m² roof)   → 1.52
      ≈ 35 m (industrial)       → 1.45  — tightest, still airy
      ≈ 60 m (>5000 m² roof)    → 1.63
      ≥ 80 m (>16k m² roof)     → 1.75  — sconfinato, massima aria

    The curve never dips to the old tight ~1.22; even the tightest point is
    1.45 so the whole building always sits inside the frame with breathing
    room, and big roofs get the most air.
    """
    if cluster_half_diag_m <= 8.0:
        return 1.60
    if cluster_half_diag_m <= 35.0:
        # Discesa dolce: 8→35 m mappa 1.60 → 1.45 (pavimento alto, più aria).
        t = (cluster_half_diag_m - 8.0) / (35.0 - 8.0)
        return 1.60 - 0.15 * t
    if cluster_half_diag_m >= 80.0:
        return 1.75
    # Risalita: 35→80 m mappa 1.45 → 1.75 — i tetti grandi prendono più aria.
    t = (cluster_half_diag_m - 35.0) / (80.0 - 35.0)
    return 1.45 + 0.30 * t


# Base-image tile radius bounds (metres). The tile was previously a fixed
# 50 m (a 100 m-wide square): too small for industrial capannoni, which are
# routinely 150-250 m long, so the building was clipped at the SOURCE before
# the crop even ran. We now size the tile to the roof; the lower bound keeps
# small B2C roofs from over-zooming, the upper bound stays within Google
# Solar dataLayers' practical radius (beyond it we fall back to Google Static).
_MIN_BASE_RADIUS_M = 50
# Raised 140 → 175 (operator request): large manufacturing capannoni need a
# wider frame, and beyond Google Solar dataLayers' practical radius the
# Mapbox fallback (which we size by zoom) covers it cleanly.
_MAX_BASE_RADIUS_M = 175

# When the panel-cluster half-diagonal exceeds this multiple of the
# building's own half-diagonal (from Google's measured roof ``area_sqm``),
# the panels have SPRAWLED across several adjacent structures (the
# Excelsior-Vittoria symptom). The oversized cluster would blow up the zoom
# AND collapse the vertical lift, so above this ratio we discard it and
# frame the building (area-based size) centred on the pin instead.
_SPRAWL_RATIO = 1.2
# A genuine multi-building sprawl is GAPPY — panels on a tennis court + a
# flat roof with empty space between, so they cover only a small fraction
# of their bounding box. A packed single roof (a capannone, even when
# Google UNDER-measured its area_sqm) covers most of its box. We only treat
# the oversized cluster as sprawl when its panel coverage is below this
# density, so a dense real roof is never wrongly tightened/clipped.
_PANEL_AREA_M2 = 1.65
_SPRAWL_MAX_DENSITY = 0.35


def _base_radius_m(insight: RoofInsight) -> int:
    """Radius (m) for the base-image tile + crop, sized to the WHOLE roof.

    Takes the larger of two half-diagonals:
      * the panel cluster's bounds (captures elongation when panels span the
        roof), and
      * an estimate from the Solar ``area_sqm`` of the whole roof —
        ``sqrt(area/2)`` (the half-diagonal of an equivalent square).

    The area term is what fixes the common industrial case: Google Solar
    fills only PART of a big roof with panels, so the panel cluster
    under-measures the building and the capannone gets clipped. ×padding,
    then clamped to ``[_MIN_BASE_RADIUS_M, _MAX_BASE_RADIUS_M]``.
    """
    cluster_half_diag = 0.0
    if insight.panels:
        lats = [p.lat for p in insight.panels]
        lngs = [p.lng for p in insight.panels]
        clat = sum(lats) / len(lats)
        height_m = (max(lats) - min(lats)) * 111_320.0
        width_m = (max(lngs) - min(lngs)) * 111_320.0 * math.cos(math.radians(clat))
        cluster_half_diag = math.hypot(width_m, height_m) / 2.0

    area_half_diag = math.sqrt(insight.area_sqm / 2.0) if insight.area_sqm > 0 else 0.0
    half_diag_m = max(cluster_half_diag, area_half_diag)
    if half_diag_m <= 0.0:
        return _MIN_BASE_RADIUS_M
    needed = half_diag_m * _adaptive_padding_factor(max(8.0, half_diag_m))
    return max(_MIN_BASE_RADIUS_M, min(_MAX_BASE_RADIUS_M, int(math.ceil(needed))))


# Equatorial circumference of the Earth (WGS-84) in metres — Web-Mercator
# ground-resolution math for sizing the fallback satellite tile.
_EARTH_CIRCUMFERENCE_M = 40_075_016.686


def _satellite_zoom_for_radius(lat: float, radius_m: int, *, size: int) -> int:
    """Web-Mercator zoom so a ``size``-px satellite tile spans the WHOLE
    building (~2·radius_m + margin), centred on the point.

    The Mapbox / Google-Static fallback was always fetched at the fixed
    default zoom 19 (~180 m wide tile), so any building needing a radius
    above ~90 m got clipped and the crop could never frame it. We now
    size the tile to ``radius_m`` exactly like the Google Solar path:
    large plants zoom OUT (lower z) so the full footprint is captured,
    while small roofs stay at the sharp default (19).
    """
    cos_lat = max(0.05, math.cos(math.radians(lat)))
    # 16:9 crop pulls width = height·(16/9); leave 25% margin so the
    # widened crop still fits inside the tile without clamping.
    diameter_m = max(1.0, 2.0 * radius_m * 1.25)
    # Ground width of a `size`-logical-px tile at zoom z is
    #   size · cos_lat · CIRC / (256 · 2**z).  Solve ≥ diameter_m for z.
    k = size * cos_lat * _EARTH_CIRCUMFERENCE_M / 256.0
    z = math.floor(math.log2(k / diameter_m))
    return max(16, min(19, int(z)))


# ── Panel visual style ─────────────────────────────────────────────────────
# Colours are chosen to look like monocrystalline silicon panels seen
# from above at ~45° solar angle: dark base + subtle blue cell sheen +
# silver frame edge.
PANEL_FILL = (18, 32, 62)  # #12203E  — very dark blue
PANEL_CELL_1 = (22, 52, 100)  # #163464  — medium blue cell grid
PANEL_CELL_2 = (30, 70, 130)  # #1E4682  — lighter blue highlight
PANEL_FRAME = (210, 215, 220)  # silver frame edge
PANEL_FRAME_WIDTH = 2  # pixels
# Number of cell columns / rows drawn inside each panel.
CELL_COLS = 6
CELL_ROWS = 10


class SolarRenderingError(Exception):
    """Wraps all non-fatal rendering failures."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_scene3d(
    *,
    aerial_url: str,
    center_lat: float,
    center_lng: float,
    radius_m: int,
    insight: RoofInsight,
    roof_height_m: float = 7.0,
) -> dict[str, object]:
    """Build the ``scene3d`` payload the Remotion 3D composition expects.

    Mirrors ``scene3dSchema`` in
    ``apps/video-renderer/src/compositions/SolarTransition.tsx``.  The
    aerial URL should point at the flat "before" PNG (no panels drawn);
    the video renderer uses it as the ground-plane texture and layers
    3-D panel meshes on top.  Passing this dict as ``scene3d`` in the
    ``/render`` request switches the sidecar to the cinematic
    camera-orbit renderer; omitting it keeps the legacy 2-D path.
    """
    panels = [
        {
            "lat": p.lat,
            "lng": p.lng,
            "azimuthDeg": p.segment_azimuth_deg,
            "orientation": p.orientation,
        }
        for p in insight.panels
    ]
    return {
        "aerialUrl": aerial_url,
        "centerLat": center_lat,
        "centerLng": center_lng,
        "radiusM": radius_m,
        "panels": panels,
        "panelWidthM": insight.panel_width_m,
        "panelHeightM": insight.panel_height_m,
        "roofHeightM": roof_height_m,
    }


def _render_center(lat: float, lng: float, insight: RoofInsight) -> tuple[float, float]:
    """Coordinate su cui centrare base-image e crop del rendering.

    Il punto di verità è dove Solar ha messo i pannelli, NON la
    coordinata grezza del lead. Se le due divergono (es. il roof
    salvato è impreciso e ``findClosest`` aggancia l'edificio a
    centinaia di metri) e centrassimo la tile satellitare sul lead,
    la tile non conterrebbe affatto l'edificio: il crop finirebbe su
    una strada/prato e nano-banana non avrebbe alcun tetto da
    dipingere. Centrando invece sul centroide del cluster di pannelli
    (fallback: centro edificio Solar, poi coordinata lead) la base
    image contiene SEMPRE il tetto che verrà reso. In produzione, dove
    il roof del funnel coincide col building Solar entro pochi metri,
    lo shift è trascurabile e migliora solo l'inquadratura.
    """
    if insight.panels:
        clat = sum(p.lat for p in insight.panels) / len(insight.panels)
        clng = sum(p.lng for p in insight.panels) / len(insight.panels)
        return clat, clng
    if insight.lat and insight.lng:
        return insight.lat, insight.lng
    return lat, lng


async def render_before_only(
    lat: float,
    lng: float,
    insight: RoofInsight,
    *,
    api_key: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> bytes:
    """Return only the BEFORE png — real Google aerial, panels NOT drawn.

    This is the entry point for the AI-painted pipeline (Sprint
    "rendering-v2"): we generate a high-quality real aerial crop here
    and feed it to Gemini Flash Image (via Replicate) which paints
    photorealistic panels on the visible roof. The output of that call
    becomes the AFTER frame.

    Why split it from render_before_after:
      * the legacy PIL-rectangle pipeline was producing the "panels
        look pasted / cube-shaped" rendering the first paying tenant
        complained about. Keeping the legacy function intact (still
        used by tests) lets us swap pipelines without churn.
      * fewer disk writes + one less PNG encode in the AI hot path.
    """
    clat, clng = _render_center(lat, lng, insight)
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=60.0)

    try:
        base_bytes, georef = await _fetch_base_image(
            clat, clng, radius_m=_base_radius_m(insight), api_key=api_key, client=client
        )
    finally:
        if owns_client:
            await client.aclose()

    try:
        before_img, _transform = _load_and_crop(
            base_bytes, clat, clng, insight, radius_m=_base_radius_m(insight), georef=georef
        )
    except Exception as exc:
        raise SolarRenderingError(f"image processing failed: {exc}") from exc

    return _to_png_bytes(before_img)


async def render_before_after(
    lat: float,
    lng: float,
    insight: RoofInsight,
    *,
    api_key: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> tuple[bytes, bytes]:
    """Return ``(before_png_bytes, after_png_bytes)`` for a lead.

    ``insight`` must have been fetched with ``solarPanels`` present (i.e.
    ``insight.panels`` is non-empty).  If it is empty we still produce a
    before image but the after image will be identical (no panels drawn).

    Raises:
        SolarRenderingError: any unrecoverable failure (bad imagery, etc.)
    """
    clat, clng = _render_center(lat, lng, insight)
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=60.0)

    try:
        base_bytes, georef = await _fetch_base_image(
            clat, clng, radius_m=_base_radius_m(insight), api_key=api_key, client=client
        )
    finally:
        if owns_client:
            await client.aclose()

    # Parse + crop.
    try:
        before_img, transform = _load_and_crop(
            base_bytes, clat, clng, insight, radius_m=_base_radius_m(insight), georef=georef
        )
    except Exception as exc:
        raise SolarRenderingError(f"image processing failed: {exc}") from exc

    before_bytes = _to_png_bytes(before_img)

    # 4) Draw panel overlay.
    try:
        after_img = _draw_panels(
            before_img.copy(),
            insight.panels,
            transform,
            insight.panel_width_m,
            insight.panel_height_m,
        )
    except Exception as exc:
        log.warning("solar_rendering.panel_draw_failed", err=str(exc))
        # Non-fatal: after = before (no panels) rather than crashing.
        after_img = before_img

    after_bytes = _to_png_bytes(after_img)

    log.info(
        "solar_rendering.done",
        panels=len(insight.panels),
        before_kb=len(before_bytes) // 1024,
        after_kb=len(after_bytes) // 1024,
    )
    return before_bytes, after_bytes


async def render_before_and_mask(
    lat: float,
    lng: float,
    insight: RoofInsight,
    *,
    api_key: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> tuple[bytes, bytes]:
    """Return ``(before_png, mask_png)`` for the masked-inpaint pipeline.

    ``before_png`` is the real Google aerial crop with NO panels drawn.
    ``mask_png`` is a black image the same size, white exactly where
    solar panels belong (the Solar API panel footprints, dilated and
    feathered). The masked-inpaint service paints photoreal panels
    only inside the white region, then composites the result back over
    this untouched before image — so every pixel outside the mask
    stays byte-identical and the before/after pair is perfectly
    aligned by construction.
    """
    clat, clng = _render_center(lat, lng, insight)
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=60.0)

    try:
        base_bytes, georef = await _fetch_base_image(
            clat, clng, radius_m=_base_radius_m(insight), api_key=api_key, client=client
        )
    finally:
        if owns_client:
            await client.aclose()

    try:
        before_img, transform = _load_and_crop(
            base_bytes, clat, clng, insight, radius_m=_base_radius_m(insight), georef=georef
        )
    except Exception as exc:
        raise SolarRenderingError(f"image processing failed: {exc}") from exc

    mask_img = _build_panel_mask(
        before_img.size,
        insight.panels,
        transform,
        insight.panel_width_m,
        insight.panel_height_m,
    )

    log.info(
        "solar_rendering.before_and_mask_done",
        panels=len(insight.panels),
        size=before_img.size,
    )
    return _to_png_bytes(before_img), _mask_to_png_bytes(mask_img)


# ---------------------------------------------------------------------------
# GeoTIFF loading and cropping
# ---------------------------------------------------------------------------


class _GeoTransform:
    """Affine transform: (lat, lng) ↔ pixel (col, row) in the original image."""

    def __init__(
        self,
        west_lng: float,
        north_lat: float,
        scale_x: float,  # degrees per pixel in longitude
        scale_y: float,  # degrees per pixel in latitude (positive)
        crop_col: int,  # column offset of the crop within the original
        crop_row: int,  # row offset of the crop within the original
    ) -> None:
        self.west_lng = west_lng
        self.north_lat = north_lat
        self.scale_x = scale_x
        self.scale_y = scale_y
        self.crop_col = crop_col
        self.crop_row = crop_row

    def geo_to_crop_pixel(self, lat: float, lng: float) -> tuple[float, float]:
        """Return (col, row) in the *cropped* image coordinate space."""
        col_orig = (lng - self.west_lng) / self.scale_x
        row_orig = (self.north_lat - lat) / self.scale_y
        return col_orig - self.crop_col, row_orig - self.crop_row

    def meters_to_pixels(self, meters: float, lat: float) -> float:
        """Approximate: convert a ground distance in metres to pixels."""
        # One degree latitude ≈ 111 320 m; longitude is compressed by cos(lat).
        deg = meters / 111_320.0
        return deg / self.scale_y  # use latitude scale (scale_y) as base


def _to_doubles(v: object) -> list[float]:
    """Normalise a TIFF tag value into a list of doubles."""
    if isinstance(v, (list, tuple)):
        return [float(x) for x in v]
    if isinstance(v, bytes):
        n = len(v) // 8
        if n == 0:
            raise SolarRenderingError("empty GeoTIFF tag bytes")
        return list(struct.unpack(f"<{n}d", v))
    return [float(v)]  # type: ignore[arg-type]


def _find_georef_page(img: Image.Image) -> Image.Image:
    """Return the first IFD in the TIFF that carries georeference tags.

    Google Solar GeoTIFFs sometimes place the tags in a non-primary IFD
    (e.g. a sub-IFD for the full-resolution layer while IFD0 holds an
    overview).  Pillow's ``Image.open`` returns IFD0 by default, so we
    iterate through ``ImageSequence.Iterator`` to find the right one.

    Raises SolarRenderingError if NO page has both tags, logging the
    tag IDs seen in each page so we can diagnose format surprises.
    """
    pages_seen: list[dict[str, object]] = []
    for idx, page in enumerate(ImageSequence.Iterator(img)):
        tags = getattr(page, "tag_v2", None)
        if tags is None:
            pages_seen.append({"idx": idx, "size": page.size, "tags": None})
            continue
        tag_ids = list(tags.keys())
        pages_seen.append(
            {
                "idx": idx,
                "size": page.size,
                "mode": page.mode,
                "tag_ids": sorted(tag_ids),
            }
        )
        if 33550 in tags and 33922 in tags:
            log.info(
                "solar_rendering.georef_page_found",
                ifd=idx,
                size=page.size,
                mode=page.mode,
            )
            return page
    # Nothing matched — log everything we saw so the error is actionable.
    log.error("solar_rendering.no_georef_page", pages=pages_seen)
    raise SolarRenderingError(
        "GeoTIFF missing ModelPixelScaleTag (33550) or "
        f"ModelTiepointTag (33922) in any IFD — cannot georeference; "
        f"pages_inspected={len(pages_seen)}"
    )


def _parse_geotiff_tags(img: Image.Image) -> tuple[float, float, float, float]:
    """Return (west_lng, north_lat, scale_x_deg, scale_y_deg) from GeoTIFF tags.

    Tag 33550 = ModelPixelScaleTag  (ScaleX, ScaleY, ScaleZ)
    Tag 33922 = ModelTiepointTag    (I, J, K, X, Y, Z)

    Both use doubles; Pillow may return them as tuples or raw bytes.

    Walks every IFD in the TIFF — some GeoTIFF producers (including the
    Google Solar pipeline) put georef metadata in a sub-IFD, not IFD0.
    """
    page = _find_georef_page(img)
    tags = page.tag_v2  # type: ignore[attr-defined]

    scale = _to_doubles(tags[33550])  # (ScaleX, ScaleY, ...)
    tie = _to_doubles(tags[33922])  # (I, J, K, X, Y, Z, ...)

    # Tiepoint: pixel (I, J) maps to geographic (X=lng, Y=lat)
    # For top-left origin (I=J=0): X=west_lng, Y=north_lat
    west_lng = tie[3]
    north_lat = tie[4]
    scale_x = scale[0]  # degrees per pixel in longitude
    scale_y = scale[1]  # degrees per pixel in latitude (positive: row↓ = lat↓)

    if scale_x <= 0 or scale_y <= 0:
        raise SolarRenderingError(
            f"Implausible GeoTIFF scale: scale_x={scale_x}, scale_y={scale_y}"
        )

    return west_lng, north_lat, scale_x, scale_y


def _derive_geo_from_request(
    img: Image.Image,
    *,
    center_lat: float,
    center_lng: float,
    radius_m: int,
) -> tuple[float, float, float, float]:
    """Fall-back geo transform computed from the dataLayers request params.

    Google Solar's GeoTIFFs don't always embed ModelPixelScaleTag /
    ModelTiepointTag.  The image returned by ``dataLayers:get`` covers a
    square region centred on ``(center_lat, center_lng)`` extending
    ``radius_m`` in each direction, so we can reconstruct an adequate
    transform from the image dimensions alone.

    Precision is ~1 pixel over 100 m at Italian latitudes — more than
    enough for drawing panels at their lat/lng to the correct roof.
    """
    img_w, img_h = img.size
    meters_per_deg_lat = 111_320.0
    meters_per_deg_lng = 111_320.0 * math.cos(math.radians(center_lat))
    if meters_per_deg_lng <= 0:
        raise SolarRenderingError(f"Cannot derive geo transform at center_lat={center_lat}")
    width_deg = (2 * radius_m) / meters_per_deg_lng
    height_deg = (2 * radius_m) / meters_per_deg_lat
    west_lng = center_lng - width_deg / 2
    north_lat = center_lat + height_deg / 2
    scale_x = width_deg / img_w
    scale_y = height_deg / img_h
    log.info(
        "solar_rendering.georef_from_request",
        center_lat=center_lat,
        center_lng=center_lng,
        radius_m=radius_m,
        img_w=img_w,
        img_h=img_h,
        scale_x=scale_x,
        scale_y=scale_y,
    )
    return west_lng, north_lat, scale_x, scale_y


async def _fetch_base_image(
    lat: float,
    lng: float,
    *,
    radius_m: int,
    api_key: str | None,
    client: httpx.AsyncClient,
) -> tuple[bytes, tuple[float, float, float, float] | None]:
    """Fetch the aerial base image for a rendering.

    Prefers the Google Solar dataLayers GeoTIFF (sharpest, ~10 cm/px and
    most recent aerial imagery in Italy); falls back to a Google Maps Static
    satellite tile when Google Solar has no coverage at the point, the
    key is exhausted, or the GeoTIFF download fails. Returns
    ``(image_bytes, georef)`` where ``georef`` is ``None`` for the
    GeoTIFF path (its georeference is parsed from the TIFF tags
    downstream), or ``(west_lng, north_lat, scale_x, scale_y)`` for the
    Google Static tile.
    """
    # Primary — Google Solar dataLayers GeoTIFF (highest fidelity in IT).
    try:
        data_layers = await fetch_data_layers(
            lat, lng, radius_m=radius_m, client=client, api_key=api_key
        )
        tiff_bytes = await download_geotiff(data_layers.rgb_url, client=client, api_key=api_key)
        log.info(
            "solar_rendering.base_image",
            source="google_solar",
            quality=data_layers.imagery_quality,
            date=data_layers.imagery_date,
        )
        return tiff_bytes, None
    except (SolarApiNotFound, SolarApiError) as exc:
        # No Google Solar coverage / quota / GeoTIFF error — fall back to a
        # plain satellite tile. We try Mapbox FIRST: its Static Images API
        # uses a public token that works server-side and has reliable rural
        # coverage, unlike Google Maps Static (whose key carries an HTTP-
        # referrer application restriction that 403s server-side calls).
        solar_exc: Exception = exc
        log.warning("solar_rendering.google_solar_fallback", reason=str(exc)[:160])

    fallback_errors: list[str] = []

    # Fallback 1 — Mapbox Static satellite (public token, no referrer block).
    if settings.mapbox_access_token:
        try:
            mapbox_zoom = _satellite_zoom_for_radius(lat, radius_m, size=800)
            tile = await fetch_mapbox_satellite(lat, lng, zoom=mapbox_zoom, client=client)
            log.info(
                "solar_rendering.base_image",
                source="mapbox_static",
                lat=lat,
                lng=lng,
                radius_m=radius_m,
                zoom=mapbox_zoom,
            )
            return tile.image_bytes, tile.georef
        except (MapboxError, httpx.HTTPError) as exc:
            fallback_errors.append(f"mapbox={exc}")
            log.warning("solar_rendering.mapbox_fallback_failed", reason=str(exc)[:160])

    # Fallback 2 — Google Maps Static satellite tile.
    if maps_static_key():
        try:
            gstatic_zoom = _satellite_zoom_for_radius(lat, radius_m, size=640)
            tile = await fetch_google_static_satellite(lat, lng, zoom=gstatic_zoom, client=client)
            log.info(
                "solar_rendering.base_image",
                source="google_static",
                lat=lat,
                lng=lng,
                radius_m=radius_m,
                zoom=gstatic_zoom,
            )
            return tile.image_bytes, tile.georef
        except (GoogleStaticError, httpx.HTTPError) as exc:
            fallback_errors.append(f"google_static={exc}")

    detail = "; ".join(fallback_errors) or "no satellite fallback configured"
    raise SolarRenderingError(
        f"no Google Solar imagery at ({lat}, {lng}); satellite fallbacks failed: {detail}"
    ) from solar_exc


def _load_and_crop(
    image_bytes: bytes,
    center_lat: float,
    center_lng: float,
    insight: RoofInsight,
    *,
    radius_m: int,
    georef: tuple[float, float, float, float] | None = None,
) -> tuple[Image.Image, _GeoTransform]:
    """Open the base image, resolve its georeference, crop to the building.

    Returns ``(cropped_rgb_image, transform)`` where ``transform`` maps
    lat/lng in the *original* image to pixel coordinates in the *crop*.

    When ``georef`` is given (Google Static tile) it is used directly. Otherwise
    the image is treated as a Google Solar GeoTIFF: embedded tags first,
    falling back to a transform derived from the dataLayers request
    params when Google's imagery omits those tags.
    """
    img = Image.open(io.BytesIO(image_bytes))
    if georef is not None:
        west_lng, north_lat, scale_x, scale_y = georef
    else:
        # Parse geo-reference tags BEFORE convert() — Pillow's convert()
        # returns a plain Image copy that no longer carries tag_v2 TIFF
        # metadata.
        try:
            west_lng, north_lat, scale_x, scale_y = _parse_geotiff_tags(img)
        except SolarRenderingError as exc:
            # Fall back to computing bounds from the request parameters.
            log.warning("solar_rendering.georef_fallback", reason=str(exc))
            west_lng, north_lat, scale_x, scale_y = _derive_geo_from_request(
                img,
                center_lat=center_lat,
                center_lng=center_lng,
                radius_m=radius_m,
            )
    # Force RGB so downstream code always deals with 3-channel images.
    img = img.convert("RGB")

    img_w, img_h = img.size

    # Pixels per metre (latitude direction, more stable than longitude)
    px_per_m = 1.0 / (scale_y * 111_320.0)

    # Compute crop radius. We prefer to derive it from the *panel
    # cluster bounds* (the actual roof footprint where Solar API placed
    # eligible panels) rather than `area_sqm` (the entire roof segment
    # area, which on multi-segment buildings can be much larger than
    # the part that's actually covered with panels). This gives us a
    # tighter, more accurate frame around what we want the AI model
    # to focus on.
    roof_half_h_m: float | None = None
    force_pin_center = False
    if insight.panels:
        panel_lats = [p.lat for p in insight.panels]
        panel_lngs = [p.lng for p in insight.panels]
        cluster_height_m = (max(panel_lats) - min(panel_lats)) * 111_320.0
        cluster_width_m = (
            (max(panel_lngs) - min(panel_lngs)) * 111_320.0 * math.cos(math.radians(center_lat))
        )
        # Half-diagonal of the panel cluster — the smallest circle
        # that still contains every panel, centred on the cluster.
        half_diag_m = math.hypot(cluster_width_m, cluster_height_m) / 2.0
        # Building size implied by Google's measured roof area (half-diagonal
        # of an equivalent square) — our reference for SPRAWL detection.
        area_half_diag_m = math.sqrt(insight.area_sqm / 2.0) if insight.area_sqm > 0 else 0.0
        # Panel coverage of the cluster bounding box. Low ⇒ gappy ⇒ the
        # cluster spans several structures (true sprawl). High ⇒ a packed
        # single roof we must NOT tighten even if it dwarfs the (often
        # under-measured) area_sqm.
        bbox_area_m2 = max(cluster_width_m, 1.0) * max(cluster_height_m, 1.0)
        panel_density = (len(insight.panels) * _PANEL_AREA_M2) / bbox_area_m2
        is_sprawl = (
            area_half_diag_m > 0
            and half_diag_m > _SPRAWL_RATIO * area_half_diag_m
            and panel_density < _SPRAWL_MAX_DENSITY
        )
        if is_sprawl:
            # SPRAWL: panels span well beyond the measured building, gappily →
            # findClosest hit several adjacent structures. Discard the bogus
            # oversized cluster: frame the building (area-based) on the pin,
            # and size the lift to the building so the downward scroll works.
            force_pin_center = True
            effective_half_diag_m = max(8.0, area_half_diag_m)
            roof_half_h_m = area_half_diag_m
        else:
            # DENSE roof — centre on the PIN and zoom OUT just enough to
            # contain the whole building FROM the pin (never clip), rather
            # than hugging the cluster centroid. We measure the building's
            # reach from the pin and take the 92nd percentile so a single
            # stray panel can't inflate the frame; the area estimate is a
            # floor. This errs toward zoom-OUT: an off-centre pin or an
            # under-measured area still keeps the whole roof in view.
            cos_lat = math.cos(math.radians(center_lat))
            reach_from_pin = sorted(
                math.hypot(
                    (p.lat - center_lat) * 111_320.0,
                    (p.lng - center_lng) * 111_320.0 * cos_lat,
                )
                for p in insight.panels
            )
            vreach_from_pin = sorted(abs(p.lat - center_lat) * 111_320.0 for p in insight.panels)
            pct_idx = min(len(reach_from_pin) - 1, int(0.92 * len(reach_from_pin)))
            # Floor at 8 m so single-panel residential cases still get a
            # sensible frame instead of a 1m-wide pinhole.
            effective_half_diag_m = max(8.0, area_half_diag_m, reach_from_pin[pct_idx])
            roof_half_h_m = max(8.0, vreach_from_pin[pct_idx])
        padding = _adaptive_padding_factor(effective_half_diag_m)
        crop_radius_m = effective_half_diag_m * padding
        log.info(
            "solar_rendering.adaptive_zoom",
            panels=len(insight.panels),
            cluster_half_diag_m=round(half_diag_m, 1),
            area_half_diag_m=round(area_half_diag_m, 1),
            panel_density=round(panel_density, 3),
            sprawl=force_pin_center,
            padding=round(padding, 3),
            crop_radius_m=round(crop_radius_m, 1),
        )
    else:
        # Fallback: no panels in the insight (tiny / unsuitable roof).
        # Use the legacy sqrt(area) approximation with conservative padding.
        roof_diag_m = math.sqrt(max(insight.area_sqm, 50.0))
        crop_radius_m = roof_diag_m * PADDING_FACTOR_FALLBACK

    # Half-height del crop (in pixel originali). Il floor garantisce una
    # risoluzione minima anche su cluster piccoli.
    crop_radius_px = max(OUTPUT_H // 2, int(crop_radius_m * px_per_m))

    # Reserve vertical headroom so the default downward-scroll below can
    # lift the roof toward the top WITHOUT clipping it. Only grows the crop
    # for roofs that would otherwise fill the frame; wide capannoni
    # (diagonal-sized) keep their tighter frame unchanged.
    if roof_half_h_m is not None:
        crop_radius_px = max(crop_radius_px, int(roof_half_h_m * px_per_m * _VLIFT_HEADROOM))

    # Centre on the PIN — always. The lead pin is the business's verified
    # Google Maps location; the zoom above is sized to contain the whole
    # building FROM the pin (dense) or the measured building (sprawl), so an
    # off-centre pin or under-measured area still keeps the roof in frame.
    # We deliberately err toward zoom-OUT and never clip. (This replaces the
    # old cluster-centroid centring, which followed Google's findClosest onto
    # neighbour roofs on complexes like Excelsior.)
    cluster_lat = center_lat
    cluster_lng = center_lng

    # Centre pixel of the roof within the full image.
    center_col = (cluster_lng - west_lng) / scale_x
    center_row = (north_lat - cluster_lat) / scale_y

    # Crop box 16:9 (landscape): altezza = 2·crop_radius_px (contiene il
    # cluster in verticale), larghezza allargata di 16/9 per dare
    # contesto laterale. Poi clampato ai bordi dell'immagine.
    half_h = crop_radius_px
    half_w = int(crop_radius_px * OUTPUT_ASPECT)

    # DEFAULT downward scroll — applied to EVERY render. Translate the crop
    # DOWN so the roof's centre lands at _ROOF_CENTER_Y of the frame height
    # (above the geometric middle), leaving the lower band clear for the
    # blue "Risparmio annuo" strip. Previously this shift was clamped to the
    # roof's own top margin and collapsed to ~0 whenever the building filled
    # the frame, so the roof sat dead-centre and half under the strip — the
    # "dimezzato" look. The headroom reserved above guarantees it now lifts.
    focus_shift_px = int((0.5 - _ROOF_CENTER_Y) * 2 * half_h)
    if roof_half_h_m is not None:
        roof_half_h_px = roof_half_h_m * px_per_m
        max_shift = half_h - roof_half_h_px - _FOCUS_SAFETY * half_h
        focus_shift_px = int(max(0.0, min(focus_shift_px, max_shift)))
    else:
        # No panel geometry to bound the roof — cap the blind lift so an
        # unknown-size roof can't be clipped.
        focus_shift_px = int(max(0.0, min(focus_shift_px, 0.10 * half_h)))

    # Finestra di crop centrata sul cluster ma SEMPRE contenuta
    # nell'immagine: prima limito la dimensione alla tile, poi faccio
    # *scorrere* la finestra dentro i bordi invece di troncarla. Il
    # vecchio clamp indipendente di left/right poteva invertire la box
    # (right < left → PIL "Coordinate 'right' is less than 'left'")
    # quando il cluster di pannelli cadeva oltre il bordo della tile —
    # es. quando il findClosest di Solar aggancia un edificio spostato
    # rispetto al punto richiesto.
    win_w = min(2 * half_w, img_w)
    win_h = min(2 * half_h, img_h)
    cx = center_col
    cy = center_row + focus_shift_px
    left = int(round(cx - win_w / 2))
    top = int(round(cy - win_h / 2))
    left = max(0, min(left, img_w - win_w))
    top = max(0, min(top, img_h - win_h))
    right = left + win_w
    bottom = top + win_h

    # La finestra può non essere 16:9 se l'immagine era più piccola del
    # crop richiesto su un asse — rifilo il lato in eccesso.
    cur_w = right - left
    cur_h = bottom - top
    if cur_w / max(cur_h, 1) > OUTPUT_ASPECT:
        right = left + int(cur_h * OUTPUT_ASPECT)
    else:
        bottom = top + int(cur_w / OUTPUT_ASPECT)

    crop = img.crop((left, top, right, bottom))
    crop = crop.resize((OUTPUT_W, OUTPUT_H), Image.LANCZOS)

    # Scale factor from original pixels → resized output pixels. Crop e
    # output sono entrambi 16:9 → la scala è uniforme su x e y.
    scale_factor = OUTPUT_W / max(right - left, 1)

    # Absolute original pixel → scaled output pixel. Crop e output sono
    # entrambi 16:9 quindi un singolo scale_factor è corretto su x e y.
    transform = _GeoTransformScaled(
        west_lng=west_lng,
        north_lat=north_lat,
        scale_x=scale_x,
        scale_y=scale_y,
        crop_col=left,
        crop_row=top,
        scale_factor=scale_factor,
    )

    return crop, transform


class _GeoTransformScaled(_GeoTransform):
    """Like _GeoTransform but also applies the resize scale factor."""

    def __init__(self, *, scale_factor: float, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.scale_factor = scale_factor

    def geo_to_crop_pixel(self, lat: float, lng: float) -> tuple[float, float]:
        col_orig = (lng - self.west_lng) / self.scale_x
        row_orig = (self.north_lat - lat) / self.scale_y
        col_crop = (col_orig - self.crop_col) * self.scale_factor
        row_crop = (row_orig - self.crop_row) * self.scale_factor
        return col_crop, row_crop

    def meters_to_pixels(self, meters: float, lat: float) -> float:
        deg = meters / 111_320.0
        px_per_deg = 1.0 / self.scale_y
        return deg * px_per_deg * self.scale_factor


# ---------------------------------------------------------------------------
# Panel overlay drawing
# ---------------------------------------------------------------------------


def _rotate_corners(
    cx: float, cy: float, half_w: float, half_h: float, angle_deg: float
) -> list[tuple[float, float]]:
    """Return 4 corners of a rectangle rotated CW by ``angle_deg`` in screen
    coordinates (y-axis pointing down).

    CW screen rotation matrix:
      x' =  x·cos θ + y·sin θ
      y' = -x·sin θ + y·cos θ
    """
    theta = math.radians(angle_deg)
    cos_a = math.cos(theta)
    sin_a = math.sin(theta)
    local = [(-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)]
    return [(cx + dx * cos_a + dy * sin_a, cy - dx * sin_a + dy * cos_a) for dx, dy in local]


def _panel_rotation_deg(panel: SolarPanel) -> float:
    """Clockwise rotation applied to the panel rectangle before drawing.

    ``panel_width_m`` (short side) is passed as ``w_px`` to ``_draw_panel_on``
    and ``panel_height_m`` (long side) as ``h_px``.  At rotation=0° the panel
    appears tall and narrow (portrait); at 90° it appears wide and short
    (landscape).

    Google Solar azimuth = direction the panel FACES.  For LANDSCAPE the
    long axis runs PERPENDICULAR to the azimuth, so:
      azimuth 0°  (N) → long axis runs N-S  → tall in image → 0° rotation
      azimuth 90° (E) → long axis runs E-W  → wide in image → 0° rotation after (90+90)%180
      azimuth 180°(S) → long axis runs E-W  → wide in image → 90° rotation
    PORTRAIT: long axis parallel to azimuth direction.
    """
    azimuth = panel.segment_azimuth_deg % 360.0
    if panel.orientation == "PORTRAIT":
        return azimuth % 180.0  # long axis parallel to azimuth, wrapped to [0,180)
    # LANDSCAPE: long axis perpendicular to azimuth
    return (azimuth + 90.0) % 180.0


def _draw_panel_on(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    w_px: float,
    h_px: float,
    angle_deg: float,
) -> None:
    """Draw a single panel: filled rectangle + cell grid + frame."""
    half_w = w_px / 2
    half_h = h_px / 2

    corners = _rotate_corners(cx, cy, half_w, half_h, angle_deg)
    # Main fill
    draw.polygon(corners, fill=PANEL_FILL)

    # Cell grid — divide the panel interior into CELL_COLS × CELL_ROWS cells.
    # We draw grid lines by interpolating along the panel edges.
    def _interp(p1: tuple[float, float], p2: tuple[float, float], t: float) -> tuple[float, float]:
        return (p1[0] + (p2[0] - p1[0]) * t, p1[1] + (p2[1] - p1[1]) * t)

    tl, tr, br, bl = corners

    # Column dividers (parallel to the short sides)
    for i in range(1, CELL_COLS):
        t = i / CELL_COLS
        p_top = _interp(tl, tr, t)
        p_bot = _interp(bl, br, t)
        draw.line([p_top, p_bot], fill=PANEL_CELL_1, width=max(1, int(w_px / 60)))

    # Row dividers (parallel to the long sides)
    for i in range(1, CELL_ROWS):
        t = i / CELL_ROWS
        p_left = _interp(tl, bl, t)
        p_right = _interp(tr, br, t)
        draw.line([p_left, p_right], fill=PANEL_CELL_1, width=max(1, int(h_px / 80)))

    # Highlight the top-left quadrant to simulate specular reflection.
    highlight_corners = [
        tl,
        _interp(tl, tr, 0.5),
        _interp(tl, br, 0.5),
        _interp(tl, bl, 0.5),
    ]
    # Draw as a semi-transparent lighter polygon using a separate draw pass.
    draw.polygon(highlight_corners, fill=(*PANEL_CELL_2, 60))  # type: ignore[arg-type]

    # Silver frame
    draw.polygon(corners, outline=PANEL_FRAME, width=PANEL_FRAME_WIDTH)


def _draw_panels(
    img: Image.Image,
    panels: list[SolarPanel],
    transform: _GeoTransform,
    panel_width_m: float,
    panel_height_m: float,
) -> Image.Image:
    """Draw all panels onto ``img`` in-place and return it."""
    if not panels:
        return img

    draw = ImageDraw.Draw(img, "RGBA")

    # Use a representative latitude for the metre→pixel conversion.
    ref_lat = panels[0].lat if panels else transform.north_lat - 0.001

    w_px = transform.meters_to_pixels(panel_width_m, ref_lat)
    h_px = transform.meters_to_pixels(panel_height_m, ref_lat)

    # Guard: if the image resolution is too low to show panels, skip.
    if w_px < 2 or h_px < 2:
        log.warning(
            "solar_rendering.panels_too_small_for_image",
            w_px=w_px,
            h_px=h_px,
        )
        return img

    drawn = 0
    for panel in panels:
        cx, cy = transform.geo_to_crop_pixel(panel.lat, panel.lng)
        # Skip panels that fall outside the crop area (can happen at edges).
        if not (-w_px < cx < img.width + w_px and -h_px < cy < img.height + h_px):
            continue
        angle = _panel_rotation_deg(panel)
        _draw_panel_on(draw, cx, cy, w_px, h_px, angle)
        drawn += 1

    log.debug("solar_rendering.panels_drawn", total=len(panels), drawn=drawn)
    return img.convert("RGB")  # flatten RGBA → RGB for PNG output


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone-chain convex hull of a set of 2-D points."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _build_panel_mask(
    size: tuple[int, int],
    panels: list[SolarPanel],
    transform: _GeoTransform,
    panel_width_m: float,
    panel_height_m: float,
) -> Image.Image:
    """Return an 'L' mask: white where panels go, black everywhere else.

    Panels are grouped by roof segment; for each segment we fill the
    convex hull of every panel corner — a single clean contiguous
    polygon. A per-panel mask (individual dilated rectangles) produces
    a lumpy, holed blob and the inpainter fills it just as lumpily; a
    clean per-segment polygon lets the model lay down a tidy
    rectangular array. The mask is then dilated a few pixels and
    lightly feathered so the composite blends at the edges.
    """
    mask = Image.new("L", size, 0)
    if not panels:
        return mask

    draw = ImageDraw.Draw(mask)
    ref_lat = panels[0].lat
    w_px = transform.meters_to_pixels(panel_width_m, ref_lat)
    h_px = transform.meters_to_pixels(panel_height_m, ref_lat)
    if w_px < 2 or h_px < 2:
        log.warning("solar_rendering.mask_panels_too_small", w_px=w_px, h_px=h_px)
        return mask

    # Collect every panel's 4 corners, grouped by roof segment.
    segments: dict[int, list[tuple[float, float]]] = {}
    for panel in panels:
        cx, cy = transform.geo_to_crop_pixel(panel.lat, panel.lng)
        angle = _panel_rotation_deg(panel)
        corners = _rotate_corners(cx, cy, w_px / 2, h_px / 2, angle)
        segments.setdefault(panel.segment_index, []).extend(corners)

    for seg_points in segments.values():
        hull = _convex_hull(seg_points)
        if len(hull) >= 3:
            draw.polygon(hull, fill=255)

    # Dilate a few px (MaxFilter) for a small working margin, then a
    # gentle blur so the inpaint composite feathers into the roof.
    mask = mask.filter(ImageFilter.MaxFilter(7))
    return mask.filter(ImageFilter.GaussianBlur(radius=2.0))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True, compress_level=6)
    return buf.getvalue()


def _mask_to_png_bytes(img: Image.Image) -> bytes:
    """Encode an 'L' (grayscale) mask to PNG without flattening to RGB."""
    buf = io.BytesIO()
    img.convert("L").save(buf, format="PNG", optimize=True, compress_level=6)
    return buf.getvalue()


def normalize_to_output_dimensions(png_bytes: bytes) -> bytes:
    """Resize any PNG to exactly OUTPUT_W×OUTPUT_H.

    The AFTER frame from nano-banana frequently comes back at a
    different resolution than the BEFORE crop (e.g. 1344×768 vs
    1536×864). The before/after pair is composited pixel-for-pixel by
    the crossfade sidecar, so a size mismatch makes the roof jump
    during the reveal. Forcing the AFTER frame back to the canonical
    16:9 output size guarantees the two frames are dimensionally
    identical; the prompt framing-lock keeps the *content* aligned.

    Returns the input unchanged if it is already the right size.
    """
    img = Image.open(io.BytesIO(png_bytes))
    if img.size == (OUTPUT_W, OUTPUT_H):
        return png_bytes
    resized = img.convert("RGB").resize((OUTPUT_W, OUTPUT_H), Image.LANCZOS)
    return _to_png_bytes(resized)


# ---------------------------------------------------------------------------
# Convenience: fetch insight + render in one call (used by tests / admin)
# ---------------------------------------------------------------------------


async def render_lead(
    lat: float,
    lng: float,
    *,
    api_key: str | None = None,
) -> tuple[bytes, bytes]:
    """Fetch building insight + render before/after in a single call.

    Suitable for one-off admin endpoints and tests.  Production creative
    agent should pass a pre-fetched ``RoofInsight`` to avoid duplicate API
    calls.
    """
    async with httpx.AsyncClient(timeout=60.0) as client:
        insight = await fetch_building_insight(lat, lng, client=client, api_key=api_key)
        return await render_before_after(lat, lng, insight, api_key=api_key, http_client=client)


# ---------------------------------------------------------------------------
# Post-render strip overlay
# ---------------------------------------------------------------------------

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Debian/Ubuntu (Railway base)
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",  # macOS dev
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)


def _resolve_strip_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Try a few common font paths; fall back to PIL's bitmap default
    so we never crash inside the rendering pipeline because a font is
    missing on a particular image (the strip is preferable to no
    after.png at all)."""
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Best-effort hex → RGB. Accepts '#RGB' / '#RRGGBB' / 'RRGGBB';
    falls back to a neutral navy if the input is malformed."""
    s = (hex_color or "").lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return (24, 48, 84)
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return (24, 48, 84)


def _format_eur_it(value: float | int) -> str:
    """Italian thousand separator: 34881 → '34.881'."""
    return f"{int(round(float(value))):,}".replace(",", ".")


def bake_savings_strip(
    after_png_bytes: bytes,
    *,
    savings_eur: float | int,
    kwp: float | None = None,
    brand_color_hex: str,
    label: str = "RISPARMIO ANNUO STIMATO",
    include_text: bool = True,
) -> bytes:
    """Stamp a solid brand-color strip across the bottom of the after.png.

    Layout intenzionalmente speculare a quello che faceva l'overlay
    ffmpeg in ``apps/video-renderer`` (ora rimosso): striscia in fondo,
    label maiuscoletto piccolo + riga grossa con "€34.881  ·  132,4 kW".
    Allineata a sinistra (~3,75 % di margine), proporzioni studiate per
    matchare l'overlay video precedente così la transizione before→after
    rivela esattamente lo stesso contenuto.

    Crossfade ffmpeg + strip nel frame di destinazione = la fascia
    compare gradualmente insieme ai pannelli. Niente overlay video
    separato, e l'after.png statico (poster del video + fallback email)
    mostra subito il numero. Best-effort: in caso di errore PIL la
    funzione ritorna gli input bytes invariati.
    """
    try:
        img = Image.open(io.BytesIO(after_png_bytes)).convert("RGB")
        width, height = img.size
        # 14,4 % di altezza — match dell'STRIP_H ffmpeg (104 px su 720 →
        # ~124 px su 864). Floor 96 px per leggibilità su immagini basse.
        strip_h = max(96, int(height * 0.144))
        bg_rgb = _hex_to_rgb(brand_color_hex)
        strip = Image.new("RGB", (width, strip_h), bg_rgb)

        # `include_text=False` mode: si dipinge SOLO il riquadro pieno
        # navy (stesso identico background della strip con testo). Usato
        # sul `before.png` per dare al wipedown una zona inferiore
        # uniforme su entrambi i frame — la tendina passa fra navy e
        # navy, solo il TESTO si rivela nella metà bassa, niente jump
        # apparente fra "aeriale" e "navy" che fa sembrare i frame
        # disallineati.
        if include_text:
            draw = ImageDraw.Draw(strip)
            # Label piccolo (matcha font 21 dell'overlay → ~3 % di 864).
            label_font_size = max(18, int(height * 0.030))
            label_font = _resolve_strip_font(label_font_size)
            # Valore grosso (matcha font 34 dell'overlay → ~4,7 % di 864).
            value_font_size = max(28, int(height * 0.047))
            value_font = _resolve_strip_font(value_font_size)

            value_parts: list[str] = [f"€{_format_eur_it(savings_eur)}/anno"]
            if kwp is not None and kwp > 0:
                kw_str = f"{round(kwp * 10) / 10:g}".replace(".", ",")
                value_parts.append(f"{kw_str} kW")
            value_text = "   ·   ".join(value_parts)

            left_pad = int(width * 0.0375)  # ~58 px su 1536
            # Posizionamento verticale: label sulla riga alta, valore sotto.
            # Ricavato dai bbox per un'ancoraggio robusto al baseline.
            label_bbox = draw.textbbox((0, 0), label, font=label_font)
            value_bbox = draw.textbbox((0, 0), value_text, font=value_font)
            label_h = label_bbox[3] - label_bbox[1]
            value_h = value_bbox[3] - value_bbox[1]
            gap = max(4, int(strip_h * 0.04))
            total_h = label_h + gap + value_h
            top = (strip_h - total_h) // 2

            draw.text(
                (left_pad - label_bbox[0], top - label_bbox[1]),
                label,
                fill=(255, 255, 255, 178),  # ≈ 70 % opacità sul brand bg
                font=label_font,
            )
            draw.text(
                (
                    left_pad - value_bbox[0],
                    top + label_h + gap - value_bbox[1],
                ),
                value_text,
                fill=(255, 255, 255),
                font=value_font,
            )

        out = img.copy()
        out.paste(strip, (0, height - strip_h))
        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        log.warning("solar_rendering.strip_bake_failed", err=str(exc))
        return after_png_bytes
