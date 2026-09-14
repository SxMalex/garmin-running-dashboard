"""
Page Activités — Liste filtrée et détails des sorties.
"""

import math
from datetime import datetime, timedelta, date

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

from formatting import decimate, seconds_to_pace_str
from ui_helpers import (
    cached_load_activities,
    get_garmin_client,
    render_activity_map,
    render_garmin_attribution,
    get_athlete_id,
    require_login,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Activités — Running Dashboard",
    page_icon="📋",
    layout="wide",
)

require_login()

_athlete_id = get_athlete_id()

st.markdown("""
<style>
    .activity-card {
        background: linear-gradient(135deg, #161a23 0%, #1b2029 100%);
        border: 1px solid #2a3040;
        border-radius: 12px;
        padding: 16px 20px;
        margin-bottom: 8px;
    }
    .split-table th {
        background: #1b2029;
        color: #3987e5;
    }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Client Garmin (réutilisé depuis le cache Streamlit)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner="Chargement des détails...")
def load_activity_details(athlete_id: int, activity_id: int) -> dict:
    return get_garmin_client().get_activity_details(activity_id)


@st.cache_data(ttl=3600, show_spinner="Chargement des streams...")
def load_streams(athlete_id: int, activity_id: int) -> dict:
    return get_garmin_client().get_streams(activity_id)


def _render_streams(streams: dict, max_hr: int = 190) -> None:
    """Graphiques détaillés type Garmin : altitude, allure, fréquence cardiaque."""
    dist_raw = streams.get("distance", [])
    if not dist_raw:
        return

    n_raw = len(dist_raw)
    has_alt = len(streams.get("altitude", [])) == n_raw
    has_vel = len(streams.get("velocity_smooth", [])) == n_raw
    has_hr  = len(streams.get("heartrate", [])) == n_raw

    # Décimation à ~1000 points pour éviter de surcharger Plotly côté navigateur
    # (les streams Garmin peuvent atteindre 2k points sur une longue sortie).
    streams = {k: decimate(v) for k, v in streams.items() if isinstance(v, list)}
    dist_raw = streams["distance"]
    dist_km = [d / 1000 for d in dist_raw]
    n = len(dist_km)

    if not has_alt and not has_vel and not has_hr:
        st.info("Aucun stream exploitable pour cette activité (pas de capteur enregistré).")
        return

    panels = (
        [("altitude", 0.18)] if has_alt else []
    ) + (
        [("pace", 0.41)] if has_vel else []
    ) + (
        [("heartrate", 0.41)] if has_hr else []
    )
    n_rows = len(panels)
    total_h = sum(h for _, h in panels)
    row_heights = [h / total_h for _, h in panels]

    fig = make_subplots(
        rows=n_rows, cols=1,
        shared_xaxes=True,
        row_heights=row_heights,
        vertical_spacing=0.05,
    )

    axis_style = dict(
        gridcolor="#232833",
        tickfont=dict(color="#8b8f98", size=10),
        title_font=dict(color="#8b8f98", size=11),
        zeroline=False,
    )

    for row_idx, (ptype, _) in enumerate(panels, 1):
        is_last = row_idx == n_rows

        if ptype == "altitude":
            alt = streams["altitude"]
            grade = streams.get("grade_smooth", [])
            hover = [
                f"%{{x:.2f}} km · {a:.0f} m · pente {g:.1f}%"
                if len(grade) == n else f"%{{x:.2f}} km · {a:.0f} m"
                for a, g in zip(alt, grade if len(grade) == n else [0] * n)
            ]
            customdata = grade if len(grade) == n else None
            fig.add_trace(go.Scatter(
                x=dist_km, y=alt,
                fill="tozeroy",
                fillcolor="rgba(144,133,233,0.16)",
                line=dict(color="rgba(144,133,233,0.9)", width=1.5),
                name="Altitude",
                customdata=customdata,
                hovertemplate=(
                    "%{x:.2f} km · %{y:.0f} m · pente %{customdata:.1f}%<extra></extra>"
                    if customdata is not None else
                    "%{x:.2f} km · %{y:.0f} m<extra></extra>"
                ),
            ), row=row_idx, col=1)
            fig.update_yaxes(title_text="Alt. (m)", row=row_idx, col=1, **axis_style)

        elif ptype == "pace":
            vel = streams["velocity_smooth"]
            pace_raw = pd.Series([
                1000 / v / 60 if v and v > 0.5 else None for v in vel
            ])
            # Lissage sur 15 points pour effacer le bruit GPS
            smoothed = pace_raw.rolling(15, center=True, min_periods=1).mean().tolist()
            hover = [
                f"{int(p)}:{int((p % 1) * 60):02d}/km" if p and p < 20 else "—"
                for p in smoothed
            ]
            clipped = [p if p and p < 20 else None for p in smoothed]

            fig.add_trace(go.Scatter(
                x=dist_km, y=clipped,
                mode="lines",
                line=dict(color="rgba(57,135,229,0.95)", width=1.5),
                name="Allure",
                customdata=hover,
                hovertemplate="%{x:.2f} km · %{customdata}<extra></extra>",
            ), row=row_idx, col=1)

            valid = [p for p in clipped if p]
            if valid:
                y_min = max(2.0, min(valid) * 0.97)
                y_max = min(15.0, max(valid) * 1.03)
            else:
                y_min, y_max = 3.0, 8.0
            fig.update_yaxes(
                title_text="Allure",
                autorange="reversed",
                range=[y_max, y_min],
                tickformat=".1f",
                row=row_idx, col=1,
                **axis_style,
            )

        elif ptype == "heartrate":
            hr = streams["heartrate"]
            # Bandes de zones FC en arrière-plan
            zone_bands = [
                (0,              0.60 * max_hr, "rgba(57,135,229,0.07)"),
                (0.60 * max_hr,  0.70 * max_hr, "rgba(25,158,112,0.08)"),
                (0.70 * max_hr,  0.80 * max_hr, "rgba(201,133,0,0.10)"),
                (0.80 * max_hr,  0.90 * max_hr, "rgba(217,89,38,0.10)"),
                (0.90 * max_hr,  max_hr * 1.1,  "rgba(230,103,103,0.12)"),
            ]
            for y0, y1, color in zone_bands:
                fig.add_hrect(y0=y0, y1=y1, fillcolor=color, line_width=0, row=row_idx, col=1)

            fig.add_trace(go.Scatter(
                x=dist_km, y=hr,
                mode="lines",
                line=dict(color="rgba(230,103,103,0.9)", width=1.5),
                fill="tozeroy",
                fillcolor="rgba(230,103,103,0.08)",
                name="FC",
                hovertemplate="%{x:.2f} km · %{y:.0f} bpm<extra></extra>",
            ), row=row_idx, col=1)

            valid_hr = [h for h in hr if h]
            hr_min = max(40, min(valid_hr) * 0.93) if valid_hr else 40
            hr_max = min(max_hr * 1.08, max(valid_hr) * 1.05) if valid_hr else max_hr
            fig.update_yaxes(
                title_text="FC (bpm)",
                range=[hr_min, hr_max],
                row=row_idx, col=1,
                **axis_style,
            )

        fig.update_xaxes(
            showticklabels=is_last,
            title_text="Distance (km)" if is_last else "",
            row=row_idx, col=1,
            **axis_style,
        )

    chart_height = sum(
        100 if ptype == "altitude" else 200
        for ptype, _ in panels
    ) + 60

    fig.update_layout(
        height=chart_height,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#c6c8ce"),
        margin=dict(l=0, r=0, t=5, b=0),
        hovermode="x unified",
        showlegend=False,
        bargap=0,
        bargroupgap=0,
    )
    st.plotly_chart(fig)


# ---------------------------------------------------------------------------
# Chargement
# ---------------------------------------------------------------------------
st.title("📋 Mes Activités")

df, error = cached_load_activities(_athlete_id)

if error:
    st.error(f"Erreur Garmin : {error}")
    st.stop()

if df.empty:
    st.warning("Aucune activité disponible.")
    st.stop()

# ---------------------------------------------------------------------------
# Filtres dans la barre latérale
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## 🔍 Filtres")

    # Filtre par type d'activité
    all_types = sorted(df["activityType"].unique().tolist())
    type_labels = {
        "running": "🏃 Course",
        "cycling": "🚴 Vélo",
        "swimming": "🏊 Natation",
        "walking": "🚶 Marche",
        "hiking": "🥾 Randonnée",
        "strength": "💪 Musculation",
        "yoga": "🧘 Yoga",
        "cardio": "❤️ Cardio",
    }
    type_options = [type_labels.get(t, t.title()) for t in all_types]
    type_mapping = dict(zip(type_options, all_types))

    selected_type_labels = st.multiselect(
        "Type d'activité",
        options=type_options,
        default=["🏃 Course"] if "🏃 Course" in type_options else type_options[:1],
    )
    selected_types = [type_mapping[l] for l in selected_type_labels]

    st.divider()

    # Filtre par date
    min_date = df["startTimeLocal"].min().date() if not df.empty else date.today() - timedelta(days=365)
    max_date = df["startTimeLocal"].max().date() if not df.empty else date.today()

    date_from = st.date_input(
        "Du",
        value=max_date - timedelta(days=90),
        min_value=min_date,
        max_value=max_date,
    )
    date_to = st.date_input(
        "Au",
        value=max_date,
        min_value=min_date,
        max_value=max_date,
    )

    st.divider()

    # Filtre par distance
    if not df.empty and "distance_km" in df.columns:
        raw_max = float(df["distance_km"].max()) if not df.empty else 50.0
        max_dist = max(math.ceil(raw_max * 2) / 2, 1.0)  # round up to nearest 0.5
        dist_range = st.slider(
            "Distance (km)",
            min_value=0.0,
            max_value=max_dist,
            value=(0.0, max_dist),
            step=0.5,
        )
    else:
        dist_range = (0.0, 999.0)

    # Recherche par nom
    search_name = st.text_input("🔎 Rechercher par nom", placeholder="ex : 10km, Trail...")

# ---------------------------------------------------------------------------
# Application des filtres
# ---------------------------------------------------------------------------
filtered = df.copy()

if selected_types:
    filtered = filtered[filtered["activityType"].isin(selected_types)]

filtered = filtered[
    (filtered["startTimeLocal"].dt.date >= date_from)
    & (filtered["startTimeLocal"].dt.date <= date_to)
]

filtered = filtered[
    (filtered["distance_km"] >= dist_range[0])
    & (filtered["distance_km"] <= dist_range[1])
]

if search_name:
    filtered = filtered[
        filtered["activityName"].str.contains(search_name, case=False, na=False)
    ]

filtered = filtered.sort_values("startTimeLocal", ascending=False)

# ---------------------------------------------------------------------------
# Résumé des filtres
# ---------------------------------------------------------------------------
col1, col2, col3, col4 = st.columns(4)
col1.metric("Activités trouvées", len(filtered))
if not filtered.empty:
    col2.metric("Volume total", f"{filtered['distance_km'].sum():.1f} km")
    col3.metric("Durée totale", f"{filtered['duration_min'].sum() / 60:.1f} h")
    pace_vals = filtered.loc[filtered["avgPace_sec"] > 0, "avgPace_sec"]
    col4.metric(
        "Allure moyenne",
        seconds_to_pace_str(pace_vals.mean()) if not pace_vals.empty else "—"
    )

st.divider()

# ---------------------------------------------------------------------------
# Tableau des activités
# ---------------------------------------------------------------------------
if filtered.empty:
    st.info("Aucune activité ne correspond aux filtres sélectionnés.")
    st.stop()

# Préparer l'affichage
display = filtered[[
    "startTimeLocal", "activityName", "activityType",
    "distance_km", "duration_min", "avgPace",
    "avgHR", "avgCadence", "calories", "elevationGain",
]].copy()

display["startTimeLocal"] = pd.to_datetime(display["startTimeLocal"]).dt.strftime("%d/%m/%Y %H:%M")
display["activityType"] = display["activityType"].map(type_labels).fillna(display["activityType"])

# Conserver les colonnes numériques comme nombres (NaN = cellule vide, pas "—")
for col in ["avgHR", "avgCadence", "calories", "elevationGain"]:
    display[col] = pd.to_numeric(display[col], errors="coerce")

display = display.rename(columns={
    "startTimeLocal": "Date",
    "activityName": "Nom",
    "activityType": "Type",
    "distance_km": "Distance (km)",
    "duration_min": "Durée (min)",
    "avgPace": "Allure",
    "avgHR": "FC moy",
    "avgCadence": "Cadence",
    "calories": "Calories",
    "elevationGain": "D+ (m)",
})

# Sélection d'une ligne
selected_event = st.dataframe(
    display,
    width='stretch',
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
    column_config={
        "Date":          st.column_config.TextColumn("📅 Date"),
        "Nom":           st.column_config.TextColumn("🏃 Nom"),
        "Type":          st.column_config.TextColumn("🏷️ Type"),
        "Distance (km)": st.column_config.NumberColumn("📏 Distance (km)", format="%.2f"),
        "Durée (min)":   st.column_config.NumberColumn("⏱️ Durée (min)",   format="%.0f"),
        "Allure":        st.column_config.TextColumn("🐇 Allure"),
        "FC moy":        st.column_config.NumberColumn("❤️ FC moy",        format="%d bpm"),
        "Cadence":       st.column_config.NumberColumn("🦶 Cadence",        format="%d spm"),
        "Calories":      st.column_config.NumberColumn("🔥 Calories",       format="%d kcal"),
        "D+ (m)":        st.column_config.NumberColumn("⛰️ D+ (m)",         format="%d m"),
    },
)

# ---------------------------------------------------------------------------
# Détails de l'activité sélectionnée
# ---------------------------------------------------------------------------
if selected_event.selection.rows:
    selected_idx = selected_event.selection.rows[0]
    selected_row = filtered.iloc[selected_idx]
    activity_id = selected_row["activityId"]

    st.divider()
    st.subheader(f"🔍 Détails — {selected_row.get('activityName', 'Activité')}")

    date_fmt = pd.to_datetime(selected_row["startTimeLocal"]).strftime("%A %d %B %Y à %H:%M")
    st.caption(f"📅 {date_fmt}")

    # Métriques principales
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("📏 Distance", f"{selected_row['distance_km']:.2f} km")
    m2.metric("⏱️ Durée", f"{selected_row['duration_min']:.0f} min")
    m3.metric("🐇 Allure", selected_row["avgPace"])
    m4.metric("❤️ FC moy", f"{int(selected_row['avgHR'])} bpm" if pd.notna(selected_row.get("avgHR")) else "—")
    m5.metric("🦶 Cadence", f"{int(selected_row['avgCadence'])} spm" if pd.notna(selected_row.get("avgCadence")) else "—")

    m6, m7, m8, _, _ = st.columns(5)
    m6.metric("🔥 Calories", f"{int(selected_row['calories'])} kcal" if pd.notna(selected_row.get("calories")) else "—")
    m7.metric("⛰️ D+", f"{int(selected_row['elevationGain'])} m" if pd.notna(selected_row.get("elevationGain")) else "—")
    m8.metric("❤️‍🔥 FC max", f"{int(selected_row['maxHR'])} bpm" if pd.notna(selected_row.get("maxHR")) else "—")

    # Chargement des détails et splits
    with st.spinner("Chargement des détails..."):
        details = load_activity_details(_athlete_id, activity_id)
        streams = load_streams(_athlete_id, activity_id)

    # Carte GPS (depuis le stream latlng)
    if streams:
        render_activity_map(streams, height=420)

    # Graphiques détaillés (streams)
    if streams:
        st.subheader("📈 Graphiques détaillés")
        max_hr_act = int(selected_row.get("maxHR") or 190)
        max_hr_act = max(150, min(220, max_hr_act))
        _render_streams(streams, max_hr=max_hr_act)

    if details and details.get("splits"):
        st.subheader("📊 Laps")
        splits = details["splits"]
        splits_df = pd.DataFrame(splits)

        if not splits_df.empty:
            # Graphique des splits
            fig_splits = go.Figure()

            avg_split_pace = splits_df.loc[splits_df["pace_sec"] > 0, "pace_sec"].mean()
            fig_splits.add_trace(go.Bar(
                x=splits_df["lap"].astype(str),
                y=splits_df["pace_sec"].apply(lambda s: s / 60 if s > 0 else None),
                name="Allure (min/km)",
                marker_color=[
                    "rgba(12,163,12,0.9)" if p > 0 and p < avg_split_pace
                    else "rgba(57,135,229,0.85)"
                    for p in splits_df["pace_sec"]
                ],
                hovertemplate="<b>Lap %{x}</b><br>Allure : %{customdata}<extra></extra>",
                customdata=splits_df["pace"],
            ))

            fig_splits.update_layout(
                height=280,
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#c6c8ce"),
                xaxis=dict(
                    title="Lap",
                    gridcolor="#232833",
                ),
                yaxis=dict(
                    title="Allure (min/km)",
                    gridcolor="#232833",
                    tickformat=".1f",
                ),
                margin=dict(l=0, r=0, t=10, b=0),
                showlegend=False,
            )
            st.plotly_chart(fig_splits)

            # Tableau des splits
            splits_display = splits_df[["lap", "distance_km", "duration_min", "pace", "avgHR", "avgCadence", "elevationGain"]].copy()
            for col in ["avgHR", "avgCadence", "elevationGain"]:
                splits_display[col] = pd.to_numeric(splits_display[col], errors="coerce")
            splits_display = splits_display.rename(columns={
                "distance_km": "Distance (km)",
                "duration_min": "Durée (min)",
                "pace": "Allure",
                "avgHR": "FC moy",
                "avgCadence": "Cadence",
                "elevationGain": "D+ (m)",
            })
            st.dataframe(
                splits_display,
                width='stretch',
                hide_index=True,
                column_config={
                    "lap":          st.column_config.NumberColumn("Lap",         format="%d"),
                    "Distance (km)":st.column_config.NumberColumn("Distance (km)",format="%.2f"),
                    "Durée (min)":  st.column_config.NumberColumn("Durée (min)", format="%.2f"),
                    "Allure":       st.column_config.TextColumn("Allure"),
                    "FC moy":       st.column_config.NumberColumn("FC moy",      format="%d bpm"),
                    "Cadence":      st.column_config.NumberColumn("Cadence",      format="%d spm"),
                    "D+ (m)":       st.column_config.NumberColumn("D+ (m)",       format="%d m"),
                },
            )
    elif details:
        st.info("Les données de splits ne sont pas disponibles pour cette activité.")
    else:
        st.info("Impossible de charger les détails de cette activité.")

render_garmin_attribution()
