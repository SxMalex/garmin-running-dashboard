"""
Tests unitaires pour heatmap_logic — logique pure, sans I/O Strava.
"""

import math
import numpy as np
import pytest

from heatmap_logic import (
    HeatmapConfig,
    build_colormaps,
    detect_home,
    grid_bounds_latlon,
    haversine_km,
    normalize,
    rasterize,
    render_count_png,
    render_rgba_png,
    render_white_png,
    track_gps_spread_m,
)


# ---------------------------------------------------------------------------
# haversine_km
# ---------------------------------------------------------------------------

def test_haversine_km_zero_distance():
    assert haversine_km(48.85, 2.35, 48.85, 2.35) == pytest.approx(0.0)


def test_haversine_km_one_degree_latitude():
    # 1° de latitude ≈ 111 km à toute longitude
    d = haversine_km(0.0, 0.0, 1.0, 0.0)
    assert d == pytest.approx(111.0, rel=0.01)


def test_haversine_km_paris_lyon():
    # Paris (48.85, 2.35) → Lyon (45.76, 4.84) ≈ 392 km
    d = haversine_km(48.85, 2.35, 45.76, 4.84)
    assert d == pytest.approx(392.0, rel=0.02)


# ---------------------------------------------------------------------------
# detect_home
# ---------------------------------------------------------------------------

def test_detect_home_returns_dominant_cluster():
    starts = [
        (48.850, 2.350), (48.851, 2.351), (48.852, 2.349),  # cluster Paris
        (48.853, 2.350), (48.849, 2.351),
        (45.760, 4.840),  # un outlier Lyon
    ]
    home_lat, home_lon, n = detect_home(starts)
    assert home_lat == pytest.approx(48.851, abs=0.002)
    assert home_lon == pytest.approx(2.350, abs=0.002)
    assert n == 5


def test_detect_home_empty_raises():
    with pytest.raises(ValueError):
        detect_home([])


# ---------------------------------------------------------------------------
# track_gps_spread_m
# ---------------------------------------------------------------------------

def test_track_gps_spread_treadmill():
    # Tapis : tous les points au même endroit → spread ~0
    pts = [(48.85, 2.35, 3.0, 150, 100.0) for _ in range(20)]
    assert track_gps_spread_m(pts) < 1.0


def test_track_gps_spread_outdoor():
    # 1 km Est-Ouest à Paris ≈ 0.014° de longitude
    pts = [(48.85, 2.35 + i * 0.001, 3.0, 150, 100.0) for i in range(15)]
    spread = track_gps_spread_m(pts)
    assert 800 < spread < 1500  # de l'ordre du km


def test_track_gps_spread_empty():
    assert track_gps_spread_m([]) == 0.0


# ---------------------------------------------------------------------------
# rasterize
# ---------------------------------------------------------------------------

def _make_linear_track(label="t", n=20, base_lat=48.85, base_lon=2.35, step=0.0005):
    """Track linéaire avec speed/HR/alt constants — utile pour tester la painting."""
    return label, [
        (base_lat + i * step, base_lon + i * step, 3.0, 150.0, 100.0 + i)
        for i in range(n)
    ]


def test_rasterize_produces_grid_with_count():
    track = _make_linear_track()
    cfg = HeatmapConfig(meters_per_pixel=5, padding_m=100, track_clip_radius_km=None, blur_sigma_px=2)
    grids = rasterize([track], home_lat=48.85, home_lon=2.35, config=cfg)
    assert grids.width > 0 and grids.height > 0
    assert grids.count.sum() > 0
    assert grids.count.shape == (grids.height, grids.width)


def test_rasterize_count_doubled_for_two_passes():
    track1 = _make_linear_track(label="t1")
    track2 = _make_linear_track(label="t2")  # exactement les mêmes points
    cfg = HeatmapConfig(meters_per_pixel=5, padding_m=100, track_clip_radius_km=None, blur_sigma_px=2)
    g1 = rasterize([track1], 48.85, 2.35, cfg)
    g2 = rasterize([track1, track2], 48.85, 2.35, cfg)
    # Le 2e passage double les valeurs sur les pixels visités
    assert g2.count.max() >= 2 * g1.count.max() - 1


def test_rasterize_empty_tracks_raises():
    cfg = HeatmapConfig()
    with pytest.raises(ValueError):
        rasterize([], 48.85, 2.35, cfg)


def test_rasterize_paints_speed_hr_alt():
    track = _make_linear_track()
    cfg = HeatmapConfig(meters_per_pixel=5, padding_m=100, track_clip_radius_km=None, blur_sigma_px=2)
    grids = rasterize([track], 48.85, 2.35, cfg)
    assert grids.speed_n.sum() > 0
    assert grids.hr_n.sum() > 0
    assert grids.elev_n.sum() > 0  # variation d'altitude → painted
    assert grids.grad_n.sum() > 0


def test_rasterize_handles_none_metrics():
    """Un point sans speed/HR/alt ne plante pas et n'incrémente pas speed_n/hr_n."""
    pts = [
        (48.85 + i * 0.0005, 2.35 + i * 0.0005, None, None, None)
        for i in range(10)
    ]
    cfg = HeatmapConfig(meters_per_pixel=5, padding_m=100, track_clip_radius_km=None, blur_sigma_px=2)
    grids = rasterize([("t", pts)], 48.85, 2.35, cfg)
    assert grids.count.sum() > 0
    assert grids.speed_n.sum() == 0
    assert grids.hr_n.sum() == 0
    assert grids.elev_n.sum() == 0


