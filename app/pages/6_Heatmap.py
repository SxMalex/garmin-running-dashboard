"""
Page Heatmap — Cartes de chaleur des courses (fréquence, allure, FC, dénivelé).
Inspiré du notebook https://github.com/moresamwilson/running-heatmap.
"""

from datetime import date, timedelta
from typing import Optional

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

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
from ui_helpers import (
    cached_load_activities,
    get_garmin_client,
    render_garmin_attribution,
    get_athlete_id,
    require_login,
)


st.set_page_config(
    page_title="Heatmap — Running Dashboard",
    page_icon="🔥",
    layout="wide",
)

require_login()

_athlete_id = get_athlete_id()

# Borne supérieure du slider "Activités max" — pilote le `limit` du fetch initial
# pour qu'on ne plafonne pas en dessous de ce que l'utilisateur peut demander.
MAX_ACTIVITIES_LIMIT = 300


# ---------------------------------------------------------------------------
# Données
# ---------------------------------------------------------------------------
def load_data(athlete_id: int) -> tuple[pd.DataFrame, Optional[str]]:
    """
    Délègue à cached_load_activities (historique complet, cache partagé).

    Seul le slider « Activités max » borne le travail réel : chaque activité
    retenue coûte un fetch de streams, contrairement à la liste qui est un seul
    appel paginé.
    """
    return cached_load_activities(athlete_id)


@st.cache_data(ttl=86400, show_spinner=False)
def load_track_points(athlete_id: int, activity_id: int) -> list:
    """Fetch les streams d'une activité et retourne des points (lat, lon, speed, hr, alt)."""
    streams = get_garmin_client().get_streams(activity_id)
    latlng = streams.get("latlng") or []
    if not latlng:
        return []
    speed = streams.get("velocity_smooth") or []
    hr = streams.get("heartrate") or []
    alt = streams.get("altitude") or []
    points = []
    for i, ll in enumerate(latlng):
        if not ll or len(ll) < 2:
            continue
        lat, lon = ll[0], ll[1]
        s = speed[i] if i < len(speed) else None
        h = hr[i] if i < len(hr) else None
        a = alt[i] if i < len(alt) else None
        points.append((lat, lon, s, h, a))
    return points


@st.cache_data(ttl=3600, show_spinner="Calcul de la heatmap…")
def compute_heatmap(
    athlete_id: int,
    activity_ids: tuple[int, ...],
    home_lat: float,
    home_lon: float,
    meters_per_pixel: float,
    track_clip_radius_km: float,
    blur_sigma_px: float,
    gps_spread_min_m: float,
):
    """
    Rasterise + normalise les tracks pour la combinaison (athlète, activités, params).

    Les streams individuels sont relus via `load_track_points` (déjà cachée),
    donc cet appel est rapide tant que les streams sont en cache RAM/disque.
    Le résultat est mis en cache par Streamlit : changer uniquement le calque
    actif ne déclenche aucun recalcul.
    """
    config = HeatmapConfig(
        meters_per_pixel=meters_per_pixel,
        padding_m=500.0,
        track_clip_radius_km=track_clip_radius_km,
        blur_sigma_px=blur_sigma_px,
    )
    tracks: list[tuple[str, list]] = []
    for aid in activity_ids:
        pts = load_track_points(athlete_id, aid)
        if pts and track_gps_spread_m(pts) >= gps_spread_min_m:
            tracks.append((str(aid), pts))
    if not tracks:
        return None
    grids = rasterize(tracks, home_lat, home_lon, config)
    layers = normalize(grids, config)
    bounds_sw, bounds_ne = grid_bounds_latlon(grids)
    return layers, bounds_sw, bounds_ne


# ---------------------------------------------------------------------------
# Chargement initial
# ---------------------------------------------------------------------------
st.title("Carte de tes sorties")
st.caption(
    "Cartes de chaleur de tes courses : fréquence des passages, allure moyenne, "
    "FC moyenne, pente absolue et dénivelé signé."
)

df, error = load_data(_athlete_id)
if error:
    st.error(f"Erreur Garmin : {error}")
    st.stop()
if df.empty:
    st.warning("Aucune donnée disponible.")
    st.stop()


# ---------------------------------------------------------------------------
# Sidebar — filtres
# ---------------------------------------------------------------------------
ACTIVITY_TYPE_LABELS = {
    "running": "Course",
    "cycling": "Vélo",
    "walking": "Marche",
    "hiking": "Rando",
}

