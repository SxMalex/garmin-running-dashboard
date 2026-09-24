"""
Page Progression — records personnels Garmin, prédictions de course
(natives + Riegel) et évolution du VO2max. Répond à « est-ce que je progresse ? ».
"""

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from physio_ui import render_aerobic_progress, render_physio_settings
from ui_mode import explain
from progression_logic import (
    RACE_TARGETS,
    fmt_race_pace,
    fmt_race_time,
    parse_personal_records,
    predictions_history_df,
    riegel_estimates,
)
from ui_helpers import (
    cached_load_activities,
    get_athlete_id,
    get_garmin_client,
    render_garmin_attribution,
    render_refresh_button,
    require_login,
)

st.set_page_config(
    page_title="Progression — Running Dashboard",
    page_icon="📈",
    layout="wide",
)

require_login()
render_physio_settings()

_athlete_id = get_athlete_id()


# ---------------------------------------------------------------------------
# Chargement (caché)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=86400, show_spinner=False)
def load_records(athlete_id: int) -> list:
    return get_garmin_client().get_personal_records()


@st.cache_data(ttl=86400, show_spinner=False)
def load_predictions(athlete_id: int) -> dict:
    return get_garmin_client().get_race_predictions()


@st.cache_data(ttl=86400, show_spinner="Chargement de l'historique des prédictions…")
def load_predictions_history(athlete_id: int, start: str, end: str) -> list:
    return get_garmin_client().get_race_predictions_range(start, end)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## ⚙️ Paramètres")
    history_days = st.selectbox(
        "Historique des prédictions",
        options=[30, 90, 180, 365],
        index=1,
        format_func=lambda d: f"{d} jours",
    )
    render_refresh_button("🔄 Actualiser")

st.title("📈 Progression")
st.caption("Records personnels, prédictions de course et évolution du VO2max.")

df, error = cached_load_activities(_athlete_id)
if error:
    st.error(f"Erreur Garmin : {error}")
    st.stop()
running_df = df[df["activityType"] == "running"].copy() if not df.empty else pd.DataFrame()

# ---------------------------------------------------------------------------
# Prédictions de course — natives Garmin + Riegel
# ---------------------------------------------------------------------------
st.subheader("🏆 Prédictions de course")

predictions = load_predictions(_athlete_id)
riegel = riegel_estimates(running_df)

_ICONS = {"5 km": "🥇", "10 km": "🥈", "Semi": "🥉", "Marathon": "🏅"}

if predictions or riegel:
    cols = st.columns(4)
    for col, (label, dist_km, garmin_key) in zip(cols, RACE_TARGETS):
        garmin_sec = predictions.get(garmin_key)
        riegel_sec = riegel.get(label)
        with col:
            if garmin_sec:
                st.metric(
                    label=f"{_ICONS[label]} {label}",
                    value=fmt_race_time(garmin_sec),
                    delta=fmt_race_pace(garmin_sec, dist_km),
                    delta_color="off",
                )
                if riegel_sec:
                    st.caption(f"Riegel : {fmt_race_time(riegel_sec)}")
            elif riegel_sec:
                st.metric(
                    label=f"{_ICONS[label]} {label}",
                    value=fmt_race_time(riegel_sec),
                    delta=fmt_race_pace(riegel_sec, dist_km),
                    delta_color="off",
                )
                st.caption("Estimation Riegel")
    st.caption(
        "Prédictions natives Garmin ; Riegel (T2 = T1 × (D2/D1)^1.06) en comparaison."
    )
else:
    st.info("Pas de prédictions disponibles.")

# ── Évolution des prédictions ──────────────────────────────────────────────
hist_start = (date.today() - timedelta(days=history_days)).isoformat()
hist_raw = load_predictions_history(_athlete_id, hist_start, date.today().isoformat())
hist_df = predictions_history_df(hist_raw)

