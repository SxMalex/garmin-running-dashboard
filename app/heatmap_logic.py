"""
Logique pure pour la génération de heatmaps de courses.

Inspiré de https://github.com/moresamwilson/running-heatmap.

Entrée : une liste de tracks `(label, points)` où `points` est une liste de
points `(lat, lon, speed_ms, hr_bpm, alt_m)` — n'importe quel des trois
derniers peut être `None`. Le module ne fait pas d'I/O : il est testable
sans session Garmin ni accès réseau.

Pipeline :
    rasterize -> normalize -> render_*_png
"""

from __future__ import annotations

import base64
import math
from dataclasses import dataclass
from io import BytesIO
from typing import Iterable, Optional, Sequence

import matplotlib.colors as mcolors
import numpy as np
from PIL import Image
from pyproj import Transformer
from scipy.ndimage import gaussian_filter


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HeatmapConfig:
    """
    Configuration de la passe rasterize + normalize.

    Le filtrage par rayon autour de la maison et l'exclusion des tapis
    (`gps_spread_min_m`) sont la responsabilité de l'appelant (cf.
    `track_gps_spread_m` et `haversine_km` exposés par le module).
    """

    meters_per_pixel: float = 5.0
    padding_m: float = 500.0
    track_clip_radius_km: Optional[float] = 12.0
    blur_sigma_px: float = 10.0
    auto_range_pct: float = 5.0
    speed_min_ms: Optional[float] = None
    speed_max_ms: Optional[float] = None
    hr_min_bpm: Optional[float] = None
    hr_max_bpm: Optional[float] = None


# Un point GPS : (lat, lon, speed_ms_or_None, hr_or_None, alt_m_or_None)
GpsPoint = tuple[float, float, Optional[float], Optional[float], Optional[float]]
Track = tuple[str, list[GpsPoint]]


# ---------------------------------------------------------------------------
# Géo utilitaires
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance en km entre deux points GPS."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


# Rayon de voisinage pour `detect_home` (300 m : un pâté de maisons).
HOME_CLUSTER_RADIUS_KM = 0.3
# Au-delà, on échantillonne les CANDIDATS testés (mais chacun compte ses
# voisins sur la totalité des départs) pour garder l'algorithme O(n²) rapide.
HOME_DETECT_MAX_CANDIDATES = 3000


def _haversine_km_np(lat1: float, lon1: float, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    """Version vectorisée de `haversine_km`, pour comparer un point à un tableau."""
    R = 6371.0
    dlat = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)
    a = (
        np.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * np.cos(np.radians(lat2)) * np.sin(dlon / 2) ** 2
    )
    return R * 2 * np.arcsin(np.sqrt(a))


def detect_home(
    starts: Sequence[tuple[float, float]], radius_km: float = HOME_CLUSTER_RADIUS_KM
) -> tuple[float, float, int]:
    """
    Détecte la "maison" comme le point de départ le plus fréquent.

    Compte, pour chaque départ, ses voisins dans un rayon de `radius_km`
    (300 m par défaut) plutôt que de biner sur une grille fixe (`round(lat,
    2)`) : un cluster à cheval sur une frontière de grille se retrouvait coupé
    en deux, perdant face à un cluster plus petit mais entier. Retourne le
    barycentre des voisins du départ qui en a le plus.

    O(n²) sur le nombre de départs, acceptable jusqu'à quelques milliers ;
    au-delà de `HOME_DETECT_MAX_CANDIDATES`, on échantillonne les candidats.

    Lève ValueError si la liste est vide.
    """
    if not starts:
        raise ValueError("Aucun point de départ — impossible de détecter la maison.")

    pts = np.asarray(starts, dtype=float)
    lats, lons = pts[:, 0], pts[:, 1]

    candidate_idx = np.arange(len(pts))
    if len(pts) > HOME_DETECT_MAX_CANDIDATES:
        rng = np.random.default_rng(0)
        candidate_idx = rng.choice(len(pts), size=HOME_DETECT_MAX_CANDIDATES, replace=False)

    best_idx, best_count = int(candidate_idx[0]), -1
    for i in candidate_idx:
        count = int(np.sum(_haversine_km_np(lats[i], lons[i], lats, lons) <= radius_km))
        if count > best_count:
            best_count, best_idx = count, int(i)

    neighbors = _haversine_km_np(lats[best_idx], lons[best_idx], lats, lons) <= radius_km
    home_lat = float(lats[neighbors].mean())
    home_lon = float(lons[neighbors].mean())
    return home_lat, home_lon, int(neighbors.sum())


