"""Helpers d'affichage Streamlit partagés entre pages."""

import logging
from datetime import date
from typing import Sequence

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import chart_theme as ct  # active le template Plotly gar
from coach_logic import COACH_UNKNOWN, load_coach_context
from formatting import map_zoom
from garmin_client import (
    ACTIVITY_HISTORY_LIMIT,
    GarminClient,
    adopt_session,
    is_garmin_failure,
    shared_athlete_id,
    shared_session,
    safe_load_activities,
)

logger = logging.getLogger(__name__)

OSM_ATTRIBUTION = ("Fond de carte © [OpenStreetMap](https://www.openstreetmap.org/copyright) "
                   "contributors")

ACCENT_COLOR = ct.PACE  # la couleur suit l'entité (allure/tracé)

# Profondeur d'historique commune (définie dans garmin_client, sans Streamlit,
# pour que le serveur MCP charge exactement le même historique que les pages).


def get_session_api():
    """
    La session Garmin du process (partagée par tous les onglets, cf.
    `garmin_client.shared_session`), reprise du tokenstore au besoin. None si
    aucune session valide (→ formulaire de connexion). Relue à chaque run :
    une déconnexion faite dans un autre onglet s'applique ici aussi.
    """
    api = shared_session()
    if api is None:
        drop_session()
        return None
    if (st.session_state.get("garmin_api") is not api
            or not st.session_state.get("garmin_athlete_id_reliable")):
        athlete_id, reliable = shared_athlete_id()       # (un id de repli est retenté)
        st.session_state["garmin_api"] = api
        st.session_state["garmin_athlete_id"] = athlete_id
        st.session_state["garmin_athlete_id_reliable"] = reliable
    return api


def store_session(api) -> None:
    """Enregistre une session Garmin fraîchement connectée (pour tout le process)."""
    adopt_session(api)
    get_session_api()


def drop_session() -> None:
    """Vide la session Streamlit (sans toucher au tokenstore)."""
    for key in ("garmin_api", "garmin_athlete_id", "garmin_athlete_id_reliable"):
        st.session_state.pop(key, None)


def get_athlete_id() -> int:
    return st.session_state.get("garmin_athlete_id", 0)


def validated_plan_sessions():
    """
    Séances du plan Objectif validé pour la session — le seul chemin des pages.
    Sous un id de repli (Garmin n'a pas confirmé le compte), le plan serait lu
    dans un autre dossier : None, et un avis le dit (la séance du jour retombe
    alors sur la logique interne, sans prétendre suivre le plan).
    """
    import goal_store

    if not athlete_id_is_reliable():
        st.caption(":material/sync_problem: Plan Objectif non lu : Garmin n'a pas encore confirmé "
                   "ton compte. Réessai automatique dans la minute.")
        return None
    return goal_store.validated_sessions(get_athlete_id())


def athlete_id_is_reliable() -> bool:
    """
    False quand l'id vient du repli (Garmin n'a pas donné le profileId) : il
    ne faut alors RIEN écrire de durable (objectif, plan validé, journal), qui
    partirait dans un autre dossier que celui relu au prochain démarrage.
    """
    return bool(st.session_state.get("garmin_athlete_id_reliable", False))


def require_login() -> None:
    """
    Garde à appeler en haut des pages. Le routeur (`main.py`) n'enregistre les
    pages qu'une fois connecté : ce chemin ne sert que si la session tombe
    entre deux runs. Le formulaire de connexion vit dans le routeur.
    """
    if get_session_api() is not None:
        return
    st.title("Connexion requise")
    st.warning("Ta session Garmin a expiré : recharge la page pour te reconnecter.",
               icon=":material/key:")
    st.stop()


def get_garmin_client() -> GarminClient:
    """
    Construit un `GarminClient` depuis la session courante.
    À appeler après `require_login()`.
    """
    return GarminClient(
        api=st.session_state["garmin_api"],
        athlete_id=get_athlete_id(),
        athlete_id_reliable=athlete_id_is_reliable(),
    )


def render_garmin_attribution() -> None:
    """Mention de la source des données, en bas de page."""
    st.markdown(
        """
        <div style="text-align: center; margin: 24px 0 8px 0;">
            <span style="color: #62666F; font-size: 0.8rem;">
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
    `refresh_data`.
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
    Contexte du plan Garmin Run Coach (séances à venir, phase, objectif), None
    si aucun plan n'est actif, `coach_logic.COACH_UNKNOWN` si Garmin n'a pas
    répondu — un échec n'est pas mis en cache (st.cache_data ne garde pas une
    exception) : il est retenté au prochain rendu, pas dans une heure.

    Partagé par l'accueil et la page Prochaine sortie : les deux doivent annoncer
    la même séance, donc lire le plan par le même chemin.
    """
    day = cdate or date.today().isoformat()
    try:
        return _cached_coach_context_impl(athlete_id, day, cache_nonce())
    except Exception as exc:
        if not is_garmin_failure(exc):
            raise                  # bug de lecture : visible, pas une fausse « panne » permanente
        logger.warning("Garmin n'a pas répondu sur le plan Run Coach : état inconnu", exc_info=True)
        return COACH_UNKNOWN


def refresh_data() -> None:
    """
    Actualiser (bouton de l'en-tête) : invalide le cache disque + bump du nonce
    per-session pour invalider les caches Streamlit `@st.cache_data`.
    Ne pas utiliser `st.cache_data.clear()` (global à tous les utilisateurs).
    """
    get_garmin_client().invalidate_cache()
    st.session_state["_cache_nonce"] = cache_nonce() + 1
    st.rerun()


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
    un Plotly Scatter avec fill.
    """
    if not elevations or not distances_km:
        st.caption("Profil altimétrique non disponible.")
        return
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(distances_km), y=list(elevations),
        mode="lines",
        fill="tozeroy",
        fillcolor=ct.rgba(color, fill_alpha),
        line=dict(color=color, width=2),
        hovertemplate="<b>%{x:.2f} km</b><br>Altitude : %{y:.0f} m<extra></extra>",
    ))
    fig.update_layout(
        height=height,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(title="Distance (km)"),
        yaxis=dict(title="Altitude (m)"),
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
        marker=dict(size=14, color=[ct.GOOD, ct.CRITICAL]),
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
    # Politique des tuiles OSM : attribution visible (celle de Plotly est repliée).
    st.caption(OSM_ATTRIBUTION)