with st.sidebar:
    st.markdown("## ⚙️ Filtres")

    available_types = sorted(set(df["activityType"].dropna().unique()))
    default_types = ["running"] if "running" in available_types else available_types[:1]
    selected_types = st.multiselect(
        "Type d'activité",
        options=available_types,
        default=default_types,
        format_func=lambda t: ACTIVITY_TYPE_LABELS.get(t, t.capitalize()),
    )

    earliest = df["startTimeLocal"].min().date() if not df.empty else date.today() - timedelta(days=365)
    latest = df["startTimeLocal"].max().date() if not df.empty else date.today()
    default_from = max(earliest, latest - timedelta(days=365))

    col1, col2 = st.columns(2)
    with col1:
        date_from = st.date_input("Du", value=default_from, min_value=earliest, max_value=latest)
    with col2:
        date_to = st.date_input("Au", value=latest, min_value=earliest, max_value=latest)

    radius_km = st.slider("Rayon autour de la maison (km)", 1.0, 50.0, 15.0, 1.0)
    clip_km = st.slider("Clip des tracks (km)", 1.0, 50.0, 12.0, 1.0)
    blur_sigma = st.slider("Flou (px)", 2, 30, 10, 1)
    meters_per_pixel = st.slider("Résolution (m/px)", 3, 20, 5, 1)
    gps_spread_min_m = st.slider("Exclure tapis (spread min m)", 0, 500, 200, 50)
    max_activities = st.slider("Activités max", 10, MAX_ACTIVITIES_LIMIT, 100, 10)



# ---------------------------------------------------------------------------
# Filtrage des activités
# ---------------------------------------------------------------------------
if not selected_types:
    st.warning("Sélectionne au moins un type d'activité.")
    st.stop()

date_from_ts = pd.Timestamp(date_from)
date_to_ts = pd.Timestamp(date_to) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)

filtered = df[
    df["activityType"].isin(selected_types)
    & df["startTimeLocal"].between(date_from_ts, date_to_ts)
    & df["startLat"].notna()
    & df["startLon"].notna()
].sort_values("startTimeLocal", ascending=False).head(max_activities)

if filtered.empty:
    st.warning("Aucune activité avec GPS dans la période sélectionnée.")
    st.stop()

st.caption(
    f"**{len(filtered)} activités** retenues "
    f"({date_from.isoformat()} → {date_to.isoformat()})."
)


# ---------------------------------------------------------------------------
# Détection de la maison (à partir des starts du DataFrame, sans fetch streams)
# ---------------------------------------------------------------------------
starts = list(zip(filtered["startLat"].tolist(), filtered["startLon"].tolist()))
home_lat, home_lon, n_home = detect_home(starts)

home_radius_km = float(radius_km)
filtered = filtered.copy()
filtered["dist_from_home_km"] = filtered.apply(
    lambda r: haversine_km(home_lat, home_lon, r["startLat"], r["startLon"]),
    axis=1,
)
filtered = filtered[filtered["dist_from_home_km"] <= home_radius_km]

if filtered.empty:
    st.warning(f"Aucune activité ne démarre dans un rayon de {home_radius_km:.0f} km de la maison.")
    st.stop()

st.caption(
    f"🏠 Maison auto-détectée : `{home_lat:.4f}, {home_lon:.4f}` "
    f"({n_home} départs dans la cellule la plus dense). "
    f"**{len(filtered)} activités** dans un rayon de {home_radius_km:.0f} km."
)


# ---------------------------------------------------------------------------
# Pré-chargement des streams GPS (peuple le cache de load_track_points)
# ---------------------------------------------------------------------------
activity_ids = [int(x) for x in filtered["activityId"].tolist()]
progress = st.progress(0.0, text="Récupération des tracks GPS…")
total = len(activity_ids)
for i, aid in enumerate(activity_ids, start=1):
    load_track_points(_athlete_id, aid)
    progress.progress(i / total, text=f"Tracks GPS… ({i}/{total})")
progress.empty()


# ---------------------------------------------------------------------------
# Rasterisation + normalisation (résultat caché par Streamlit)
# ---------------------------------------------------------------------------
result = compute_heatmap(
    _athlete_id,
    tuple(sorted(activity_ids)),
    # Arrondi à ~1m de précision pour que le cache reste stable même si
    # `detect_home` réagrège des moyennes sur un set d'activités légèrement
    # différent d'un rerun à l'autre.
    round(float(home_lat), 5),
    round(float(home_lon), 5),
    float(meters_per_pixel),
    float(clip_km),
    float(blur_sigma),
    float(gps_spread_min_m),
)
if result is None:
    st.warning("Aucun track GPS exploitable (toutes les activités semblent indoor ou sans GPS).")
    st.stop()
layers, (sw_lat, sw_lon), (ne_lat, ne_lon) = result

