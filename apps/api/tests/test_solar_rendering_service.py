"""Unit tests for solar_rendering_service.

All tests are network-free.  We create a minimal synthetic GeoTIFF in-memory
(using Pillow's tag_v2 support) and mock the Google Solar HTTP calls so the
full render_before_after pipeline can be exercised without any API key.
"""

from __future__ import annotations

import io
import struct
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from src.services.google_solar_service import (
    DataLayers,
    RoofInsight,
    SolarApiNotFound,
    SolarPanel,
)
from src.services.solar_rendering_service import (
    OUTPUT_SIZE,
    SolarRenderingError,
    _GeoTransformScaled,
    _panel_rotation_deg,
    _parse_geotiff_tags,
    _rotate_corners,
    render_before_after,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_geotiff(
    width: int = 512,
    height: int = 512,
    west_lng: float = 12.0,
    north_lat: float = 42.0,
    scale_x: float = 1e-5,   # ~1 m/pixel at equator
    scale_y: float = 1e-5,
) -> bytes:
    """Return a minimal GeoTIFF (RGB, all white) with proper georeference tags."""
    img = Image.new("RGB", (width, height), color=(200, 200, 200))
    buf = io.BytesIO()

    # ModelPixelScaleTag = 33550: (ScaleX, ScaleY, ScaleZ)
    scale_bytes = struct.pack("<3d", scale_x, scale_y, 0.0)
    # ModelTiepointTag  = 33922: (I, J, K, X=lng, Y=lat, Z)
    tie_bytes = struct.pack("<6d", 0.0, 0.0, 0.0, west_lng, north_lat, 0.0)

    tag_data: dict[int, object] = {
        33550: scale_bytes,
        33922: tie_bytes,
    }
    img.save(buf, format="TIFF", tiffinfo=tag_data)
    return buf.getvalue()


def _make_insight(
    lat: float = 42.0,
    lng: float = 12.0,
    n_panels: int = 4,
) -> RoofInsight:
    """Return a minimal RoofInsight with synthetic panels near (lat, lng)."""
    panels = [
        SolarPanel(
            lat=lat + i * 1e-5,
            lng=lng + i * 1e-5,
            orientation="LANDSCAPE",
            segment_azimuth_deg=180.0,
            yearly_energy_kwh=350.0,
            segment_index=0,
        )
        for i in range(n_panels)
    ]
    return RoofInsight(
        lat=lat,
        lng=lng,
        area_sqm=100.0,
        estimated_kwp=float(n_panels) * 0.4,
        estimated_yearly_kwh=float(n_panels) * 350.0,
        max_panel_count=n_panels,
        panel_capacity_w=400.0,
        dominant_exposure="S",
        pitch_degrees=20.0,
        shading_score=0.9,
        postal_code="00100",
        region_code="IT",
        administrative_area="RM",
        locality="Roma",
        raw={},
        panels=panels,
        panel_width_m=1.045,
        panel_height_m=1.879,
    )


# ---------------------------------------------------------------------------
# _parse_geotiff_tags
# ---------------------------------------------------------------------------


def test_parse_geotiff_tags_returns_correct_values() -> None:
    tiff = _make_geotiff(west_lng=11.5, north_lat=43.2, scale_x=2e-5, scale_y=2e-5)
    img = Image.open(io.BytesIO(tiff))
    west, north, sx, sy = _parse_geotiff_tags(img)
    assert abs(west - 11.5) < 1e-9
    assert abs(north - 43.2) < 1e-9
    assert abs(sx - 2e-5) < 1e-12
    assert abs(sy - 2e-5) < 1e-12


def test_parse_geotiff_tags_raises_on_missing_tags() -> None:
    img = Image.new("RGB", (64, 64))
    buf = io.BytesIO()
    img.save(buf, format="TIFF")
    img2 = Image.open(io.BytesIO(buf.getvalue()))
    with pytest.raises(SolarRenderingError, match="ModelPixelScaleTag"):
        _parse_geotiff_tags(img2)


def test_parse_geotiff_tags_raises_on_implausible_scale() -> None:
    """scale_x = 0 should raise."""
    img = Image.new("RGB", (64, 64))
    buf = io.BytesIO()
    # ScaleX = 0 is invalid
    scale_bytes = struct.pack("<3d", 0.0, 1e-5, 0.0)
    tie_bytes = struct.pack("<6d", 0.0, 0.0, 0.0, 12.0, 42.0, 0.0)
    tag_data: dict[int, object] = {33550: scale_bytes, 33922: tie_bytes}
    img.save(buf, format="TIFF", tiffinfo=tag_data)
    img2 = Image.open(io.BytesIO(buf.getvalue()))
    with pytest.raises(SolarRenderingError, match="scale"):
        _parse_geotiff_tags(img2)


# ---------------------------------------------------------------------------
# _rotate_corners
# ---------------------------------------------------------------------------


def test_rotate_corners_no_rotation() -> None:
    corners = _rotate_corners(cx=10.0, cy=10.0, half_w=3.0, half_h=2.0, angle_deg=0.0)
    assert len(corners) == 4
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    assert min(xs) == pytest.approx(7.0, abs=1e-6)
    assert max(xs) == pytest.approx(13.0, abs=1e-6)
    assert min(ys) == pytest.approx(8.0, abs=1e-6)
    assert max(ys) == pytest.approx(12.0, abs=1e-6)


def test_rotate_corners_90_degrees() -> None:
    """90° CW rotation swaps width and height around the centre."""
    corners = _rotate_corners(cx=0.0, cy=0.0, half_w=4.0, half_h=1.0, angle_deg=90.0)
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    # After 90° CW: half_w (4) maps to the height direction
    assert max(abs(x) for x in xs) == pytest.approx(1.0, abs=1e-5)
    assert max(abs(y) for y in ys) == pytest.approx(4.0, abs=1e-5)


# ---------------------------------------------------------------------------
# _panel_rotation_deg
# ---------------------------------------------------------------------------


def _panel(orientation: str, azimuth: float) -> SolarPanel:
    return SolarPanel(
        lat=42.0, lng=12.0, orientation=orientation,
        segment_azimuth_deg=azimuth, yearly_energy_kwh=350.0, segment_index=0
    )


def test_panel_rotation_landscape_south() -> None:
    """South-facing (azimuth=180°) LANDSCAPE: (180+90)%180 = 90° (wide/horizontal)."""
    rot = _panel_rotation_deg(_panel("LANDSCAPE", 180.0))
    assert rot == pytest.approx(90.0, abs=1e-6)


def test_panel_rotation_landscape_east() -> None:
    """East-facing (azimuth=90°) LANDSCAPE: (90+90)%180 = 0° (tall/vertical)."""
    rot = _panel_rotation_deg(_panel("LANDSCAPE", 90.0))
    assert rot == pytest.approx(0.0, abs=1e-6)


def test_panel_rotation_portrait_south() -> None:
    """South-facing PORTRAIT: long axis parallel to azimuth → 0° (mod 180)."""
    rot = _panel_rotation_deg(_panel("PORTRAIT", 180.0))
    assert rot == pytest.approx(0.0, abs=1e-6)


def test_panel_rotation_portrait_east() -> None:
    rot = _panel_rotation_deg(_panel("PORTRAIT", 90.0))
    assert rot == pytest.approx(90.0, abs=1e-6)


# ---------------------------------------------------------------------------
# _GeoTransformScaled
# ---------------------------------------------------------------------------


def test_geotransform_scaled_center_pixel() -> None:
    """The centre pixel of a GeoTIFF should map back to the reference lat/lng."""
    west_lng, north_lat = 12.0, 42.0
    scale_x = scale_y = 1e-4   # 0.0001 deg/pixel ≈ 11 m/pixel
    scale_factor = 2.0
    crop_col = crop_row = 0

    t = _GeoTransformScaled(
        west_lng=west_lng,
        north_lat=north_lat,
        scale_x=scale_x,
        scale_y=scale_y,
        crop_col=crop_col,
        crop_row=crop_row,
        scale_factor=scale_factor,
    )
    # Pixel (0, 0) in the crop → should be (west_lng, north_lat) after inverse
    col, row = t.geo_to_crop_pixel(north_lat, west_lng)
    assert col == pytest.approx(0.0, abs=1e-6)
    assert row == pytest.approx(0.0, abs=1e-6)


def test_geotransform_meters_to_pixels() -> None:
    """1 m should convert to ~9.0 pixels at 1e-5 deg/pixel scale, factor=1."""
    t = _GeoTransformScaled(
        west_lng=12.0, north_lat=42.0,
        scale_x=1e-5, scale_y=1e-5,
        crop_col=0, crop_row=0,
        scale_factor=1.0,
    )
    # scale_y = 1e-5 deg/px → 1 deg = 100000 px → 1 m ≈ 100000/111320 ≈ 0.899 px
    px = t.meters_to_pixels(1.0, lat=42.0)
    assert px == pytest.approx(1.0 / (1e-5 * 111_320.0), rel=1e-4)


# ---------------------------------------------------------------------------
# render_before_after — end-to-end with mocked network
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_before_after_returns_png_bytes() -> None:
    """Full pipeline with mocked fetch_data_layers + download_geotiff.

    The TIFF uses ~0.11 m/pixel (scale=1e-6) so panel rectangles are ~9 px
    wide — well above the 2-pixel guard in _draw_panels.
    """
    lat, lng = 41.9028, 12.4964   # Rome
    insight = _make_insight(lat=lat, lng=lng, n_panels=6)
    # High-res TIFF: 1e-6 deg/pixel ≈ 0.11 m/pixel.
    # Centre at (500,500) within a 1024×1024 image so the crop has room.
    tiff_bytes = _make_geotiff(
        width=1024, height=1024,
        west_lng=lng - 0.0005,    # 500 px west of centre
        north_lat=lat + 0.0005,   # 500 px north of centre
        scale_x=1e-6,
        scale_y=1e-6,
    )

    fake_data_layers = DataLayers(
        rgb_url="https://solar.googleapis.com/v1/geoTiff:get?id=fake",
        imagery_quality="HIGH",
        imagery_date="2024-01-01",
    )

    with (
        patch(
            "src.services.solar_rendering_service.fetch_data_layers",
            new=AsyncMock(return_value=fake_data_layers),
        ),
        patch(
            "src.services.solar_rendering_service.download_geotiff",
            new=AsyncMock(return_value=tiff_bytes),
        ),
    ):
        before, after = await render_before_after(lat, lng, insight, api_key="fake-key")

    # Both outputs must be valid PNG bytes.
    for label, buf in [("before", before), ("after", after)]:
        img = Image.open(io.BytesIO(buf))
        assert img.format == "PNG", f"{label} is not PNG"
        assert img.size == (OUTPUT_SIZE, OUTPUT_SIZE), f"{label} wrong size: {img.size}"
        assert img.mode == "RGB", f"{label} wrong mode: {img.mode}"

    # after must differ from before because panels were drawn.
    assert before != after, "after image should differ from before (panels drawn)"


@pytest.mark.asyncio
async def test_render_before_after_no_panels_before_equals_after() -> None:
    """With zero panels the after image should be identical to before."""
    lat, lng = 41.9028, 12.4964
    insight = _make_insight(lat=lat, lng=lng, n_panels=0)  # empty panel list
    tiff_bytes = _make_geotiff(
        width=512, height=512,
        west_lng=lng - 0.005,
        north_lat=lat + 0.005,
        scale_x=2e-5,
        scale_y=2e-5,
    )
    fake_dl = DataLayers(rgb_url="https://fake", imagery_quality="HIGH", imagery_date="2024-01-01")

    with (
        patch("src.services.solar_rendering_service.fetch_data_layers", new=AsyncMock(return_value=fake_dl)),
        patch("src.services.solar_rendering_service.download_geotiff", new=AsyncMock(return_value=tiff_bytes)),
    ):
        before, after = await render_before_after(lat, lng, insight, api_key="fake-key")

    # No panels → after == before (pixel-for-pixel).
    assert before == after, "with no panels before and after should be identical"


@pytest.mark.asyncio
async def test_render_before_after_falls_back_when_tiff_lacks_georef_tags() -> None:
    """Google Solar's real GeoTIFFs don't always embed 33550/33922.

    When the tags are missing the service must fall back to a transform
    derived from (center_lat, center_lng, radius_m, image_size) rather
    than error out — the radius/center are already known from the request.
    """
    lat, lng = 41.9028, 12.4964
    insight = _make_insight(lat=lat, lng=lng, n_panels=4)

    # Build a TIFF WITHOUT ModelPixelScaleTag / ModelTiepointTag.
    img = Image.new("RGB", (1024, 1024), color=(180, 180, 180))
    buf = io.BytesIO()
    img.save(buf, format="TIFF")  # no tiffinfo → no georef tags
    tiff_bytes = buf.getvalue()

    fake_dl = DataLayers(
        rgb_url="https://fake",
        imagery_quality="HIGH",
        imagery_date="2024-01-01",
    )
    with (
        patch(
            "src.services.solar_rendering_service.fetch_data_layers",
            new=AsyncMock(return_value=fake_dl),
        ),
        patch(
            "src.services.solar_rendering_service.download_geotiff",
            new=AsyncMock(return_value=tiff_bytes),
        ),
    ):
        before, after = await render_before_after(
            lat, lng, insight, api_key="fake-key"
        )

    # Pipeline completed — both outputs are valid PNG.
    for label, b in [("before", before), ("after", after)]:
        im = Image.open(io.BytesIO(b))
        assert im.format == "PNG", f"{label} not PNG"
        assert im.size == (OUTPUT_SIZE, OUTPUT_SIZE)


@pytest.mark.asyncio
async def test_render_before_after_raises_on_solar_not_found() -> None:
    """SolarApiNotFound from fetch_data_layers should become SolarRenderingError."""
    from src.services.google_solar_service import SolarApiNotFound

    lat, lng = 0.0, 0.0
    insight = _make_insight(lat=lat, lng=lng)

    with (
        patch(
            "src.services.solar_rendering_service.fetch_data_layers",
            new=AsyncMock(side_effect=SolarApiNotFound("no data")),
        ),
    ):
        with pytest.raises(SolarRenderingError, match="no imagery"):
            await render_before_after(lat, lng, insight, api_key="fake-key")
