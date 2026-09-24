"""Helpers d'affichage Streamlit partagés entre pages."""

from datetime import date
from typing import Sequence

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import chart_theme  # active le template Plotly gar_dark
from coach_logic import load_coach_context
from formatting import map_zoom
from ui_mode import render_mode_toggle
from ui_theme import inject_theme
from garmin_client import (
    ACTIVITY_HISTORY_LIMIT,
    GarminClient,
    athlete_id_of,
    resume_session,
    safe_load_activities,
)

ACCENT_COLOR = chart_theme.PACE  # la couleur suit l'entité (allure/tracé)

# Profondeur d'historique commune (définie dans garmin_client, sans Streamlit,
# pour que le serveur MCP charge exactement le même historique que les pages).


def get_session_api():
    """
    Retourne l'objet Garmin de la session courante, en le reprenant depuis le
    tokenstore si besoin. Retourne None si aucune session valide n'existe
    (→ la page d'accueil affiche alors le formulaire de connexion).
    """
    if "garmin_api" in st.session_state:
        return st.session_state["garmin_api"]
    api = resume_session()
    if api is not None:
        store_session(api)
    return api


def store_session(api) -> None:
    """Enregistre une session Garmin fraîchement connectée."""
    st.session_state["garmin_api"] = api
    st.session_state["garmin_athlete_id"] = athlete_id_of(api)


def drop_session() -> None:
    """Vide la session Streamlit (sans toucher au tokenstore)."""
    for key in ("garmin_api", "garmin_athlete_id"):
        st.session_state.pop(key, None)


def get_athlete_id() -> int:
    return st.session_state.get("garmin_athlete_id", 0)


def require_login() -> None:
    """
    Garde à appeler en haut des sous-pages : si aucune session Garmin n'est
    disponible, on affiche un message + un lien vers l'accueil (où vit le
    formulaire de connexion) et on arrête le rendu de la page courante.
    """
    if get_session_api() is not None:
        # Bascule Light/Pro rendue sur CHAQUE page (toutes passent par ici) :
        # un widget absent d'une page perdrait son état.
        render_mode_toggle()
        inject_theme()
        return
    st.title("🔒 Connexion requise")
    st.warning(
        "Tu dois d'abord connecter ton compte Garmin pour accéder à cette page.",
        icon="🔑",
    )
    st.page_link("main.py", label="Aller à la page de connexion", icon="🏠")
    st.stop()


def get_garmin_client() -> GarminClient:
    """
    Construit un `GarminClient` depuis la session courante.
    À appeler après `require_login()`.
    """
    return GarminClient(
        api=st.session_state["garmin_api"],
        athlete_id=get_athlete_id(),
    )


