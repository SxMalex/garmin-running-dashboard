"""
Page Activités — explorateur des sorties (un indicateur, points cliquables),
répartition de l'intensité (80/20), liste enrichie et détail d'une sortie.
"""

import hashlib
import math
from datetime import timedelta, date

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from activities_logic import METRICS, ZONE_LABELS, enrich, intensity_distribution, polarization
from formatting import decimate, md_escape, seconds_to_pace_str
from next_session_logic import reference_threshold_sec
from physio_ui import render_physio_settings, render_signal_quality
from ui_mode import explain
from ui_theme import chip, esc, html_block
from ui_helpers import (
    cached_load_activities,
    get_garmin_client,
    render_activity_map,
    render_garmin_attribution,
    get_athlete_id,
    require_login,
)

import chart_theme as ct

# Zones d'intensité : convention cardio bleu → rouge (ct.ZONE_HEAT). Jamais seule
# porteuse de l'info : le nom de la zone est dans la légende et au survol.
ZONE_COLORS = dict(zip(["recup", "endurance", "tempo", "seuil", "vma"], ct.ZONE_HEAT)) | {
    "autre": ct.INK_MUTED}

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Activités — Running Dashboard",
    page_icon="📋",
    layout="wide",
)

require_login()
render_physio_settings()

_athlete_id = get_athlete_id()


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
        tickfont=dict(color=ct.INK_MUTED, size=10),
        title_font=dict(color=ct.INK_MUTED, size=11),
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
                fillcolor=ct.rgba(ct.ALTITUDE, 0.16),
                line=dict(color=ct.rgba(ct.ALTITUDE, 0.9), width=1.5),
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
                line=dict(color=ct.rgba(ct.PACE, 0.95), width=1.5),
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
                (0,              0.60 * max_hr, ct.rgba(ct.ZONE_HEAT[0], 0.07)),
                (0.60 * max_hr,  0.70 * max_hr, ct.rgba(ct.ZONE_HEAT[1], 0.08)),
                (0.70 * max_hr,  0.80 * max_hr, ct.rgba(ct.ZONE_HEAT[2], 0.10)),
                (0.80 * max_hr,  0.90 * max_hr, ct.rgba(ct.ZONE_HEAT[3], 0.10)),
                (0.90 * max_hr,  max_hr * 1.1,  ct.rgba(ct.ZONE_HEAT[4], 0.12)),
            ]
            for y0, y1, color in zone_bands:
                fig.add_hrect(y0=y0, y1=y1, fillcolor=color, line_width=0, row=row_idx, col=1)

            fig.add_trace(go.Scatter(
                x=dist_km, y=hr,
                mode="lines",
                line=dict(color=ct.rgba(ct.HR, 0.9), width=1.5),
                fill="tozeroy",
                fillcolor=ct.rgba(ct.HR, 0.08),
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
st.title("Activités")

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
        filtered["activityName"].str.contains(search_name, case=False, na=False, regex=False)
    ]

filtered = filtered.sort_values("startTimeLocal", ascending=False)

# ---------------------------------------------------------------------------
# Résumé des filtres
# ---------------------------------------------------------------------------
if filtered.empty:
    st.info("Aucune activité ne correspond aux filtres sélectionnés.")
    st.stop()

# Intensité et charge de chaque sortie : même allure seuil (et donc même TSS)
# que la page Forme — calculée sur l'historique complet, pas sur le filtre.
enriched = enrich(filtered, reference_threshold_sec(df), history=df)   # TSS = celui du PMC

# Sélection (graphe ou liste) : chaque widget a une version dans sa clé. Quand
# l'un choisit une sortie, l'autre est remis à zéro — on ne garde qu'un choix
# visible, et recliquer une ligne ne la décoche pas en silence. (En revenant sur
# la page, Streamlit a purgé les deux widgets : la sélection « change » vers
# vide et le détail reste fermé — rien à faire de plus.)
for _k in ("_act_chart_ver", "_act_table_ver"):
    st.session_state.setdefault(_k, 0)
_chart_key = f"act_explorer_chart_{st.session_state['_act_chart_ver']}"

with st.container(key="card-act-kpi"):
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Activités", len(filtered))
    col2.metric("Volume", f"{filtered['distance_km'].sum():.1f} km")
    col3.metric("Durée", f"{filtered['duration_min'].sum() / 60:.1f} h")
    pace_vals = filtered.loc[filtered["avgPace_sec"] > 0, "avgPace_sec"]
    col4.metric("Allure moyenne", seconds_to_pace_str(pace_vals.mean()) if not pace_vals.empty else "—")
    col5.metric("Charge (TSS)", f"{enriched['tss'].sum():.0f}",
                help="Somme des charges de la période : même calcul que CTL/ATL sur la page Forme.")

# ---------------------------------------------------------------------------
# Explorateur : un indicateur, toutes les sorties, cliquables
# ---------------------------------------------------------------------------
_METRIC_LABELS = {"intensite": "Intensité", "fc": "FC", "allure": "Allure", "charge": "Charge",
                  "calories": "Calories", "distance": "Distance", "cadence": "Cadence",
                  "denivele": "Dénivelé"}
if st.session_state.get("act_metric") not in _METRIC_LABELS:
    st.session_state["act_metric"] = "intensite"
metric_key = st.segmented_control("Indicateur", list(_METRIC_LABELS), format_func=_METRIC_LABELS.get,
                                  key="act_metric", required=True)
column, axis_label, hover_value, sense = METRICS[metric_key]
plot_df = enriched.dropna(subset=[column]) if column in enriched else enriched.iloc[0:0]
plot_df = plot_df[pd.to_numeric(plot_df[column], errors="coerce") > 0] if metric_key != "intensite" else plot_df

_fig_sig = None                        # empreinte de la figure (aucune si pas de graphe)
with st.container(key="card-act-explorer"):
    if plot_df.empty:
        st.info(f"Aucune sortie filtrée ne porte cette donnée ({axis_label.lower()}).")
        chart_event = None
    else:
        plot_df = plot_df.sort_values("startTimeLocal")
        size = 8 + 22 * (plot_df["distance_km"] / max(plot_df["distance_km"].max(), 1)).clip(0, 1)
        fig = go.Figure()
        for zone in [*ZONE_COLORS]:
            part = plot_df[plot_df["zone"] == zone]
            if part.empty:
                continue
            custom = pd.DataFrame({
                "id": part["activityId"].astype("int64"),
                # Plotly interprète le HTML du survol : nom Garmin échappé.
                "name": part["activityName"].fillna("").astype(str).str.slice(0, 40).map(esc),
                "pace": part["avgPace"].fillna("—").astype(str),
                "dist": part["distance_km"].round(1),
                "zone": ZONE_LABELS[zone],
            })
            fig.add_trace(go.Scatter(
                x=part["startTimeLocal"], y=part[column], mode="markers", name=ZONE_LABELS[zone],
                marker=dict(size=size[part.index], color=ZONE_COLORS[zone], opacity=0.9,
                            line=dict(color=ct.SURFACE_2, width=1.5)),
                customdata=custom.to_numpy(),
                hovertemplate=("%{x|%a %d/%m} · %{customdata[1]}<br>" + hover_value
                               + " · %{customdata[3]} km · %{customdata[4]}<extra></extra>"),
            ))
        # Tendance : médiane glissante sur 28 jours (robuste aux séances atypiques)
        trend = (plot_df.set_index("startTimeLocal")[column].astype(float)
                 .rolling("28D", min_periods=3).median())
        if trend.notna().sum() >= 2:
            fig.add_trace(go.Scatter(x=trend.index, y=trend.to_numpy(), mode="lines", name="Tendance 4 sem.",
                                     line=dict(color=ct.INK_SECONDARY, width=2, dash="dot"),
                                     hoverinfo="skip"))
        if metric_key == "intensite":
            fig.add_hline(y=100, line=dict(color=ct.BASELINE, width=1, dash="dash"),
                          annotation_text="allure seuil", annotation_position="top left",
                          annotation_font=dict(color=ct.INK_MUTED, size=11))
        fig.update_layout(height=380, margin=dict(l=0, r=0, t=30, b=0),
                          yaxis=dict(title=axis_label, autorange="reversed" if sense == "lower" else True),
                          legend=dict(orientation="h", y=1.1), clickmode="event+select")
        # Streamlit identifie le graphe par TOUTE la figure : une figure nouvelle
        # (filtre, indicateur, Actualiser) repart sans sélection.
        _fig_sig = hashlib.md5(fig.to_json().encode()).hexdigest()
        chart_event = st.plotly_chart(fig, on_select="rerun", selection_mode="points",
                                      key=_chart_key)
        st.caption("Chaque point est une sortie (taille = distance, couleur = zone d'intensité). "
                   "**Clique un point** pour ouvrir son détail en bas de page.")
explain("intensite")

# ---------------------------------------------------------------------------
# Répartition de l'intensité (le 80/20)
# ---------------------------------------------------------------------------
dist = intensity_distribution(enriched)
polar = polarization(enriched)
if polar:
    with st.container(key="card-act-polar"):
        c_txt, c_chart = st.columns([1, 2], gap="large")
        with c_txt:
            html_block('<div class="gd-kicker">Répartition de l\'intensité</div>'
                       f'<div><span class="gd-big">{polar["easy"]:.0%}</span> '
                       f'{chip("en facile", polar["status"])}</div>')
            st.write(polar["verdict"])
            st.caption(f"Tempo {polar['grey']:.0%} · seuil et au-delà {polar['hard']:.0%} · "
                       f"{polar['minutes'] / 60:.0f} h de course sur la période. Repère : ~80 % "
                       "en facile (Seiler).")
        with c_chart:
            fig_d = go.Figure()
            for zone, color in ZONE_COLORS.items():
                part = dist[dist["zone"] == zone]
                if zone == "autre" or part.empty:
                    continue
                fig_d.add_trace(go.Bar(x=part["week"], y=part["minutes"] / 60, name=ZONE_LABELS[zone],
                                       marker_color=color,
                                       hovertemplate="Semaine du %{x|%d/%m} · %{y:.1f} h<extra>"
                                                     + ZONE_LABELS[zone] + "</extra>"))
            fig_d.update_layout(barmode="stack", height=240, margin=dict(l=0, r=0, t=10, b=0),
                                yaxis=dict(title="heures"), legend=dict(orientation="h", y=1.15))
            st.plotly_chart(fig_d, config={"displayModeBar": False})

# ---------------------------------------------------------------------------
# Toutes les sorties (sélection possible aussi depuis la liste)
# ---------------------------------------------------------------------------
_rows_sig = hashlib.md5(",".join(map(str, enriched["activityId"])).encode()).hexdigest()[:10]
display = enriched[[
    "activityId", "startTimeLocal", "activityName", "activityType", "zone", "intensity_pct",
    "distance_km", "duration_min", "avgPace", "avgHR", "tss", "calories", "elevationGain",
]].copy()
display["startTimeLocal"] = pd.to_datetime(display["startTimeLocal"]).dt.strftime("%d/%m/%Y %H:%M")
display["activityType"] = display["activityType"].map(type_labels).fillna(display["activityType"])
display["zone"] = display["zone"].map(ZONE_LABELS)
for col in ["avgHR", "calories", "elevationGain", "tss", "intensity_pct"]:
    display[col] = pd.to_numeric(display[col], errors="coerce")

with st.expander(f"Toutes les sorties ({len(display)})", expanded=False, icon=":material/list:"):
    _table_key = f"act_table_{_rows_sig}_{st.session_state['_act_table_ver']}"
    selected_event = st.dataframe(
        display.drop(columns=["activityId"]),
        width="stretch", hide_index=True, on_select="rerun", selection_mode="single-row",
        # Clé liée aux lignes affichées : un filtre change la liste, la sélection
        # repart de zéro (l'ancien index pointait hors de la liste → iloc plantait,
        # ou, pire, sur une autre sortie sans rien dire).
        key=_table_key,
        column_config={
            "startTimeLocal": st.column_config.TextColumn("Date"),
            "activityName": st.column_config.TextColumn("Nom"),
            "activityType": st.column_config.TextColumn("Type"),
            "zone": st.column_config.TextColumn("Zone"),
            "intensity_pct": st.column_config.ProgressColumn(
                "Intensité", help="Allure en % de ton allure seuil (100 % = seuil)",
                format="%.0f %%", min_value=50, max_value=120),
            "distance_km": st.column_config.ProgressColumn(
                "Distance", format="%.1f km", min_value=0,
                max_value=float(max(display["distance_km"].max(), 1))),
            "duration_min": st.column_config.NumberColumn("Durée", format="%.0f min"),
            "avgPace": st.column_config.TextColumn("Allure"),
            "avgHR": st.column_config.NumberColumn("FC moy", format="%d bpm"),
            "tss": st.column_config.ProgressColumn(
                "Charge", format="%.0f", min_value=0,
                max_value=float(max(display["tss"].max(skipna=True) or 1, 1))),
            "calories": st.column_config.NumberColumn("Calories", format="%d kcal"),
            "elevationGain": st.column_config.NumberColumn("D+", format="%d m"),
        },
    )

# Sortie choisie : le DERNIER geste, graphe ou liste (cf. versions de clés plus haut).
_points = (getattr(getattr(chart_event, "selection", None), "points", None) or []) if chart_event else []
_chart_sel = tuple(int(p["customdata"][0]) for p in _points if p.get("customdata"))
_rows = [r for r in (selected_event.selection.rows or []) if 0 <= r < len(display)]
_table_sel = tuple(int(display.iloc[r]["activityId"]) for r in _rows)
_prev_chart, _prev_table = st.session_state.get("_act_last_sel", ((), ()))
_prev_view = st.session_state.get("_act_last_view")
_view = (_rows_sig, _fig_sig)
if _prev_view is not None and _prev_view[0] != _view[0]:
    _prev_table = ()     # nouvelle liste (filtre) : widget neuf et vide, pas une ligne décochée
if _prev_view is not None and _prev_view[1] != _view[1]:
    _prev_chart = ()     # nouvelle figure : sélection vidée par Streamlit, pas un point désélectionné
st.session_state["_act_last_view"] = _view
_picked_id = st.session_state.get("_act_picked")
_reset = None
if _chart_sel != _prev_chart:
    # Le point AJOUTÉ (shift-clic : plusieurs points), sinon rien = désélection.
    _added = [i for i in _chart_sel if i not in _prev_chart]
    _picked_id = _added[-1] if _added else None
    if _picked_id is not None and _table_sel:
        _reset, _table_sel = "_act_table_ver", ()
elif _table_sel != _prev_table:
    _picked_id = _table_sel[0] if _table_sel else None           # ligne décochée : détail refermé
    if _picked_id is not None and _chart_sel:
        _reset, _chart_sel = "_act_chart_ver", ()
st.session_state["_act_last_sel"] = (_chart_sel, _table_sel)
st.session_state["_act_picked"] = _picked_id
if _reset:
    st.session_state[_reset] += 1        # l'autre widget repart vide au prochain run
    st.rerun()

# ---------------------------------------------------------------------------
# Détails de l'activité sélectionnée
# ---------------------------------------------------------------------------
if _picked_id is not None and (filtered["activityId"] == _picked_id).any():
    selected_row = filtered[filtered["activityId"] == _picked_id].iloc[0]
    activity_id = selected_row["activityId"]

    st.divider()
    st.subheader(f"🔍 Détails — {md_escape(selected_row.get('activityName', 'Activité'))}")

    date_fmt = pd.to_datetime(selected_row["startTimeLocal"]).strftime("%A %d %B %Y à %H:%M")
    st.caption(f"📅 {date_fmt}")

    # Métriques principales
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("📏 Distance", f"{selected_row['distance_km']:.2f} km")
    m2.metric("⏱️ Durée", f"{selected_row['duration_min']:.0f} min")
    m3.metric("🐇 Allure", selected_row["avgPace"])
    m4.metric("❤️ FC moy", f"{int(selected_row['avgHR'])} bpm" if pd.notna(selected_row.get("avgHR")) else "—")
    # avgCadence est en pas/min pour la course, en tours/min pour le vélo
    # (repli averageBikingCadenceInRevPerMinute de activity_row).
    cad_unit = "rpm" if selected_row.get("activityType") == "cycling" else "spm"
    m5.metric("🦶 Cadence", f"{int(selected_row['avgCadence'])} {cad_unit}" if pd.notna(selected_row.get("avgCadence")) else "—")

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
        if selected_row.get("activityType") == "running":
            render_signal_quality(streams)

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
                    ct.rgba(ct.GOOD, 0.9) if p > 0 and p < avg_split_pace
                    else ct.rgba(ct.PACE, 0.85)
                    for p in splits_df["pace_sec"]
                ],
                hovertemplate="<b>Lap %{x}</b><br>Allure : %{customdata}<extra></extra>",
                customdata=splits_df["pace"],
            ))

            fig_splits.update_layout(
                height=280,
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(
                    title="Lap",
                ),
                yaxis=dict(
                    title="Allure (min/km)",
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
