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

import httpx
from PIL import Image, ImageDraw, ImageSequence

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

log = get_logger(__name__)

# Output image size in pixels (square).  At 10 cm/pixel this captures
# roughly 100 m × 100 m around the building — enough context to look
# good in email without excessive file size.
OUTPUT_SIZE = 1024

# Padding around the roof footprint, expressed as a multiplier of the
# roof's bounding-box half-diagonal (computed from panel cluster bounds
# when available, falls back to sqrt(area) otherwise).
#
# Values <1 crop tightly; >2 shows a lot of surroundings.
#
# Why 1.4 (was 2.2): the AI video model uses the start/end frames as
# spatial reference; a wide crop with lots of pavement and lawn
# confuses it into placing panels off-roof. Tighter framing keeps the
# roof at >70% of the frame area, which is enough signal for the
# model to lock onto the correct surface. We still leave a small
# margin so the roof edges aren't chopped off on non-square buildings.
PADDING_FACTOR = 1.4

# ── Panel visual style ─────────────────────────────────────────────────────
# Colours are chosen to look like monocrystalline silicon panels seen
# from above at ~45° solar angle: dark base + subtle blue cell sheen +
# silver frame edge.
PANEL_FILL = (18, 32, 62)          # #12203E  — very dark blue
PANEL_CELL_1 = (22, 52, 100)       # #163464  — medium blue cell grid
PANEL_CELL_2 = (30, 70, 130)       # #1E4682  — lighter blue highlight
PANEL_FRAME = (210, 215, 220)      # silver frame edge
PANEL_FRAME_WIDTH = 2              # pixels
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
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=60.0)

    try:
        # 1) Fetch DataLayers to get the rgb_url.
        try:
            data_layers = await fetch_data_layers(
                lat, lng, radius_m=50, client=client, api_key=api_key
            )
        except SolarApiNotFound as exc:
            raise SolarRenderingError(f"no imagery at ({lat}, {lng})") from exc
        except SolarApiError as exc:
            raise SolarRenderingError(f"Solar API error: {exc}") from exc

        log.info(
            "solar_rendering.imagery_found",
            lat=lat,
            lng=lng,
            quality=data_layers.imagery_quality,
            date=data_layers.imagery_date,
        )

        # 2) Download the GeoTIFF.
        try:
            tiff_bytes = await download_geotiff(
                data_layers.rgb_url, client=client, api_key=api_key
            )
        except SolarApiError as exc:
            raise SolarRenderingError(f"GeoTIFF download failed: {exc}") from exc

    finally:
        if owns_client:
            await client.aclose()

    # 3) Parse + crop.
    try:
        before_img, transform = _load_and_crop(
            tiff_bytes, lat, lng, insight, radius_m=50
        )
    except Exception as exc:
        raise SolarRenderingError(f"image processing failed: {exc}") from exc

    before_bytes = _to_png_bytes(before_img)

    # 4) Draw panel overlay.
    try:
        after_img = _draw_panels(before_img.copy(), insight.panels, transform,
                                 insight.panel_width_m, insight.panel_height_m)
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


# ---------------------------------------------------------------------------
# GeoTIFF loading and cropping
# ---------------------------------------------------------------------------