def track_gps_spread_m(points: Iterable[GpsPoint]) -> float:
    """Étendue GPS approximative d'un track (m). Sert à exclure les tapis."""
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    if not lats:
        return 0.0
    mid_lat = (min(lats) + max(lats)) / 2
    return max(
        (max(lats) - min(lats)) * 111_000,
        (max(lons) - min(lons)) * 111_000 * math.cos(math.radians(mid_lat)),
    )


# ---------------------------------------------------------------------------
# Rasterisation
# ---------------------------------------------------------------------------

@dataclass
class Grids:
    """Grilles brutes (avant blur/normalize) + métadonnées de projection."""

    count: np.ndarray
    speed_sum: np.ndarray
    speed_n: np.ndarray
    hr_sum: np.ndarray
    hr_n: np.ndarray
    grad_sum: np.ndarray
    grad_n: np.ndarray
    elev_sum: np.ndarray
    elev_n: np.ndarray

    x_min_wm: float
    x_max_wm: float
    y_min_wm: float
    y_max_wm: float
    width: int
    height: int


def _utm_crs(home_lat: float, home_lon: float) -> str:
    zone = int((home_lon + 180) / 6) + 1
    base = 32700 if home_lat < 0 else 32600
    return f"EPSG:{base + zone}"


def _pairwise_mean(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """
    Pour chaque paire (a[i], b[i]) — `NaN` signifie "absent" :
      - les deux non-NaN → moyenne
      - un seul non-NaN → celui-là
      - les deux NaN → NaN
    Reproduit la sémantique `s0/s1 is not None` du notebook original.
    """
    out = np.where(np.isnan(a), b, a)
    both = ~np.isnan(a) & ~np.isnan(b)
    out = np.where(both, (a + b) / 2, out)
    return out


def rasterize(
    tracks: Sequence[Track],
    home_lat: float,
    home_lon: float,
    config: HeatmapConfig,
) -> Grids:
    """
    Peint les tracks GPS dans des grilles Web Mercator.

    Pour chaque track :
      - count_grid : incrémenté à chaque point GPS
      - speed_sum/n, hr_sum/n, grad_sum/n, elev_sum/n :
        moyennes par segment (peintes le long du segment Bresenham-style)

    Le calcul du gradient utilise une projection UTM locale pour avoir une
    distance en mètres au sol (Web Mercator déforme avec la latitude).
    """
    if not tracks:
        raise ValueError("Aucun track à rasteriser.")

    to_wm = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    to_utm = Transformer.from_crs("EPSG:4326", _utm_crs(home_lat, home_lon), always_xy=True)

    home_x_utm, home_y_utm = to_utm.transform(home_lon, home_lat)
    clip_m = (
        config.track_clip_radius_km * 1000.0
        if config.track_clip_radius_km is not None
        else None
    )

    # Calcule les bornes du grid à partir des points (clippés au rayon)
    all_xs_wm: list[float] = []
    all_ys_wm: list[float] = []
    for _, pts in tracks:
        if not pts:
            continue
        lats = np.array([p[0] for p in pts])
        lons = np.array([p[1] for p in pts])
        xs_utm, ys_utm = to_utm.transform(lons, lats)
        if clip_m is not None:
            mask = ((xs_utm - home_x_utm) ** 2 + (ys_utm - home_y_utm) ** 2) <= clip_m**2
            if not mask.any():
                continue
            lats = lats[mask]
            lons = lons[mask]
        xs_wm, ys_wm = to_wm.transform(lons, lats)
        all_xs_wm.extend(xs_wm.tolist())
        all_ys_wm.extend(ys_wm.tolist())

    if not all_xs_wm:
        raise ValueError("Tous les tracks sont hors du rayon de clipping.")

    x_min_wm = min(all_xs_wm) - config.padding_m
    x_max_wm = max(all_xs_wm) + config.padding_m
    y_min_wm = min(all_ys_wm) - config.padding_m
    y_max_wm = max(all_ys_wm) + config.padding_m

    grid_w = int((x_max_wm - x_min_wm) / config.meters_per_pixel) + 1
    grid_h = int((y_max_wm - y_min_wm) / config.meters_per_pixel) + 1

    count = np.zeros((grid_h, grid_w), dtype=np.float32)
    speed_sum = np.zeros((grid_h, grid_w), dtype=np.float32)
    speed_n = np.zeros((grid_h, grid_w), dtype=np.float32)
    hr_sum = np.zeros((grid_h, grid_w), dtype=np.float32)
    hr_n = np.zeros((grid_h, grid_w), dtype=np.float32)
    grad_sum = np.zeros((grid_h, grid_w), dtype=np.float32)
    grad_n = np.zeros((grid_h, grid_w), dtype=np.float32)
    elev_sum = np.zeros((grid_h, grid_w), dtype=np.float32)
    elev_n = np.zeros((grid_h, grid_w), dtype=np.float32)

    for _, pts in tracks:
        if not pts:
            continue
        lats = np.array([p[0] for p in pts], dtype=np.float64)
        lons = np.array([p[1] for p in pts], dtype=np.float64)
        speeds = np.array([np.nan if p[2] is None else p[2] for p in pts], dtype=np.float64)
        hrs = np.array([np.nan if p[3] is None else p[3] for p in pts], dtype=np.float64)
        alts = np.array([np.nan if p[4] is None else p[4] for p in pts], dtype=np.float64)

        xs_utm, ys_utm = to_utm.transform(lons, lats)
        xs_wm, ys_wm = to_wm.transform(lons, lats)

        keep_idx = None
        if clip_m is not None:
            mask = ((xs_utm - home_x_utm) ** 2 + (ys_utm - home_y_utm) ** 2) <= clip_m**2
            if not mask.any():
                continue
            keep_idx = np.flatnonzero(mask)
            xs_utm = xs_utm[mask]
            ys_utm = ys_utm[mask]
            xs_wm = xs_wm[mask]
            ys_wm = ys_wm[mask]
            speeds = speeds[mask]
            hrs = hrs[mask]
            alts = alts[mask]

        n = xs_wm.size
        if n == 0:
            continue

        px = (xs_wm - x_min_wm) / config.meters_per_pixel
        py = (y_max_wm - ys_wm) / config.meters_per_pixel

        # Count : un incrément par point GPS dans les bornes.
        xi_pts = np.round(px).astype(np.int64)
        yi_pts = np.round(py).astype(np.int64)
        m_pts = (xi_pts >= 0) & (xi_pts < grid_w) & (yi_pts >= 0) & (yi_pts < grid_h)
        if m_pts.any():
            np.add.at(count, (yi_pts[m_pts], xi_pts[m_pts]), np.float32(1.0))

        if n < 2:
            continue

        # Un segment ne relie deux points compactés que s'ils étaient déjà
        # consécutifs dans le track d'origine. Le clip par rayon retire des
        # points au milieu du parcours (sortie puis retour dans le rayon) :
        # sans ce garde-fou, la ligne peinte reliait directement les deux
        # extrémités du "trou", traçant une corde là où personne n'a couru
        # (repro_heat_chord).
        contig = np.diff(keep_idx) == 1 if keep_idx is not None else np.ones(n - 1, dtype=bool)
        valid_seg = np.flatnonzero(contig)
        if valid_seg.size == 0:
            continue

        # Valeurs par segment (NaN si absent côté capteur).
        seg_speeds = _pairwise_mean(speeds[:-1], speeds[1:])[valid_seg]
        seg_hrs = _pairwise_mean(hrs[:-1], hrs[1:])[valid_seg]

        d_alt = (alts[1:] - alts[:-1])[valid_seg]  # NaN si l'une des altitudes manque
        d_dist = np.sqrt((xs_utm[1:] - xs_utm[:-1]) ** 2 + (ys_utm[1:] - ys_utm[:-1]) ** 2)[valid_seg]
        valid_alt = ~np.isnan(d_alt) & (d_dist >= 0.5)
        with np.errstate(divide="ignore", invalid="ignore"):
            seg_grads = np.where(valid_alt, np.abs(d_alt) / d_dist, np.nan)
        seg_elevs = np.where(valid_alt, d_alt, np.nan)

        # Peinture vectorisée Bresenham-style : pour chaque segment on étale
        # n_steps+1 points sur la ligne, on concatène tous les segments en un
        # gros tableau, puis on accumule via `np.add.at` (gère les doublons).
        px0 = px[:-1][valid_seg]
        py0 = py[:-1][valid_seg]
        dx = (px[1:] - px[:-1])[valid_seg]
        dy = (py[1:] - py[:-1])[valid_seg]
        n_steps = np.maximum(np.maximum(np.abs(dx), np.abs(dy)).astype(np.int64) + 1, 1)
        pts_per_seg = n_steps + 1
        total = int(pts_per_seg.sum())

        seg_idx = np.repeat(np.arange(valid_seg.size), pts_per_seg)
        seg_offsets = np.concatenate(([0], np.cumsum(pts_per_seg)[:-1]))
        step_in_seg = np.arange(total) - seg_offsets[seg_idx]
        t = step_in_seg / n_steps[seg_idx]

        xi = np.round(px0[seg_idx] + t * dx[seg_idx]).astype(np.int64)
        yi = np.round(py0[seg_idx] + t * dy[seg_idx]).astype(np.int64)
        m = (xi >= 0) & (xi < grid_w) & (yi >= 0) & (yi < grid_h)

        sv = seg_speeds[seg_idx]
        hv = seg_hrs[seg_idx]
        gv = seg_grads[seg_idx]
        ev = seg_elevs[seg_idx]

        m_sv = m & ~np.isnan(sv)
        m_hv = m & ~np.isnan(hv)
        m_gv = m & ~np.isnan(gv)
        m_ev = m & ~np.isnan(ev)

        if m_sv.any():
            np.add.at(speed_sum, (yi[m_sv], xi[m_sv]), sv[m_sv].astype(np.float32))
            np.add.at(speed_n, (yi[m_sv], xi[m_sv]), np.float32(1.0))
        if m_hv.any():
            np.add.at(hr_sum, (yi[m_hv], xi[m_hv]), hv[m_hv].astype(np.float32))
            np.add.at(hr_n, (yi[m_hv], xi[m_hv]), np.float32(1.0))
        if m_gv.any():
            np.add.at(grad_sum, (yi[m_gv], xi[m_gv]), gv[m_gv].astype(np.float32))
            np.add.at(grad_n, (yi[m_gv], xi[m_gv]), np.float32(1.0))
        if m_ev.any():
            np.add.at(elev_sum, (yi[m_ev], xi[m_ev]), ev[m_ev].astype(np.float32))
            np.add.at(elev_n, (yi[m_ev], xi[m_ev]), np.float32(1.0))

    return Grids(
        count=count,
        speed_sum=speed_sum, speed_n=speed_n,
        hr_sum=hr_sum, hr_n=hr_n,
        grad_sum=grad_sum, grad_n=grad_n,
        elev_sum=elev_sum, elev_n=elev_n,
        x_min_wm=x_min_wm, x_max_wm=x_max_wm,
        y_min_wm=y_min_wm, y_max_wm=y_max_wm,
        width=grid_w, height=grid_h,
    )


# ---------------------------------------------------------------------------
# Blur + normalisation
# ---------------------------------------------------------------------------

@dataclass
class NormalizedLayers:
    count_norm: np.ndarray
    count_log_norm: np.ndarray
    speed_norm: np.ndarray
    speed_alpha: np.ndarray
    speed_range_ms: tuple[float, float]
    hr_norm: np.ndarray
    hr_alpha: np.ndarray
    hr_range_bpm: tuple[float, float]
    grad_alpha: np.ndarray
    grad_range_pct: tuple[float, float]
    elev_norm: np.ndarray
    elev_alpha: np.ndarray
    has_speed: bool = False
    has_hr: bool = False
    has_grad: bool = False
    has_elev: bool = False
    count_max: float = 0.0


def _presence_alpha(sample_n_grid: np.ndarray, sigma: float, pct: float = 10.0) -> np.ndarray:
    binary = (sample_n_grid > 0).astype(np.float32)
    blurred = gaussian_filter(binary, sigma=sigma)
    visible = blurred[binary > 0]
    if visible.size == 0:
        return blurred
    sat = float(np.percentile(visible, pct))
    return np.clip(blurred / sat, 0, 1) if sat > 0 else blurred


def normalize(grids: Grids, config: HeatmapConfig) -> NormalizedLayers:
    """Floute les grilles avec un noyau gaussien et normalise par calque."""
    sigma = config.blur_sigma_px
    pct = config.auto_range_pct

    # Frequency
    b_count = gaussian_filter(grids.count, sigma=sigma)
    count_max = float(b_count.max())
    if count_max > 0:
        count_norm = b_count / count_max
        count_log_norm = np.log1p(b_count) / np.log1p(count_max)
    else:
        count_norm = b_count
        count_log_norm = b_count

    # Speed
    b_speed_sum = gaussian_filter(grids.speed_sum, sigma=sigma)
    b_speed_n = gaussian_filter(grids.speed_n, sigma=sigma)
    mean_speed = np.where(b_speed_n > 0, b_speed_sum / np.maximum(b_speed_n, 1e-9), 0)
    visited_speeds = mean_speed[b_speed_n > 0.01]
    has_speed = visited_speeds.size > 0
    if has_speed:
        s_lo = config.speed_min_ms if config.speed_min_ms is not None else float(np.percentile(visited_speeds, pct))
        s_hi = config.speed_max_ms if config.speed_max_ms is not None else float(np.percentile(visited_speeds, 100 - pct))
        if s_hi <= s_lo:
            s_hi = s_lo + 1e-3
        speed_norm = np.clip((mean_speed - s_lo) / (s_hi - s_lo), 0, 1)
        speed_norm = np.where(b_speed_n > 0, speed_norm, 0)
        sw = gaussian_filter(speed_norm * (b_speed_n > 0.01).astype(np.float32), sigma=sigma)
        sn = gaussian_filter((b_speed_n > 0.01).astype(np.float32), sigma=sigma)
        speed_norm = np.where(sn > 0, sw / np.maximum(sn, 1e-9), 0)
    else:
        s_lo, s_hi = 1.0, 5.0
        speed_norm = np.zeros_like(mean_speed)
    speed_alpha = _presence_alpha(grids.speed_n, sigma)

    # HR
    b_hr_sum = gaussian_filter(grids.hr_sum, sigma=sigma)
    b_hr_n = gaussian_filter(grids.hr_n, sigma=sigma)
    mean_hr = np.where(b_hr_n > 0, b_hr_sum / np.maximum(b_hr_n, 1e-9), 0)
    visited_hrs = mean_hr[grids.hr_n > 0]
    has_hr = visited_hrs.size > 0
    if has_hr:
        h_lo = config.hr_min_bpm if config.hr_min_bpm is not None else float(np.percentile(visited_hrs, pct))
        h_hi = config.hr_max_bpm if config.hr_max_bpm is not None else float(np.percentile(visited_hrs, 100 - pct))
        if h_hi <= h_lo:
            h_hi = h_lo + 1.0
        hr_norm = np.clip((mean_hr - h_lo) / (h_hi - h_lo), 0, 1)
        hr_norm = np.where(b_hr_n > 0, hr_norm, 0)
        hw = gaussian_filter(hr_norm * (grids.hr_n > 0).astype(np.float32), sigma=sigma)
        hn = gaussian_filter((grids.hr_n > 0).astype(np.float32), sigma=sigma)
        hr_norm = np.where(hn > 0, hw / np.maximum(hn, 1e-9), 0)
    else:
        h_lo, h_hi = 100.0, 180.0
        hr_norm = np.zeros_like(mean_hr)
    hr_alpha = _presence_alpha(grids.hr_n, sigma)

    # Gradient absolu
    b_grad_sum = gaussian_filter(grids.grad_sum, sigma=sigma)
    b_grad_n = gaussian_filter(grids.grad_n, sigma=sigma)
    mean_grad = np.where(b_grad_n > 0, b_grad_sum / np.maximum(b_grad_n, 1e-9), 0)
    visited_grads = mean_grad[b_grad_n > 0.01]
    has_grad = (grids.grad_n > 0).any() and visited_grads.size > 0
    if has_grad:
        g_lo = float(np.percentile(visited_grads, pct))
        g_hi = float(np.percentile(visited_grads, 100 - pct))
        if g_hi <= g_lo:
            g_hi = g_lo + 1e-3
        grad_norm = np.clip((mean_grad - g_lo) / (g_hi - g_lo), 0, 1)
        grad_norm = np.where(b_grad_n > 0, grad_norm, 0)
        grad_presence = _presence_alpha(grids.grad_n, sigma)
        grad_alpha = grad_presence * (0.15 + 0.85 * grad_norm)
    else:
        g_lo = g_hi = 0.0
        grad_alpha = np.zeros_like(mean_grad)

    # Elevation change (signed)
    b_elev_sum = gaussian_filter(grids.elev_sum, sigma=sigma)
    b_elev_n = gaussian_filter(grids.elev_n, sigma=sigma)
    mean_elev = np.where(b_elev_n > 0, b_elev_sum / np.maximum(b_elev_n, 1e-9), 0)
    has_elev = (grids.elev_n > 0).any()
    if has_elev:
        visited_elevs = mean_elev[b_elev_n > 0.01]
        if visited_elevs.size > 0:
            e_abs_hi = max(
                abs(float(np.percentile(visited_elevs, pct))),
                abs(float(np.percentile(visited_elevs, 100 - pct))),
            )
            if e_abs_hi <= 0:
                e_abs_hi = 1e-3
            elev_norm = np.clip(mean_elev / e_abs_hi, -1, 1)
            elev_norm = np.where(b_elev_n > 0, elev_norm, 0)
            ew = gaussian_filter(elev_norm * (b_elev_n > 0.01).astype(np.float32), sigma=sigma)
            en = gaussian_filter((b_elev_n > 0.01).astype(np.float32), sigma=sigma)
            elev_norm = np.where(en > 0, ew / np.maximum(en, 1e-9), 0)
        else:
            elev_norm = np.zeros_like(mean_elev)
        elev_alpha = _presence_alpha(grids.elev_n, sigma)
    else:
        elev_norm = np.zeros_like(mean_elev)
        elev_alpha = np.zeros_like(mean_elev)

    return NormalizedLayers(
        count_norm=count_norm,
        count_log_norm=count_log_norm,
        speed_norm=speed_norm,
        speed_alpha=speed_alpha,
        speed_range_ms=(s_lo, s_hi),
        hr_norm=hr_norm,
        hr_alpha=hr_alpha,
        hr_range_bpm=(h_lo, h_hi),
        grad_alpha=grad_alpha,
        grad_range_pct=(g_lo * 100, g_hi * 100),
        elev_norm=elev_norm,
        elev_alpha=elev_alpha,
        has_speed=has_speed,
        has_hr=has_hr,
        has_grad=bool(has_grad),
        has_elev=bool(has_elev),
        count_max=float(grids.count.max()),
    )


# ---------------------------------------------------------------------------
# Colormaps
# ---------------------------------------------------------------------------

def _build_cmap(name: str, nodes: list[tuple[float, tuple[float, float, float, float]]]) -> mcolors.LinearSegmentedColormap:
    pos = [n[0] for n in nodes]
    cdict: dict[str, list[tuple[float, float, float]]] = {}
    for ci, ch in enumerate(("red", "green", "blue", "alpha")):
        vals = [n[1][ci] for n in nodes]
        cdict[ch] = [(pos[i], vals[i], vals[i]) for i in range(len(pos))]
    return mcolors.LinearSegmentedColormap(name, cdict, N=512)


def build_colormaps() -> dict[str, mcolors.LinearSegmentedColormap]:
    """Les 5 colormaps utilisées par les calques (count, speed, hr, elev + miroir)."""
    cmap_count = _build_cmap("count", [
        (0.00, (0.00, 0.00, 0.00, 0.00)),
        (0.01, (0.40, 0.10, 0.00, 0.55)),
        (0.20, (0.99, 0.30, 0.01, 0.80)),
        (0.50, (1.00, 0.65, 0.00, 0.92)),
        (0.80, (1.00, 0.92, 0.20, 0.97)),
        (1.00, (1.00, 1.00, 0.80, 1.00)),
    ])
    cmap_speed = _build_cmap("speed", [
        (0.00, (0.00, 0.10, 0.40, 1.00)),
        (0.35, (0.05, 0.30, 0.80, 1.00)),
        (0.65, (0.20, 0.55, 1.00, 1.00)),
        (0.85, (0.55, 0.75, 1.00, 1.00)),
        (1.00, (0.85, 0.92, 1.00, 1.00)),
    ])
    cmap_hr = _build_cmap("hr", [
        (0.00, (0.40, 0.05, 0.05, 1.00)),
        (0.35, (0.70, 0.12, 0.12, 1.00)),
        (0.65, (0.92, 0.28, 0.28, 1.00)),
        (0.85, (1.00, 0.65, 0.65, 1.00)),
        (1.00, (1.00, 0.90, 0.90, 1.00)),
    ])
    cmap_elev = _build_cmap("elev", [
        (0.00, (0.12, 0.80, 0.22, 1.00)),
        (0.25, (0.06, 0.52, 0.16, 1.00)),
        (0.45, (0.06, 0.20, 0.10, 1.00)),
        (0.50, (0.18, 0.18, 0.18, 1.00)),
        (0.55, (0.22, 0.08, 0.30, 1.00)),
        (0.75, (0.52, 0.06, 0.75, 1.00)),
        (1.00, (0.82, 0.22, 1.00, 1.00)),
    ])
    return {"count": cmap_count, "speed": cmap_speed, "hr": cmap_hr, "elev": cmap_elev}


# ---------------------------------------------------------------------------
# Encodage PNG -> data URI
# ---------------------------------------------------------------------------

def _to_data_uri(rgba_u8: np.ndarray) -> str:
    buf = BytesIO()
    Image.fromarray(rgba_u8, mode="RGBA").save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def render_count_png(norm: np.ndarray, cmap: mcolors.LinearSegmentedColormap) -> str:
    arr = (cmap(norm) * 255).clip(0, 255).astype(np.uint8)
    return _to_data_uri(arr)


def render_rgba_png(rgb_norm: np.ndarray, alpha_norm: np.ndarray, cmap: mcolors.LinearSegmentedColormap) -> str:
    arr = cmap(rgb_norm).copy()
    arr[:, :, 3] = alpha_norm
    return _to_data_uri((arr * 255).clip(0, 255).astype(np.uint8))


def render_white_png(alpha_norm: np.ndarray) -> str:
    h, w = alpha_norm.shape
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[:, :, :3] = 255
    arr[:, :, 3] = (alpha_norm * 255).clip(0, 255).astype(np.uint8)
    return _to_data_uri(arr)


# ---------------------------------------------------------------------------
# Helpers de bornes pour Folium
# ---------------------------------------------------------------------------

def grid_bounds_latlon(grids: Grids) -> tuple[tuple[float, float], tuple[float, float]]:
    """Retourne ((lat_sw, lon_sw), (lat_ne, lon_ne)) en WGS84 pour ImageOverlay."""
    from_wm = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    lon_nw, lat_nw = from_wm.transform(grids.x_min_wm, grids.y_max_wm)
    lon_se, lat_se = from_wm.transform(grids.x_max_wm, grids.y_min_wm)
    return (lat_se, lon_nw), (lat_nw, lon_se)