def render_garmin_attribution() -> None:
    """Mention de la source des données, en bas de page."""
    st.markdown(
        """
        <div style="text-align: center; margin: 24px 0 8px 0;">
            <span style="color: #888; font-size: 0.8rem;">
                Données Garmin Connect — projet personnel non affilié à Garmin
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def cache_nonce() -> int:
    """
    Compteur d'invalidation per-session. À passer en argument à toute fonction
    `@st.cache_data` qui doit pouvoir être invalidée explicitement par
    `render_refresh_button`.
    """
    return st.session_state.get("_cache_nonce", 0)


@st.cache_data(ttl=3600, show_spinner="Chargement des activités...")
def _cached_load_activities_impl(
    athlete_id: int, limit: int, nonce: int
) -> tuple[pd.DataFrame, str | None]:
    """Le `nonce` est dans la signature pour servir de clé de cache per-session."""
    del nonce  # uniquement pour la cache key
    return safe_load_activities(get_garmin_client(), limit)


def cached_load_activities(
    athlete_id: int, limit: int = ACTIVITY_HISTORY_LIMIT
) -> tuple[pd.DataFrame, str | None]:
    """
    Wrapper unique pour le chargement des activités, partagé par les pages.

    Laisser `limit` par défaut : c'est ce qui garantit des chiffres de charge
    identiques d'une page à l'autre et un seul fetch en cache (cf.
    ACTIVITY_HISTORY_LIMIT). Ne le surcharger que pour borner un affichage.
    """
    return _cached_load_activities_impl(athlete_id, limit, cache_nonce())


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_coach_context_impl(athlete_id: int, cdate: str, nonce: int):
    """Le `nonce` est dans la signature pour servir de clé de cache per-session."""
    del nonce  # uniquement pour la cache key
    return load_coach_context(get_garmin_client(), date.fromisoformat(cdate))


def cached_coach_context(athlete_id: int, cdate: str | None = None):
    """
    Contexte du plan Garmin Run Coach (séances à venir, phase, objectif), ou None
    si aucun plan n'est actif.

    Partagé par l'accueil et la page Prochaine sortie : les deux doivent annoncer
    la même séance, donc lire le plan par le même chemin.
    """
    day = cdate or date.today().isoformat()
    return _cached_coach_context_impl(athlete_id, day, cache_nonce())


def render_refresh_button(label: str = "🔄 Actualiser les données", *, stretch: bool = True) -> None:
    """
    Bouton de rafraîchissement standard : invalide le cache disque + bump du
    nonce per-session pour invalider les caches Streamlit `@st.cache_data`.
    """
    if st.button(label, width="stretch" if stretch else "content"):
        get_garmin_client().invalidate_cache()
        st.session_state["_cache_nonce"] = cache_nonce() + 1
        st.rerun()


def hex_to_rgba(hex_color: str, alpha: float) -> str:
    """Convertit un hex `#rrggbb` en chaîne CSS `rgba(r,g,b,a)`."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def render_elevation_profile(
    distances_km: Sequence[float],
    elevations: Sequence[float],
    *,
    color: str = ACCENT_COLOR,
    fill_alpha: float = 0.15,
    height: int = 210,
) -> None:
    """
    Profil altimétrique générique : reçoit deux séries alignées et trace
    un Plotly Scatter avec fill. Utilisé par main.py (depuis streams Garmin)
    et 4_Next_Session.py (depuis route ORS).
    """
    if not elevations or not distances_km:
        st.caption("Profil altimétrique non disponible.")
        return
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(distances_km), y=list(elevations),
        mode="lines",
        fill="tozeroy",
        fillcolor=hex_to_rgba(color, fill_alpha),
        line=dict(color=color, width=2),
        hovertemplate="<b>%{x:.2f} km</b><br>Altitude : %{y:.0f} m<extra></extra>",
    ))
    fig.update_layout(
        height=height,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#c6c8ce"),
        xaxis=dict(title="Distance (km)", gridcolor="#232833"),
        yaxis=dict(title="Altitude (m)", gridcolor="#232833"),
        margin=dict(l=0, r=0, t=10, b=0), showlegend=False,
    )
    st.plotly_chart(fig)


def render_activity_map(streams: dict, height: int = 420) -> None:
    """
    Affiche le tracé GPS d'une activité sur OpenStreetMap depuis le stream
    `latlng`. Ne fait rien si l'activité n'a pas de données GPS (tapis, indoor…).
    """
    latlng = (streams or {}).get("latlng") or []
    coords = [ll for ll in latlng if ll and len(ll) == 2]
    if not coords:
        return

    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    center_lat, center_lon, zoom = map_zoom(lats, lons)

    fig = go.Figure()
    fig.add_trace(go.Scattermap(
        lat=lats, lon=lons,
        mode="lines",
        line=dict(width=4, color=ACCENT_COLOR),
        hoverinfo="none",
    ))
    fig.add_trace(go.Scattermap(
        lat=[lats[0], lats[-1]], lon=[lons[0], lons[-1]],
        mode="markers",
        marker=dict(size=14, color=["#0ca30c", "#d03b3b"]),
        text=["Départ", "Arrivée"],
        hoverinfo="text",
    ))
    fig.update_layout(
        map=dict(
            style="open-street-map",
            center=dict(lat=center_lat, lon=center_lon),
            zoom=zoom,
        ),
        height=height,
        margin=dict(l=0, r=0, t=0, b=0),
        showlegend=False,
    )
    st.plotly_chart(fig)
