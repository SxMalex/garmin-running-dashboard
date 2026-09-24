"""
Page Prochaine sortie — Génère un parcours inédit et des objectifs
basés sur les dernières activités et la charge d'entraînement.
"""

import copy
import os
import requests
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from datetime import date, datetime, timedelta

from coach_logic import (
    target_label,
)
from formatting import map_zoom, seconds_to_pace_str, weekday_fr
from forme_logic import parse_recovery
from next_session_logic import SESSION_TYPES as _SESSION_TYPES  # noqa: F401
from next_session_logic import (
    SESSION_TYPES,
    parse_ors_route as _parse_ors_route,
    build_gpx as _build_gpx,
    todays_session,
)
from ui_helpers import (
    cached_coach_context,
    cached_load_activities,
    get_garmin_client,
    render_elevation_profile,
    render_refresh_button,
    render_garmin_attribution,
    get_athlete_id,
    require_login,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Prochaine sortie — Running Dashboard",
    page_icon="🗺️",
    layout="wide",
)

require_login()

_athlete_id = get_athlete_id()

ORS_API_BASE = "https://api.openrouteservice.org/v2"


def _ors_options(session_key: str, prefer_trails: bool, distance_m: int, seed: int) -> dict:
    """Construit les options ORS adaptées au type de séance."""
    # 1 waypoint par km → meilleure précision sur la distance générée
    points = max(4, round(distance_m / 1000))

    # Weightings : quiet évite les grandes routes, green favorise parcs/nature
    if prefer_trails or session_key == "sortie_longue":
        weightings = {"green": 0.8, "quiet": 0.4}
    elif session_key == "tempo":
        # Allure soutenue → chemins plats, calmes, revêtus
        weightings = {"quiet": 0.8}
    elif session_key == "recuperation":
        # Calme et nature, sans efforts inutiles sur terrain difficile
        weightings = {"quiet": 0.8, "green": 0.4}
    else:  # endurance
        weightings = {"quiet": 0.6, "green": 0.3}

    # Steps autorisés pour sortie longue / trails (escaliers = passages légitimes en nature)
    avoid = ["ferries", "fords"] if (prefer_trails or session_key == "sortie_longue") else ["ferries", "fords", "steps"]

    return {
        "avoid_features": avoid,
        "round_trip": {"length": distance_m, "points": points, "seed": seed},
        "profile_params": {"weightings": weightings},
    }