if not hist_df.empty:
    distance = st.radio(
        "Distance",
        [label for label, _km, _k in RACE_TARGETS],
        index=1,
        horizontal=True,
        label_visibility="collapsed",
    )
    serie = hist_df[hist_df["distance"] == distance].sort_values("date")
    if not serie.empty:
        serie = serie.copy()
        serie["time_str"] = serie["time_sec"].apply(fmt_race_time)
        fig_hist = go.Figure(go.Scatter(
            x=serie["date"],
            y=serie["time_sec"] / 60,
            mode="lines",
            line=dict(color="#3987e5", width=2),
            customdata=serie["time_str"],
            hovertemplate="%{x|%d/%m/%Y} · %{customdata}<extra></extra>",
        ))
        best = serie.loc[serie["time_sec"].idxmin()]
        fig_hist.add_trace(go.Scatter(
            x=[best["date"]], y=[best["time_sec"] / 60],
            mode="markers+text",
            marker=dict(size=10, color="#0ca30c"),
            text=[f"  meilleur : {best['time_str']}"],
            textposition="middle right",
            textfont=dict(color="#0ca30c", size=11),
            hoverinfo="skip",
        ))
        fig_hist.update_layout(
            height=300,
            yaxis=dict(title="Temps prédit (min)", autorange="reversed"),
            showlegend=False,
            margin=dict(l=0, r=0, t=10, b=0),
        )
        st.plotly_chart(fig_hist)
        first, last_p = serie.iloc[0], serie.iloc[-1]
        delta_sec = last_p["time_sec"] - first["time_sec"]
        trend = "🟢 en progression" if delta_sec < 0 else "🔻 en retrait"
        st.caption(
            f"Prédiction {distance} sur {history_days} jours : "
            f"{first['time_str']} → {last_p['time_str']} ({delta_sec:+.0f} s, {trend}). "
            "Axe inversé : plus bas = plus rapide."
        )

st.divider()

# ---------------------------------------------------------------------------
# VO2max — évolution (valeur portée par chaque activité course)
# ---------------------------------------------------------------------------
st.subheader("🫁 VO2max")

if not running_df.empty and "vo2max" in running_df.columns:
    vo2 = running_df.dropna(subset=["vo2max"]).sort_values("startTimeLocal")
else:
    vo2 = pd.DataFrame()

if not vo2.empty:
    v1, v2 = st.columns([1, 4])
    current = vo2.iloc[-1]["vo2max"]
    best = vo2["vo2max"].max()
    v1.metric("Actuel", f"{current:.0f}", delta=f"max {best:.0f}", delta_color="off")
    with v2:
        fig_vo2 = go.Figure(go.Scatter(
            x=vo2["startTimeLocal"], y=vo2["vo2max"],
            mode="lines+markers",
            line=dict(color="#d55181", width=2),
            marker=dict(size=6),
            hovertemplate="%{x|%d/%m/%Y} · VO2max %{y:.0f}<extra></extra>",
        ))
        fig_vo2.update_layout(
            height=260,
            yaxis=dict(title="VO2max"),
            showlegend=False,
            margin=dict(l=0, r=0, t=10, b=0),
        )
        st.plotly_chart(fig_vo2)
    st.caption(
        f"Estimation Garmin portée par chaque sortie ({len(vo2)} points sur les "
        "activités chargées)."
    )
    explain("vo2max")
else:
    st.info("Pas de VO2max sur les activités chargées.")

st.divider()

# ---------------------------------------------------------------------------
# Records personnels
# ---------------------------------------------------------------------------
st.subheader("🏅 Records personnels")

records = parse_personal_records(load_records(_athlete_id))

if not records:
    st.info("Pas de records personnels disponibles.")
else:
    for group_label in dict.fromkeys(r["group_label"] for r in records):
        group_rows = [r for r in records if r["group_label"] == group_label]
        st.markdown(f"#### {group_label}")
        cols = st.columns(min(4, max(2, len(group_rows))))
        for i, pr in enumerate(group_rows):
            with cols[i % len(cols)]:
                st.metric(pr["label"], pr["value_str"],
                          delta=pr["date_str"] or None, delta_color="off")
                if pr["activity_name"]:
                    st.caption(pr["activity_name"])

# En fin de page : peut demander jusqu'à DECOUPLING_TREND_MAX_RUNS appels API
# au premier chargement, sans retarder les records et prédictions.
st.divider()
render_aerobic_progress(df, _athlete_id)

render_garmin_attribution()
