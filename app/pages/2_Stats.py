"""
Page Statistiques — Graphiques d'analyse des performances.
Volume, allure, FC, cadence sur le temps.
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

from formatting import event_type_label
from stats_tabs import tab_volume, tab_allure, tab_fc, tab_cadence, tab_regularite
from ui_mode import explain
from ui_helpers import (
    cached_load_activities,
    get_garmin_client,
    render_refresh_button,
    render_garmin_attribution,
    get_athlete_id,
    require_login,
)


st.set_page_config(
    page_title="Statistiques — Running Dashboard",
    page_icon="📊",
    layout="wide",
)

require_login()

_athlete_id = get_athlete_id()


# ---------------------------------------------------------------------------
# Données
# ---------------------------------------------------------------------------
def load_data(athlete_id: int) -> tuple[pd.DataFrame, str | None]:
    """Délègue à cached_load_activities — alias kept for readability."""
    return cached_load_activities(athlete_id)


@st.cache_data(ttl=86400, show_spinner=False)
def load_hr_zones(athlete_id: int) -> list:
    return get_garmin_client().get_hr_zones_definition()


# ---------------------------------------------------------------------------
# Chargement
# ---------------------------------------------------------------------------
st.title("📊 Statistiques d'entraînement")

df, error = load_data(_athlete_id)
if error:
    st.error(f"Erreur Garmin : {error}")
    st.stop()
if df.empty:
    st.warning("Aucune donnée disponible.")
    st.stop()

running_df = df[df["activityType"] == "running"].copy()
if "workoutType" not in running_df.columns:
    running_df["workoutType"] = "uncategorized"
running_df["workoutLabel"] = running_df["workoutType"].apply(event_type_label)

if running_df.empty:
    st.warning("Aucune activité de course trouvée.")
    st.stop()

# ---------------------------------------------------------------------------
# Sidebar — période et FC max
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## ⚙️ Période d'analyse")
    period = st.selectbox(
        "Afficher les",
        options=["3 derniers mois", "6 derniers mois", "12 derniers mois", "Toutes les données"],
        index=2,
    )
    period_days = {
        "3 derniers mois": 90, "6 derniers mois": 180,
        "12 derniers mois": 365, "Toutes les données": 9999,
    }
    cutoff = datetime.now() - timedelta(days=period_days[period])

    render_refresh_button("🔄 Actualiser")

    hr_zones_list = load_hr_zones(_athlete_id)

running_filtered = running_df[running_df["startTimeLocal"] >= cutoff].copy()
if running_filtered.empty:
    st.warning(f"Aucune activité sur la période sélectionnée ({period}).")
    st.stop()

client = get_garmin_client()

# ---------------------------------------------------------------------------
# Onglets
# ---------------------------------------------------------------------------
_TAB_LABELS = ["📦 Volume", "🐇 Allure", "❤️ Fréquence cardiaque", "🦶 Cadence", "📅 Régularité"]
if "stats_active_tab" not in st.session_state:
    st.session_state["stats_active_tab"] = _TAB_LABELS[0]

active_tab = st.radio(
    "Onglet", _TAB_LABELS,
    key="stats_active_tab",
    horizontal=True,
    label_visibility="collapsed",
)

if active_tab == "📦 Volume":
    tab_volume.render(running_filtered, client)
elif active_tab == "🐇 Allure":
    tab_allure.render(running_filtered)
    explain("seuil")
elif active_tab == "❤️ Fréquence cardiaque":
    tab_fc.render(running_filtered, client, hr_zones_list)
    explain("hr_zones")
elif active_tab == "🦶 Cadence":
    tab_cadence.render(running_filtered)
    explain("cadence")
elif active_tab == "📅 Régularité":
    tab_regularite.render(running_df)

st.caption("⚡ La charge d'entraînement a déménagé sur la page **Forme & Récup**.")
render_garmin_attribution()