def _get_recent_starts(running_df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """Retourne les N dernières sorties avec coordonnées GPS valides."""
    recent = running_df.sort_values("startTimeLocal", ascending=False)
    valid = recent.dropna(subset=["startLat", "startLon"]).head(n)
    return valid[["startTimeLocal", "activityName", "startLat", "startLon", "distance_km"]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Rendu carte
# ---------------------------------------------------------------------------

def _render_route_map(route: dict, session_color: str) -> None:
    lats, lons = route["lats"], route["lons"]
    center_lat = (min(lats) + max(lats)) / 2
    center_lon = (min(lons) + max(lons)) / 2

    _, _, zoom = map_zoom(lats, lons)

    fig = go.Figure()

    # Contour blanc pour faire ressortir le tracé sur la carte
    fig.add_trace(go.Scattermap(
        lat=lats, lon=lons,
        mode="lines",
        line=dict(width=9, color="white"),
        hoverinfo="none",
        showlegend=False,
    ))

    fig.add_trace(go.Scattermap(
        lat=lats, lon=lons,
        mode="lines",
        line=dict(width=5, color=session_color),
        hoverinfo="none",
        name="Parcours",
    ))

    fig.add_trace(go.Scattermap(
        lat=[lats[0], lats[-1]],
        lon=[lons[0], lons[-1]],
        mode="markers",
        marker=dict(size=16, color=["#0ca30c", "#d03b3b"]),
        text=["Départ / Arrivée", "Arrivée"],
        hoverinfo="text",
        name="Points clés",
    ))

    fig.update_layout(
        map=dict(
            style="open-street-map",
            center=dict(lat=center_lat, lon=center_lon),
            zoom=zoom,
        ),
        height=480,
        margin=dict(l=0, r=0, t=0, b=0),
        showlegend=False,
    )
    st.plotly_chart(fig)


def _render_elevation_profile(route: dict, session_color: str) -> None:
    """Calcule les distances cumulées depuis le tracé ORS et délègue au helper."""
    eles = route["elevations"]
    if not eles:
        return
    lats, lons = route["lats"], route["lons"]
    dists = [0.0]
    for i in range(1, len(lats)):
        dlat = (lats[i] - lats[i - 1]) * 111_000
        dlon = (lons[i] - lons[i - 1]) * 111_000 * np.cos(np.radians(lats[i]))
        dists.append(dists[-1] + np.sqrt(dlat ** 2 + dlon ** 2) / 1000)
    render_elevation_profile(dists, eles, color=session_color, fill_alpha=0.18, height=200)


# ---------------------------------------------------------------------------
# UI principale
# ---------------------------------------------------------------------------

st.title("🗺️ Prochaine sortie")
st.caption(
    "La séance de ton plan Garmin Run Coach, et un parcours inédit généré sur "
    "OpenStreetMap pour la courir. Sans plan actif, la séance est déduite de ta "
    "charge d'entraînement."
)

# Chargement
df, error = cached_load_activities(_athlete_id)
if error:
    st.error(f"Erreur Garmin : {error}")
    st.stop()
if df.empty:
    st.warning("Aucune activité disponible.")
    st.stop()

running_df = df[df["activityType"] == "running"].copy()
if len(running_df) < 3:
    st.warning("Il faut au moins 3 sorties pour générer une recommandation.")
    st.stop()

# Clé ORS
ors_key = os.getenv("ORS_API_KEY", "")


# Récupération du jour (HRV, sommeil) — module la recommandation
@st.cache_data(ttl=3600, show_spinner=False)
def _load_recovery(athlete_id: int, cdate: str) -> tuple:
    client = get_garmin_client()
    recovery = parse_recovery(client.get_hrv(cdate), client.get_sleep(cdate))
    return recovery["hrv_status"], recovery["sleep_score"]


_hrv_status, _sleep_score = _load_recovery(_athlete_id, date.today().isoformat())

# Plan Garmin Run Coach — quand il y en a un d'actif, c'est lui qui décide de la
# séance ; la logique interne ne sert plus que de repli. Le chargement passe par
# ui_helpers pour que l'accueil annonce exactement la même séance.
_today = date.today()
_coach = cached_coach_context(_athlete_id, _today.isoformat())

# Recommandation calculée avant la sidebar pour alimenter les défauts
# `load_df=df` : la fraîcheur qui choisit la séance intègre le sport croisé,
# comme le TSB affiché sur l'Accueil et la page Forme.
_today_session = todays_session(df, _hrv_status, _sleep_score, _coach)
rec, _downgrade = _today_session["rec"], _today_session["downgrade"]
s = SESSION_TYPES[rec["session_key"]]
rec["session"] = s
_coach_task = rec.get("coach_task")

# Sidebar — paramètres
with st.sidebar:
    st.markdown("## ⚙️ Paramètres")

    if not ors_key:
        ors_key = st.text_input(
            "Clé API OpenRouteService",
            type="password",
            help="Obtiens ta clé gratuite sur openrouteservice.org",
        )

    st.divider()
    st.markdown("### Parcours")

    prefer_trails = st.toggle(
        "Préférer les chemins / sentiers",
        value=False,
        help="Privilégie les chemins hors-route dans le tracé",
    )

    custom_dist = st.number_input(
        "Distance (km)",
        min_value=2.0,
        max_value=100.0,
        value=float(rec["target_dist_km"]),
        step=0.5,
        help=f"Recommandation : {rec['target_dist_km']} km",
    )

    custom_elev = st.number_input(
        "D+ cible (m)",
        min_value=0,
        max_value=3000,
        value=int(rec["target_elev"]),
        step=10,
        help=f"Recommandation : {rec['target_elev']} m",
    )
    st.caption("Le D+ réel dépend du terrain — active 'Sentiers' pour plus de dénivelé.")

    st.divider()
    st.markdown("### Point de départ")

    recent_starts = _get_recent_starts(running_df)
    if not recent_starts.empty:
        start_options = [
            f"{row['startTimeLocal'].strftime('%d/%m')} · {row['activityName'][:25]} ({row['distance_km']:.1f} km)"
            for _, row in recent_starts.iterrows()
        ]
        selected_start_idx = st.selectbox(
            "Départ depuis",
            options=range(len(start_options)),
            format_func=lambda i: start_options[i],
            index=0,
            help="Choisis le point de départ parmi tes sorties récentes",
        )
        chosen_start = recent_starts.iloc[selected_start_idx]
        sidebar_start_lat = float(chosen_start["startLat"])
        sidebar_start_lon = float(chosen_start["startLon"])
    else:
        sidebar_start_lat = None
        sidebar_start_lon = None

    render_refresh_button()

# Paramètres finaux (valeurs sidebar ou recommandation par défaut)
target_dist_km = custom_dist
target_elev_m = custom_elev
duration_min = round(target_dist_km * rec["target_pace_sec"] / 60)

# ── Section 0 : Plan Garmin ────────────────────────────────────────────────
if _coach:
    _plan = _coach["plan"]
    _phase = _coach["phase"]
    _bits = [f"**{_plan['name']}**"]
    if _phase:
        _bits.append(f"phase **{_phase['label']}**")
    if _coach["days_to_event"] is not None and _coach["event_date"]:
        _bits.append(
            f"objectif le {_coach['event_date'].strftime('%d/%m/%Y')} "
            f"(**J−{_coach['days_to_event']}**)"
        )
    st.info(" · ".join(_bits), icon="🎽")

# ── Section 1 : Type de séance ─────────────────────────────────────────────
if _coach_task:
    st.markdown(f"## {s['icon']} {_coach_task['name']}")
    _when = (
        "aujourd'hui" if _coach_task["date"] == _today
        else f"{weekday_fr(_coach_task['date'])} {_coach_task['date'].strftime('%d/%m')}"
    )
    st.markdown(
        f"*Séance programmée par le coach Garmin pour **{_when}** — "
        f"{target_label(_coach_task)}, {_coach_task['duration_min']} min.*"
    )
else:
    st.markdown(f"## {s['icon']} {s['label']}")
    st.markdown(f"*{s['description']}*")
    if _coach:
        st.caption(
            "Aucune séance de course programmée par le coach dans les jours qui "
            "viennent — recommandation calculée depuis ta charge d'entraînement."
        )

_coach_alert = _today_session["alert"]
if _coach_alert:
    _causes = []
    if _hrv_status and _hrv_status.upper() != "BALANCED":
        _causes.append(f"HRV {_hrv_status.lower()}")
    if _sleep_score is not None and _sleep_score < 60:
        _causes.append(f"sommeil dégradé (score {_sleep_score})")
    st.warning(
        _coach_alert + (f" Signaux : {', '.join(_causes)}." if _causes else ""),
        icon="🛟",
    )

if rec.get("downgraded_from"):
    _from = _SESSION_TYPES[rec["downgraded_from"]]["label"]
    _causes = []
    if _hrv_status and _hrv_status.upper() != "BALANCED":
        _causes.append(f"HRV {_hrv_status.lower()}")
    if _sleep_score is not None and _sleep_score < 60:
        _causes.append(f"sommeil dégradé (score {_sleep_score})")
    st.info(
        f"Séance rétrogradée depuis **{_from}** : {' et '.join(_causes) or 'récupération dégradée'}. "
        "Détail sur la page Forme & Récup.",
        icon="🛟",
    )

st.divider()

# ── Section 2 : Métriques de forme ────────────────────────────────────────
col1, col2, col3, col4 = st.columns(4)
col1.metric("CTL — Forme", f"{rec['ctl']:.1f}", help="Fitness chronique sur 42 jours")
col2.metric("ATL — Fatigue", f"{rec['atl']:.1f}", help="Fatigue aiguë sur 7 jours")

tsb = rec["tsb"]
if tsb > 10:
    tsb_delta, tsb_dc = "Bien reposé", "normal"
elif tsb > -20:
    tsb_delta, tsb_dc = "Charge normale", "off"
else:
    tsb_delta, tsb_dc = "Récupération nécessaire", "inverse"

col3.metric("TSB — Fraîcheur", f"{tsb:.1f}", delta=tsb_delta, delta_color=tsb_dc)
col4.metric("Repos depuis", f"{rec['days_since']} j", help="Jours depuis la dernière sortie")

st.divider()

# ── Section 3 : Objectifs de la séance ────────────────────────────────────
st.markdown("### Objectifs de la séance")

c1, c2, c3, c4, c5 = st.columns(5)

if _coach_task:
    c1.metric(
        "Durée prescrite", f"{_coach_task['duration_min']} min",
        help="Durée estimée par le coach Garmin pour cette séance.",
    )
    c2.metric(
        "Cible Garmin", target_label(_coach_task),
        help=f"Description Garmin : {_coach_task['description'] or '—'}",
    )
    c3.metric(
        "Distance du parcours", f"{target_dist_km} km",
        help=(
            "Estimée depuis la durée prescrite et ton allure moyenne récente "
            f"({rec['avg_pace_str']}) — Garmin prescrit une durée, le générateur "
            "de parcours a besoin d'une longueur de boucle."
        ),
    )
    c4.metric("D+ cible", f"{target_elev_m} m")
    c5.metric(
        "Date programmée",
        weekday_fr(_coach_task["date"]).capitalize() + _coach_task["date"].strftime(" %d/%m"),
        help=f"Effet visé : {_coach_task['effect'] or '—'}",
    )
else:
    c1.metric("Distance cible", f"{target_dist_km} km",
              help=f"Moyenne récente : {rec['avg_dist']} km")
    c2.metric("Allure cible", rec["target_pace_str"],
              help=f"Allure moyenne récente : {rec['avg_pace_str']}")
    c3.metric("Durée estimée", f"{duration_min} min")
    c4.metric("D+ cible", f"{target_elev_m} m")
    c5.metric(
        "Date suggérée",
        rec["suggested_date_str"],
        help=(
            f"Basé sur ton rythme habituel et ta fatigue actuelle (TSB {rec['tsb']:+.0f}). "
            f"Séance le {rec['suggested_date'].strftime('%d/%m/%Y')}."
        ),
    )

# Fourchette d'allure
pace_min = seconds_to_pace_str(rec["target_pace_sec"] * 0.96)
pace_max = seconds_to_pace_str(rec["target_pace_sec"] * 1.04)
if _coach_task:
    _target = _coach_task["target"]
    if _target.get("pace_sec"):
        st.caption(
            f"Allure de répétition Garmin : **{seconds_to_pace_str(_target['pace_sec'])}** · "
            f"allure d'ensemble retenue pour le parcours et le GPX : "
            f"**{pace_min}** → **{pace_max}**"
        )
    elif _target.get("hr_bpm"):
        st.caption(
            f"Garmin cible la **FC {_target['hr_bpm']} bpm** — allure indicative "
            f"pour le parcours : **{pace_min}** → **{pace_max}**"
        )
    else:
        st.caption(f"Fourchette d'allure conseillée : **{pace_min}** → **{pace_max}**")
else:
    st.caption(f"Fourchette d'allure conseillée : **{pace_min}** → **{pace_max}**")

# ── Programme de la semaine selon le coach ─────────────────────────────────
if _coach and _coach["week"]:
    st.markdown("### Le programme du coach cette semaine")
    _week_rows = []
    for task in _coach["week"]:
        _duration = f"{task['duration_min']} min" if task["duration_min"] else "—"
        if task["rest_day"]:
            _detail, _duration = "Repos imposé par le plan", "—"
        elif task["sport"] != "running":
            # Une cible d'allure ou de FC n'a pas de sens hors course : le nom de
            # la séance de renfo est déjà sa consigne.
            _detail = "—"
        else:
            _detail = target_label(task)
        _week_rows.append({
            "Jour": f"{weekday_fr(task['date']).capitalize()} {task['date'].strftime('%d/%m')}",
            "Séance": task["name"],
            "Sport": {"running": "🏃 Course", "strength_training": "💪 Renfo"}.get(
                task["sport"], task["sport"] or "—"
            ),
            "Cible": _detail,
            "Durée": _duration,
            "Fait": "—" if task["rest_day"] else ("à faire" if task["pending"] else "✓"),
        })
    st.dataframe(pd.DataFrame(_week_rows), hide_index=True, width="stretch")

st.divider()

# ── Section 4 : Parcours ──────────────────────────────────────────────────
st.markdown("### Parcours proposé")

if not ors_key:
    st.warning(
        "Entre ta clé API OpenRouteService dans la barre latérale pour générer le tracé. "
        "Inscription gratuite sur [openrouteservice.org](https://openrouteservice.org/dev/#/signup).",
        icon="🔑",
    )
    st.stop()

if sidebar_start_lat is None:
    st.warning("Impossible de déterminer un point de départ (coordonnées GPS manquantes).")
    st.stop()

start_lat, start_lon = sidebar_start_lat, sidebar_start_lon

# Seed pour la variation du parcours
if "route_seed" not in st.session_state:
    st.session_state["route_seed"] = 1

col_regen, col_info = st.columns([1, 3])
with col_regen:
    if st.button("🔀 Autre variante", width='stretch'):
        st.session_state["route_seed"] += 1

with col_info:
    st.caption(f"Départ : {start_lat:.4f}, {start_lon:.4f} · Variante #{st.session_state['route_seed']}")

# Génération du parcours
distance_m = int(target_dist_km * 1000)
profile = "foot-hiking" if (prefer_trails or rec["session_key"] == "sortie_longue") else "foot-walking"

with st.spinner("Génération du parcours en cours..."):
    url = f"{ORS_API_BASE}/directions/{profile}/geojson"
    headers = {
        "Authorization": ors_key,
        "Content-Type": "application/json",
        "Accept": "application/json, application/geo+json",
    }
    body = {
        "coordinates": [[start_lon, start_lat]],
        "options": _ors_options(
            rec["session_key"],
            prefer_trails,
            distance_m,
            st.session_state["route_seed"],
        ),
        "elevation": True,
        "instructions": False,
    }
    try:
        resp = requests.post(url, json=body, headers=headers, timeout=30)
        resp.raise_for_status()
        route = _parse_ors_route(resp.json())

        # Retry avec distance corrigée si l'écart dépasse 15 %
        if route and abs(route["distance_km"] - target_dist_km) / target_dist_km > 0.15:
            factor = target_dist_km / route["distance_km"]
            retry_body = copy.deepcopy(body)
            retry_body["options"]["round_trip"]["length"] = int(distance_m * factor)
            resp2 = requests.post(url, json=retry_body, headers=headers, timeout=30)
            resp2.raise_for_status()
            corrected = _parse_ors_route(resp2.json())
            if corrected:
                route = corrected
    except requests.HTTPError as e:
        st.error(f"Erreur ORS ({e.response.status_code}) : {e.response.text[:400]}")
        route = None
    except Exception as e:
        st.error(f"Erreur réseau : {e}")
        route = None

if route:
    # Métriques du parcours réel avec écart vs objectif
    dist_delta = route["distance_km"] - target_dist_km
    elev_delta = route["ascent_m"] - target_elev_m
    duration_real = round(route["distance_km"] * rec["target_pace_sec"] / 60)

    r1, r2, r3 = st.columns(3)
    r1.metric(
        "Distance réelle", f"{route['distance_km']} km",
        delta=f"{dist_delta:+.1f} km vs objectif",
        delta_color="off", delta_arrow="off",
    )
    r2.metric(
        "D+ réel", f"{route['ascent_m']} m",
        delta=f"{elev_delta:+.0f} m vs objectif",
        delta_color="off", delta_arrow="off",
    )
    r3.metric("Durée estimée", f"{duration_real} min")

    if abs(dist_delta) / target_dist_km > 0.20:
        st.warning(
            f"Le parcours généré ({route['distance_km']} km) s'écarte de plus de 20% de l'objectif "
            f"({target_dist_km} km). Essaie une autre variante ou ajuste la distance dans la barre latérale.",
            icon="⚠️",
        )

    # Carte
    _render_route_map(route, s["color"])

    # Profil altimétrique
    if route["elevations"]:
        st.markdown("#### Profil altimétrique")
        _render_elevation_profile(route, s["color"])

    # Export GPX
    st.divider()
    _gpx_label = _coach_task["name"] if _coach_task else s["label"]
    gpx_content = _build_gpx(route, _gpx_label, rec["target_pace_str"])
    _gpx_slug = (
        _coach_task["name"].lower().replace(" ", "_") if _coach_task
        else rec["session_key"]
    )
    filename = f"parcours_{_gpx_slug}_{route['distance_km']:.1f}km.gpx"
    st.download_button(
        label="Télécharger le parcours (.gpx)",
        data=gpx_content,
        file_name=filename,
        mime="application/gpx+xml",
        width='stretch',
    )

render_garmin_attribution()