class _GeoTransform:
    """Affine transform: (lat, lng) ↔ pixel (col, row) in the original image."""

    def __init__(
        self,
        west_lng: float,
        north_lat: float,
        scale_x: float,   # degrees per pixel in longitude
        scale_y: float,   # degrees per pixel in latitude (positive)
        crop_col: int,    # column offset of the crop within the original
        crop_row: int,    # row offset of the crop within the original
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
        pages_seen.append({
            "idx": idx,
            "size": page.size,
            "mode": page.mode,
            "tag_ids": sorted(tag_ids),
        })
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
    tie = _to_doubles(tags[33922])    # (I, J, K, X, Y, Z, ...)

    # Tiepoint: pixel (I, J) maps to geographic (X=lng, Y=lat)
    # For top-left origin (I=J=0): X=west_lng, Y=north_lat
    west_lng = tie[3]
    north_lat = tie[4]
    scale_x = scale[0]   # degrees per pixel in longitude
    scale_y = scale[1]   # degrees per pixel in latitude (positive: row↓ = lat↓)

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
        raise SolarRenderingError(
            f"Cannot derive geo transform at center_lat={center_lat}"
        )
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


def _load_and_crop(
    tiff_bytes: bytes,
    center_lat: float,
    center_lng: float,
    insight: RoofInsight,
    *,
    radius_m: int,
) -> tuple[Image.Image, _GeoTransform]:
    """Open the GeoTIFF, extract georeference, crop to the building.

    Returns ``(cropped_rgb_image, transform)`` where ``transform`` maps
    lat/lng in the *original* image to pixel coordinates in the *crop*.

    Tries embedded GeoTIFF tags first; falls back to a transform derived
    from the dataLayers request params (center + radius + image size)
    when Google's imagery omits those tags, which it commonly does.
    """
    img = Image.open(io.BytesIO(tiff_bytes))
    # Parse geo-reference tags BEFORE convert() — Pillow's convert() returns
    # a plain Image copy that no longer carries tag_v2 TIFF metadata.
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
    if insight.panels:
        panel_lats = [p.lat for p in insight.panels]
        panel_lngs = [p.lng for p in insight.panels]
        cluster_height_m = (max(panel_lats) - min(panel_lats)) * 111_320.0
        cluster_width_m = (
            (max(panel_lngs) - min(panel_lngs))
            * 111_320.0
            * math.cos(math.radians(center_lat))
        )
        # Half-diagonal of the panel cluster — the smallest circle
        # that still contains every panel, centred on the cluster.
        half_diag_m = math.hypot(cluster_width_m, cluster_height_m) / 2.0
        # Floor at 8 m so single-panel residential cases still get a
        # sensible frame instead of a 1m-wide pinhole.
        crop_radius_m = max(8.0, half_diag_m) * PADDING_FACTOR
    else:
        # Fallback: no panels in the insight (tiny / unsuitable roof).
        # Use the legacy sqrt(area) approximation.
        roof_diag_m = math.sqrt(max(insight.area_sqm, 50.0))
        crop_radius_m = roof_diag_m * PADDING_FACTOR

    crop_radius_px = max(OUTPUT_SIZE // 2, int(crop_radius_m * px_per_m))

    # Centre the crop on the panel cluster centroid when possible
    # (more stable than the building centre point Solar API returns,
    # which can land on a parking lot for L-shaped buildings).
    if insight.panels:
        cluster_lat = sum(p.lat for p in insight.panels) / len(insight.panels)
        cluster_lng = sum(p.lng for p in insight.panels) / len(insight.panels)
    else:
        cluster_lat = center_lat
        cluster_lng = center_lng

    # Centre pixel of the roof within the full image.
    center_col = (cluster_lng - west_lng) / scale_x
    center_row = (north_lat - cluster_lat) / scale_y

    # Clamp crop box to image bounds.
    left = max(0, int(center_col - crop_radius_px))
    top = max(0, int(center_row - crop_radius_px))
    right = min(img_w, int(center_col + crop_radius_px))
    bottom = min(img_h, int(center_row + crop_radius_px))

    # Keep it square for the video composition.
    side = min(right - left, bottom - top)
    right = left + side
    bottom = top + side

    crop = img.crop((left, top, right, bottom))
    crop = crop.resize((OUTPUT_SIZE, OUTPUT_SIZE), Image.LANCZOS)

    # Scale factor from original pixels → resized output pixels.
    scale_factor = OUTPUT_SIZE / max(side, 1)

    transform = _GeoTransform(
        west_lng=west_lng,
        north_lat=north_lat,
        scale_x=scale_x,
        scale_y=scale_y,
        # Adjusted crop offset and scale so geo_to_crop_pixel gives output-size coords.
        crop_col=left - (crop_radius_px - side / 2) * (1 - 1 / scale_factor),
        crop_row=top - (crop_radius_px - side / 2) * (1 - 1 / scale_factor),
    )
    # Override to use a simpler, accurate transform: absolute pixel → scaled output.
    # (Reuse _GeoTransform but inject the scale factor into the pixel computation.)
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
    return [
        (cx + dx * cos_a + dy * sin_a, cy - dx * sin_a + dy * cos_a)
        for dx, dy in local
    ]


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
        return azimuth % 180.0   # long axis parallel to azimuth, wrapped to [0,180)
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True, compress_level=6)
    return buf.getvalue()


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