def test_rasterize_paints_consistent_speed_average():
    """
    Un track avec speed constant = 4 m/s sur tous les points doit produire
    une grille de speed_sum/speed_n dont le ratio (la moyenne) est 4 partout
    où il y a peinture. Vérifie la cohérence de la vectorisation.
    """
    pts = [(48.85 + i * 0.0005, 2.35 + i * 0.0005, 4.0, 150.0, 100.0) for i in range(15)]
    cfg = HeatmapConfig(meters_per_pixel=5, padding_m=100, track_clip_radius_km=None, blur_sigma_px=2)
    grids = rasterize([("t", pts)], 48.85, 2.35, cfg)
    visited = grids.speed_n > 0
    assert visited.any()
    ratio = grids.speed_sum[visited] / grids.speed_n[visited]
    np.testing.assert_allclose(ratio, 4.0, atol=1e-5)


def test_rasterize_clip_excludes_far_tracks():
    near = _make_linear_track(label="near", base_lat=48.85, base_lon=2.35)
    far = _make_linear_track(label="far", base_lat=45.76, base_lon=4.84)  # Lyon
    cfg = HeatmapConfig(meters_per_pixel=10, padding_m=200, track_clip_radius_km=5.0, blur_sigma_px=2)
    grids = rasterize([near, far], home_lat=48.85, home_lon=2.35, config=cfg)
    # Le grid doit être centré sur Paris uniquement
    lon_sw = grid_bounds_latlon(grids)[0][1]
    lon_ne = grid_bounds_latlon(grids)[1][1]
    assert 2.3 < lon_sw < 2.4
    assert 2.3 < lon_ne < 2.5


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------

def test_normalize_count_in_unit_range():
    track = _make_linear_track()
    cfg = HeatmapConfig(meters_per_pixel=5, padding_m=100, track_clip_radius_km=None, blur_sigma_px=2)
    grids = rasterize([track], 48.85, 2.35, cfg)
    layers = normalize(grids, cfg)
    assert 0.0 <= layers.count_norm.min()
    assert layers.count_norm.max() <= 1.0 + 1e-6
    assert layers.count_log_norm.max() <= 1.0 + 1e-6


def test_normalize_flags_set_when_data_present():
    track = _make_linear_track()
    cfg = HeatmapConfig(meters_per_pixel=5, padding_m=100, track_clip_radius_km=None, blur_sigma_px=2)
    grids = rasterize([track], 48.85, 2.35, cfg)
    layers = normalize(grids, cfg)
    assert layers.has_speed
    assert layers.has_hr
    assert layers.has_grad
    assert layers.has_elev


def test_normalize_flags_unset_when_no_metrics():
    pts = [(48.85 + i * 0.0005, 2.35 + i * 0.0005, None, None, None) for i in range(10)]
    cfg = HeatmapConfig(meters_per_pixel=5, padding_m=100, track_clip_radius_km=None, blur_sigma_px=2)
    grids = rasterize([("t", pts)], 48.85, 2.35, cfg)
    layers = normalize(grids, cfg)
    assert not layers.has_speed
    assert not layers.has_hr
    assert not layers.has_grad
    assert not layers.has_elev


def test_normalize_speed_range_matches_data():
    # Vitesses entre 2 et 4 m/s
    pts = [
        (48.85 + i * 0.0005, 2.35 + i * 0.0005, 2.0 + i * 0.1, 150.0, 100.0)
        for i in range(21)  # 2.0 .. 4.0
    ]
    cfg = HeatmapConfig(meters_per_pixel=5, padding_m=100, track_clip_radius_km=None, blur_sigma_px=2, auto_range_pct=5)
    grids = rasterize([("t", pts)], 48.85, 2.35, cfg)
    layers = normalize(grids, cfg)
    lo, hi = layers.speed_range_ms
    assert 1.9 <= lo <= 3.0
    assert 3.0 <= hi <= 4.1


# ---------------------------------------------------------------------------
# colormaps + rendering
# ---------------------------------------------------------------------------

def test_build_colormaps_keys():
    cmaps = build_colormaps()
    assert set(cmaps.keys()) == {"count", "speed", "hr", "elev"}


def test_render_count_png_returns_data_uri():
    norm = np.linspace(0, 1, 100).reshape(10, 10).astype(np.float32)
    cmaps = build_colormaps()
    uri = render_count_png(norm, cmaps["count"])
    assert uri.startswith("data:image/png;base64,")
    assert len(uri) > 100


def test_render_rgba_png_returns_data_uri():
    norm = np.linspace(0, 1, 100).reshape(10, 10).astype(np.float32)
    alpha = np.ones_like(norm) * 0.5
    cmaps = build_colormaps()
    uri = render_rgba_png(norm, alpha, cmaps["speed"])
    assert uri.startswith("data:image/png;base64,")


def test_render_white_png_returns_data_uri():
    alpha = np.linspace(0, 1, 100).reshape(10, 10).astype(np.float32)
    uri = render_white_png(alpha)
    assert uri.startswith("data:image/png;base64,")


# ---------------------------------------------------------------------------
# grid_bounds_latlon
# ---------------------------------------------------------------------------

def test_grid_bounds_latlon_order():
    track = _make_linear_track()
    cfg = HeatmapConfig(meters_per_pixel=5, padding_m=100, track_clip_radius_km=None, blur_sigma_px=2)
    grids = rasterize([track], 48.85, 2.35, cfg)
    (lat_sw, lon_sw), (lat_ne, lon_ne) = grid_bounds_latlon(grids)
    assert lat_sw < lat_ne
    assert lon_sw < lon_ne
    # Doit contenir le point de départ
    assert lat_sw <= 48.85 <= lat_ne
    assert lon_sw <= 2.35 <= lon_ne