cmaps = build_colormaps()
bounds = [[sw_lat, sw_lon], [ne_lat, ne_lon]]
centre = [(sw_lat + ne_lat) / 2, (sw_lon + ne_lon) / 2]


# ---------------------------------------------------------------------------
# Sélecteur de calque
# ---------------------------------------------------------------------------
LAYER_OPTIONS = [
    ("Fréquence (linéaire)", "count_linear"),
    ("Fréquence (log)", "count_log"),
    ("Allure moyenne", "speed"),
    ("FC moyenne", "hr"),
    ("Pente absolue", "grad"),
    ("Dénivelé signé", "elev"),
]

if "heatmap_active_layer" not in st.session_state:
    st.session_state["heatmap_active_layer"] = LAYER_OPTIONS[0][0]

active_label = st.radio(
    "Calque",
    [opt[0] for opt in LAYER_OPTIONS],
    key="heatmap_active_layer",
    horizontal=True,
)
active_key = next(key for label, key in LAYER_OPTIONS if label == active_label)


def _format_pace(speed_ms: float) -> str:
    if speed_ms <= 0:
        return "—"
    secs = 1000.0 / speed_ms
    return f"{int(secs // 60)}:{int(secs % 60):02d}/km"


# ---------------------------------------------------------------------------
# Rendering du calque actif
# ---------------------------------------------------------------------------
if active_key == "count_linear":
    image_uri = render_count_png(layers.count_norm, cmaps["count"])
    legend_lo, legend_hi = "1 passage", f"{int(layers.count_max)} passages"
elif active_key == "count_log":
    image_uri = render_count_png(layers.count_log_norm, cmaps["count"])
    legend_lo, legend_hi = "1 passage", f"{int(layers.count_max)} passages (log)"
elif active_key == "speed":
    if not layers.has_speed:
        st.warning("Pas de données de vitesse sur cette sélection.")
        st.stop()
    image_uri = render_rgba_png(layers.speed_norm, layers.speed_alpha, cmaps["speed"])
    lo_ms, hi_ms = layers.speed_range_ms
    legend_lo, legend_hi = _format_pace(hi_ms), _format_pace(lo_ms)  # rapide = brillant
elif active_key == "hr":
    if not layers.has_hr:
        st.warning("Pas de données de FC sur cette sélection.")
        st.stop()
    image_uri = render_rgba_png(layers.hr_norm, layers.hr_alpha, cmaps["hr"])
    lo_hr, hi_hr = layers.hr_range_bpm
    legend_lo, legend_hi = f"{lo_hr:.0f} bpm", f"{hi_hr:.0f} bpm"
elif active_key == "grad":
    if not layers.has_grad:
        st.warning("Pas de données d'altitude sur cette sélection.")
        st.stop()
    image_uri = render_white_png(layers.grad_alpha)
    lo_g, hi_g = layers.grad_range_pct
    legend_lo, legend_hi = f"{lo_g:.1f}%", f"{hi_g:.1f}% de pente"
else:  # elev
    if not layers.has_elev:
        st.warning("Pas de données d'altitude sur cette sélection.")
        st.stop()
    # elev_norm est dans [-1, 1] → [0, 1] pour la cmap divergente
    image_uri = render_rgba_png(
        (layers.elev_norm + 1) / 2, layers.elev_alpha, cmaps["elev"]
    )
    legend_lo, legend_hi = "descente", "montée"


# ---------------------------------------------------------------------------
# Folium
# ---------------------------------------------------------------------------
m = folium.Map(location=centre, zoom_start=13, tiles=None, control_scale=True)
# Fond SOMBRE voulu malgré le thème clair : les rampes de heatmap_logic montent
# vers le clair (le passage le plus fréquent est presque blanc) et
# disparaîtraient sur un fond clair. Esri Dark Gray : sans clé (le fond CartoDB
# sombre affiche désormais « API KEY REQUIRED »).
folium.TileLayer(
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/"
          "World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    # Attribution exigée par Esri (celle que renvoie le service lui-même).
    attr="Esri, HERE, Garmin, &copy; OpenStreetMap contributors, and the GIS user community",
    name="Basemap", control=False, max_zoom=16,
).add_to(m)
folium.raster_layers.ImageOverlay(
    image=image_uri,
    bounds=bounds,
    opacity=0.85,
    interactive=False,
    cross_origin=False,
    zindex=1,
).add_to(m)
m.fit_bounds(bounds)

st_folium(m, height=620, width=None, returned_objects=[])

st.caption(f"**Échelle :** {legend_lo} → {legend_hi}")

render_garmin_attribution()
