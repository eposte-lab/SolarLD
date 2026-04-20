"""Unit tests for the distance subscore."""

from __future__ import annotations

from src.services.scoring import distance_score
from src.services.scoring.geo import haversine_km, province_to_region


# Napoli reference coords (roof)
NAPOLI_LAT = 40.8518
NAPOLI_LNG = 14.2681


def test_distance_zero_km_scores_100() -> None:
    # HQ at the exact same spot as the roof
    assert distance_score(NAPOLI_LAT, NAPOLI_LNG, NAPOLI_LAT, NAPOLI_LNG) == 100


def test_distance_5km_scores_100() -> None:
    # Pozzuoli ~11 km west but we use ~5km via lng delta
    assert distance_score(NAPOLI_LAT, NAPOLI_LNG, NAPOLI_LAT, NAPOLI_LNG + 0.05) == 100


def test_distance_20km_scores_80() -> None:
    # ~0.18 degree lng at 40.85°N ≈ 15km
    assert distance_score(NAPOLI_LAT, NAPOLI_LNG, NAPOLI_LAT, NAPOLI_LNG + 0.20) == 80


def test_distance_50km_scores_60() -> None:
    # ~0.55° lng ≈ 46 km
    assert distance_score(NAPOLI_LAT, NAPOLI_LNG, NAPOLI_LAT, NAPOLI_LNG + 0.55) == 60


def test_distance_80km_scores_40() -> None:
    assert distance_score(NAPOLI_LAT, NAPOLI_LNG, NAPOLI_LAT, NAPOLI_LNG + 0.95) == 40


def test_distance_500km_scores_20() -> None:
    # Napoli ↔ Milano (~660km)
    milan_lat, milan_lng = 45.4642, 9.1900
    assert distance_score(NAPOLI_LAT, NAPOLI_LNG, milan_lat, milan_lng) == 20


def test_distance_missing_hq_returns_neutral() -> None:
    assert distance_score(NAPOLI_LAT, NAPOLI_LNG, None, None) == 50
    assert distance_score(NAPOLI_LAT, NAPOLI_LNG, NAPOLI_LAT, None) == 50


def test_distance_missing_roof_returns_neutral() -> None:
    assert distance_score(None, None, NAPOLI_LAT, NAPOLI_LNG) == 50


def test_haversine_km_napoli_milan_within_tolerance() -> None:
    km = haversine_km(40.8518, 14.2681, 45.4642, 9.1900)
    assert 640 <= km <= 680


def test_haversine_km_antipodal_not_zero() -> None:
    # Basic sanity: non-trivial distance is non-zero
    assert haversine_km(0, 0, 1, 1) > 100


def test_province_to_region_common_cases() -> None:
    assert province_to_region("NA") == "Campania"
    assert province_to_region("mi") == "Lombardia"
    assert province_to_region(" RM ") == "Lazio"
    assert province_to_region("AO") == "Valle d'Aosta"


def test_province_to_region_unknown_returns_none() -> None:
    assert province_to_region("ZZ") is None
    assert province_to_region("") is None
    assert province_to_region(None) is None
