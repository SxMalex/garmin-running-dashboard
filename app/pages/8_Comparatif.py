"""
Page Comparatif annuel — superpose l'année en cours et les années précédentes
sur un axe « jour de l'année » : charge d'entraînement, volume, physiologie et
récupération. Répond à « comment je vais, comparé à la même date l'an dernier ? ».

Le périmètre est la course à pied, comme le reste du dashboard (la charge
CTL/ATL/TSB repose sur l'allure seuil, qui n'a pas de sens en vélo ou en renfo).
"""

from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import chart_theme
from comparatif_logic import (
    HRV_FIELDS,
    RESTING_HR_FIELDS,
    SLEEP_FIELDS,
    VO2MAX_FIELDS,
    add_year_doy,
    aligned_doy,
    cumulative_by_year,
    daily_sum_by_year,
    day_comparison,
    records_to_df,
    smooth_by_year,
    snapshot_at_doy,
    split_at_doy,
    year_summary,
)
from formatting import seconds_to_pace_str, weekday_fr
from next_session_logic import compute_pmc_series, reference_threshold_sec
from progression_logic import RACE_TARGETS, fmt_race_time
from ui_helpers import (
    cache_nonce,
    cached_load_activities,
    get_athlete_id,
    get_garmin_client,
    render_garmin_attribution,
    render_refresh_button,
    require_login,
)

st.set_page_config(
    page_title="Comparatif annuel — Running Dashboard",
    page_icon="📆",
    layout="wide",
)

require_login()

_athlete_id = get_athlete_id()

# Assez large pour couvrir plusieurs années : la pagination s'arrête d'elle-même
# quand Garmin ne renvoie plus rien (cf. GarminClient.get_activities).
# En dessous de ce nombre de sorties, une année est jugée trop partielle pour
# être comparée d'office (cf. `default_years`).
MIN_RUNS_PER_YEAR = 20

# Axe x commun : les jours de l'année sont projetés sur une année non bissextile
# fictive, ce qui donne des étiquettes lisibles (« 15 mars ») tout en gardant les
# années superposées.
_AXIS_ORIGIN = pd.Timestamp("2001-01-01")


# ---------------------------------------------------------------------------
# Chargement (caché par année, invalidable par le bouton d'actualisation)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def load_sleep_range(athlete_id: int, start: str, end: str, nonce: int) -> list:
    del nonce
    return get_garmin_client().get_sleep_range(start, end)


@st.cache_data(ttl=3600, show_spinner=False)
def load_hrv_range(athlete_id: int, start: str, end: str, nonce: int) -> list:
    del nonce
    return get_garmin_client().get_hrv_range(start, end)


@st.cache_data(ttl=3600, show_spinner=False)
def load_vo2max_range(athlete_id: int, start: str, end: str, nonce: int) -> list:
    del nonce
    return get_garmin_client().get_vo2max_range(start, end)


@st.cache_data(ttl=3600, show_spinner=False)
def load_resting_hr_range(athlete_id: int, start: str, end: str, nonce: int) -> list:
    del nonce
    return get_garmin_client().get_resting_hr_range(start, end)


@st.cache_data(ttl=3600, show_spinner=False)
def load_predictions_range(athlete_id: int, start: str, end: str, nonce: int) -> list:
    del nonce
    return get_garmin_client().get_race_predictions_range(start, end)


def _load_years(loader, years: list[int]) -> list[dict]:
    """
    Concatène une série quotidienne année par année : chaque appel reste dans une
    seule année civile, ce qui garde des clés de cache stables et des plages
    acceptées par Garmin.
    """
    today = date.today()
    rows = []
    for year in years:
        start = date(year, 1, 1).isoformat()
        end = min(date(year, 12, 31), today).isoformat()
        rows.extend(loader(_athlete_id, start, end, cache_nonce()) or [])
    return rows


# ---------------------------------------------------------------------------
# Helpers de rendu
# ---------------------------------------------------------------------------
def _axis_dates(doy: pd.Series) -> pd.Series:
    """Projette un jour de l'année sur l'axe calendaire fictif."""
    return _AXIS_ORIGIN + pd.to_timedelta(pd.to_numeric(doy) - 1, unit="D")


def _fmt_minutes(minutes: float) -> str:
    """Durée en minutes → « 45 min » ou « 1 h 12 »."""
    if minutes is None or pd.isna(minutes):
        return "—"
    hours, rest = divmod(int(round(minutes)), 60)
    return f"{hours} h {rest:02d}" if hours else f"{rest} min"


def _fmt_or_dash(value, template: str) -> str:
    """Applique un format, ou « — » si la valeur est absente."""
    if value is None or pd.isna(value):
        return "—"
    return template.format(value)


def _year_color(year: int, all_years: list[int]) -> str:
    """
    Couleur stable par année : l'année la plus récente prend le premier slot
    catégoriel, et garde sa teinte quelles que soient les années cochées.
    """
    try:
        index = all_years.index(int(year))
    except ValueError:
        index = len(all_years)
    return chart_theme.CAT[index % len(chart_theme.CAT)]


def _render_annual_lines(
    df: pd.DataFrame,
    value_col: str,
    *,
    all_years: list[int],
    today_doy: int,
    y_title: str,
    value_suffix: str = "",
    hover_decimals: int = 1,
    height: int = 380,
    zero_line: bool = False,
) -> None:
    """
    Superpose une série `year, doy, value_col` année par année.

    Ce qui suit la date du jour (donc uniquement des années révolues) est tracé
    en pointillé : la comparaison à date reste lisible sans masquer la fin des
    saisons passées.
    """
    plot_df = df.dropna(subset=[value_col])
    if plot_df.empty:
        st.info("Pas de données sur cette métrique pour les années sélectionnées.")
        return

    fig = go.Figure()
    for year in sorted(plot_df["year"].unique(), reverse=True):
        grp = plot_df[plot_df["year"] == year].sort_values("doy")
        color = _year_color(year, all_years)
        past, future = split_at_doy(grp, today_doy)
        hover = (
            f"<b>{int(year)}</b> · %{{x|%-d %b}}<br>"
            f"%{{y:.{hover_decimals}f}}{value_suffix}<extra></extra>"
        )
        if not past.empty:
            fig.add_trace(go.Scatter(
                x=_axis_dates(past["doy"]), y=past[value_col],
                mode="lines", name=str(int(year)),
                line=dict(color=color, width=2.4),
                hovertemplate=hover,
            ))
        if not future.empty and len(future) > 1:
            fig.add_trace(go.Scatter(
                x=_axis_dates(future["doy"]), y=future[value_col],
                mode="lines", name=str(int(year)),
                line=dict(color=color, width=1.4, dash="dot"),
                showlegend=past.empty, hovertemplate=hover,
            ))

    # Repère du jour : `add_vline(annotation_text=...)` casse sur un axe de dates
    # avec Plotly 5.24 (il moyenne les bornes), d'où l'annotation posée à part.
    today_x = _axis_dates(pd.Series([today_doy])).iloc[0]
    fig.add_vline(x=today_x, line_dash="dot", line_color=chart_theme.INK_MUTED)
    fig.add_annotation(
        x=today_x, y=1.0, yref="paper",
        text="aujourd'hui", showarrow=False,
        xanchor="left", yanchor="top",
        font=dict(color=chart_theme.INK_MUTED, size=10),
    )
    if zero_line:
        fig.add_hline(y=0, line_color=chart_theme.BASELINE, line_dash="dot")

    fig.update_layout(
        height=height,
        xaxis=dict(tickformat="%-d %b", hoverformat="%-d %b", title=None),
        yaxis=dict(title=y_title),
        hovermode="x unified",
        margin=dict(l=0, r=0, t=30, b=0),
    )
    st.plotly_chart(fig)


def _render_snapshot(
    container,
    label: str,
    snapshot: dict[int, float],
    *,
    current_year: int,
    reference_year: int | None,
    fmt,
    lower_is_better: bool = False,
    help_text: str | None = None,
) -> None:
    """
    Métrique « à la même date » : valeur de l'année en cours et écart avec
    l'année de référence. Sans valeur comparable, on affiche la valeur seule.
    """
    current = snapshot.get(current_year)
    if current is None:
        container.metric(label, "—", help=help_text)
        return

    reference = snapshot.get(reference_year) if reference_year else None
    if reference is None:
        container.metric(label, fmt(current), help=help_text)
        return

    diff = current - reference
    delta_text = fmt(abs(diff))
    # Un écart qui s'arrondit à zéro dans le format d'affichage (« +0.0 ») est
    # présenté comme stable plutôt que comme une hausse.
    if not any(char in "123456789" for char in delta_text):
        container.metric(
            label, fmt(current),
            delta=f"stable vs {reference_year}", delta_color="off", help=help_text,
        )
        return
    # Le signe moins doit rester un tiret ASCII : Streamlit déduit le sens du
    # delta d'un `startswith("-")`, et un « − » typographique lui ferait peindre
    # toute baisse comme une hausse (rouge sur une FC de repos qui descend).
    container.metric(
        label,
        fmt(current),
        delta=f"{'+' if diff > 0 else '-'}{delta_text} vs {reference_year}",
        delta_color="inverse" if lower_is_better else "normal",
        help=help_text,
    )


# ---------------------------------------------------------------------------
# Données de base
# ---------------------------------------------------------------------------
st.title("📆 Comparatif annuel")
st.caption(
    "Ton année en cours superposée aux précédentes, alignées sur le jour de "
    "l'année — charge, volume, physiologie et récupération à la même date."
)

df, error = cached_load_activities(_athlete_id)
if error:
    st.error(f"Erreur Garmin : {error}")
    st.stop()

running_df = df[df["activityType"] == "running"].copy() if not df.empty else pd.DataFrame()
if running_df.empty:
    st.info("Aucune activité de course chargée — impossible de comparer les années.")
    render_garmin_attribution()
    st.stop()

today = date.today()
today_doy = int(aligned_doy(pd.Series([pd.Timestamp(today)])).iloc[0])

_runs_per_year = add_year_doy(running_df, "startTimeLocal")["year"].value_counts()
all_years = sorted(_runs_per_year.index.tolist(), reverse=True)
current_year = all_years[0]

# Les années où l'historique Garmin ne couvre que quelques semaines (première
# montre, reprise en cours d'année) écraseraient les échelles et rempliraient le
# bandeau de zéros : proposées, mais pas cochées par défaut.
default_years = [
    year for year in all_years if _runs_per_year.get(year, 0) >= MIN_RUNS_PER_YEAR
] or all_years
_partial_years = [year for year in all_years if year not in default_years]

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## ⚙️ Paramètres")
    selected_years = st.multiselect(
        "Années comparées",
        options=all_years,
        default=default_years,
        format_func=str,
    )
    if _partial_years:
        st.caption(
            "Décochées par défaut car trop peu de sorties enregistrées : "
            + ", ".join(
                f"**{year}** ({_runs_per_year.get(year, 0)} sorties)"
                for year in _partial_years
            )
        )
    smooth_window = st.slider(
        "Lissage des séries quotidiennes (jours)", 1, 21, 7,
        help="Moyenne glissante appliquée au VO2max, à la FC de repos, au "
             "sommeil et à la HRV — ces mesures sont bruitées au jour le jour.",
    )
    st.divider()
    render_refresh_button("🔄 Actualiser")

if not selected_years:
    st.warning("Sélectionne au moins une année dans la barre latérale.")
    st.stop()

years = sorted(selected_years, reverse=True)
reference_year = next((y for y in years if y != current_year), None)
runs = add_year_doy(running_df, "startTimeLocal")
runs = runs[runs["year"].isin(years)]

# Dernier jour tracé par année : aujourd'hui pour l'année en cours, fin d'année
# pour les précédentes (les courbes des saisons révolues vont jusqu'au bout).
last_doy = {int(y): (today_doy if int(y) == current_year else 365) for y in years}

if reference_year is None:
    st.info(
        f"Une seule année disponible dans ton historique Garmin ({current_year}). "
        "Les comparatifs s'enrichiront dès que tu auras une saison complète de plus."
    )

# ---------------------------------------------------------------------------
# Bandeau « à la même date »
# ---------------------------------------------------------------------------
# La courbe de charge intègre toutes les activités ; `reference_threshold_sec`
# ne lit que la course, même sur le DataFrame complet.
threshold_sec = reference_threshold_sec(df)
pmc = compute_pmc_series(df, threshold_sec)
pmc_years = add_year_doy(pmc, "date")
pmc_years = pmc_years[pmc_years["year"].isin(years)]

km_daily = daily_sum_by_year(runs, "distance_km")
km_cumul = cumulative_by_year(km_daily, "distance_km", last_doy)

with st.spinner("Chargement de la physiologie (VO2max, FC repos, prédictions)…"):
    vo2_df = records_to_df(_load_years(load_vo2max_range, years), VO2MAX_FIELDS)
    rhr_df = records_to_df(_load_years(load_resting_hr_range, years), RESTING_HR_FIELDS)
    pred_fields = {label: key for label, _km, key in RACE_TARGETS}
    pred_df = records_to_df(_load_years(load_predictions_range, years), pred_fields)

with st.spinner("Chargement de la récupération (sommeil, HRV)…"):
    sleep_df = records_to_df(_load_years(load_sleep_range, years), SLEEP_FIELDS)
    hrv_df = records_to_df(_load_years(load_hrv_range, years), HRV_FIELDS)

if not sleep_df.empty:
    sleep_df["sleep_h"] = sleep_df["sleep_sec"] / 3600
    sleep_df = smooth_by_year(sleep_df, "sleep_h", smooth_window)
    sleep_df = smooth_by_year(sleep_df, "sleep_score", smooth_window)
if not vo2_df.empty:
    vo2_df = smooth_by_year(vo2_df, "vo2max", smooth_window)
if not rhr_df.empty:
    rhr_df = smooth_by_year(rhr_df, "resting_hr", smooth_window)
if not hrv_df.empty:
    hrv_df = smooth_by_year(hrv_df, "hrv", smooth_window)

st.subheader("📍 À la même date")
st.caption(
    f"Chaque valeur est prise au même jour de l'année ({today.strftime('%d/%m')}), "
    + (f"comparée à {reference_year}." if reference_year else "sans année de référence.")
)

k1, k2, k3, k4, k5, k6 = st.columns(6)
_render_snapshot(
    k1, "⚡ CTL — Forme",
    snapshot_at_doy(pmc_years, "ctl", today_doy, tolerance=0),
    current_year=current_year, reference_year=reference_year,
    fmt=lambda v: f"{v:.1f}",
    help_text="Charge chronique (42 j) au même jour de l'année.",
)
_render_snapshot(
    k2, "🏃 Km cumulés",
    snapshot_at_doy(km_cumul, "cumul", today_doy, tolerance=0),
    current_year=current_year, reference_year=reference_year,
    fmt=lambda v: f"{v:,.0f} km".replace(",", " "),
    help_text="Distance courue depuis le 1er janvier.",
)
_render_snapshot(
    k3, "💨 VO2max",
    snapshot_at_doy(vo2_df, "vo2max", today_doy, tolerance=14),
    current_year=current_year, reference_year=reference_year,
    fmt=lambda v: f"{v:.1f}",
    help_text="Dernière estimation Garmin dans les 14 jours précédents.",
)
_render_snapshot(
    k4, "❤️ FC repos",
    snapshot_at_doy(rhr_df, "resting_hr_smooth", today_doy, tolerance=7),
    current_year=current_year, reference_year=reference_year,
    fmt=lambda v: f"{v:.0f} bpm",
    lower_is_better=True,
    help_text=f"Moyenne lissée sur {smooth_window} j — plus bas est mieux.",
)
_render_snapshot(
    k5, "😴 Sommeil",
    snapshot_at_doy(sleep_df, "sleep_h_smooth", today_doy, tolerance=7),
    current_year=current_year, reference_year=reference_year,
    fmt=lambda v: f"{int(v)}h{int(round((v % 1) * 60)):02d}",
    help_text=f"Durée moyenne lissée sur {smooth_window} j.",
)
_render_snapshot(
    k6, "💓 HRV",
    snapshot_at_doy(hrv_df, "hrv_smooth", today_doy, tolerance=7),
    current_year=current_year, reference_year=reference_year,
    fmt=lambda v: f"{v:.0f} ms",
    help_text=f"Moyenne lissée sur {smooth_window} j.",
)

st.divider()

# ---------------------------------------------------------------------------
# La sortie du jour, année par année
# ---------------------------------------------------------------------------
st.subheader(f"🎯 La sortie du {today.strftime('%d/%m')}, année par année")

day_rows = day_comparison(runs, today_doy)
_day_by_year = {int(r["year"]): r for _, r in day_rows.iterrows()}

if not _day_by_year:
    st.info(
        f"Aucune course un {today.strftime('%d/%m')}, ni cette année ni les "
        "précédentes sélectionnées."
    )
else:
    # Une colonne par année sélectionnée : les années sans course ce jour-là sont
    # affichées en « repos » plutôt que masquées — l'absence est une information.
    _day_metrics = [
        ("Sortie", lambda r: " + ".join(r["names"]) or "Course"),
        ("Départ", lambda r: f"{weekday_fr(r['date'])} à {r['start_time']}"),
        ("Distance", lambda r: f"{r['km']:.2f} km"),
        ("Durée", lambda r: _fmt_minutes(r["minutes"])),
        ("Allure", lambda r: seconds_to_pace_str(r["pace_sec"])),
        ("FC moyenne", lambda r: _fmt_or_dash(r["avgHR"], "{:.0f} bpm")),
        ("FC max", lambda r: _fmt_or_dash(r["maxHR"], "{:.0f} bpm")),
        ("D+", lambda r: f"{r['elevation']:.0f} m"),
        ("Cadence", lambda r: _fmt_or_dash(r["cadence"], "{:.0f} pas/min")),
        ("Charge", lambda r: f"{r['trainingLoad']:.0f}"),
        ("Calories", lambda r: f"{r['calories']:.0f} kcal"),
    ]
    def _day_column(year: int) -> list[str]:
        """Valeurs d'une année ; « repos » n'est écrit qu'une fois, en tête."""
        row = _day_by_year.get(year)
        if row is None:
            return ["— repos —"] + ["—"] * (len(_day_metrics) - 1)
        return [fn(row) for _label, fn in _day_metrics]

    day_table = pd.DataFrame(
        {str(year): _day_column(year) for year in years},
        index=[label for label, _fn in _day_metrics],
    )
    st.dataframe(day_table, width="stretch")

    _current = _day_by_year.get(current_year)
    _reference = _day_by_year.get(reference_year) if reference_year else None
    if _current is not None and _reference is not None:
        _km_diff = _current["km"] - _reference["km"]
        _pace_diff = _current["pace_sec"] - _reference["pace_sec"]
        st.caption(
            f"Face au {reference_year} : **{_km_diff:+.2f} km** et "
            f"**{abs(_pace_diff):.0f} s/km "
            f"{'plus lent' if _pace_diff > 0 else 'plus rapide'}**"
            + (
                f", pour une charge de {_current['trainingLoad']:.0f} "
                f"contre {_reference['trainingLoad']:.0f}."
                if _current["trainingLoad"] and _reference["trainingLoad"]
                else "."
            )
        )
    elif _current is None:
        st.caption(
            f"Pas (encore) de course aujourd'hui — la colonne {current_year} "
            "se remplira dès ta prochaine sortie."
        )
    if any(len(row["names"]) > 1 for row in _day_by_year.values()):
        st.caption(
            "Les journées à plusieurs sorties sont additionnées : allure, FC et "
            "cadence sont recalculées sur les totaux du jour."
        )

st.divider()

# ---------------------------------------------------------------------------
# 1. Charge d'entraînement
# ---------------------------------------------------------------------------
st.subheader("⚡ Charge d'entraînement")
st.caption(
    f"Modèle PMC calculé sur tout l'historique avec une allure seuil unique "
    f"(**{seconds_to_pace_str(threshold_sec)}**) — sans quoi les TSS d'une année "
    "à l'autre ne seraient pas comparables."
)
charge_metric = st.radio(
    "Métrique de charge",
    options=["CTL — Forme", "ATL — Fatigue", "TSB — Fraîcheur"],
    horizontal=True,
    key="comparatif_charge_metric",
    label_visibility="collapsed",
)
_charge_col = {"CTL — Forme": "ctl", "ATL — Fatigue": "atl", "TSB — Fraîcheur": "tsb"}[charge_metric]
_render_annual_lines(
    pmc_years, _charge_col,
    all_years=all_years, today_doy=today_doy,
    y_title=charge_metric,
    zero_line=(_charge_col == "tsb"),
)

st.divider()

# ---------------------------------------------------------------------------
# 2. Volume & allure
# ---------------------------------------------------------------------------
st.subheader("🏃 Volume & allure")

volume_metric = st.radio(
    "Métrique de volume",
    options=["Kilomètres", "Dénivelé positif", "Heures de course"],
    horizontal=True,
    key="comparatif_volume_metric",
    label_visibility="collapsed",
)
_volume_specs = {
    "Kilomètres": ("distance_km", "Km cumulés", " km", 0),
    "Dénivelé positif": ("elevationGain", "D+ cumulé (m)", " m", 0),
    "Heures de course": ("duration_min", "Heures cumulées", " h", 1),
}
_col, _title, _suffix, _decimals = _volume_specs[volume_metric]
_daily = daily_sum_by_year(runs, _col)
if volume_metric == "Heures de course" and not _daily.empty:
    _daily[_col] = _daily[_col] / 60
_cumul = cumulative_by_year(_daily, _col, last_doy)
_render_annual_lines(
    _cumul, "cumul",
    all_years=all_years, today_doy=today_doy,
    y_title=_title, value_suffix=_suffix, hover_decimals=_decimals,
)

summary = year_summary(runs, today_doy)
if not summary.empty:
    st.markdown(f"**Bilan arrêté au {today.strftime('%d/%m')} de chaque année**")
    table = pd.DataFrame({
        "Année": summary["year"].astype(str),
        "Sorties": summary["sorties"],
        "Distance": summary["km"].map(lambda v: f"{v:,.0f} km".replace(",", " ")),
        "D+": summary["denivele"].map(lambda v: f"{v:,.0f} m".replace(",", " ")),
        "Temps": summary["heures"].map(lambda v: f"{v:.0f} h"),
        "Allure moyenne": summary["pace_sec"].map(
            lambda v: seconds_to_pace_str(v) if pd.notna(v) else "—"
        ),
        "FC moyenne": summary["fc_moy"].map(
            lambda v: f"{v:.0f} bpm" if pd.notna(v) else "—"
        ),
    })
    st.dataframe(table, hide_index=True, width="stretch")

st.divider()

# ---------------------------------------------------------------------------
# 3. Physiologie
# ---------------------------------------------------------------------------
st.subheader("💨 Physiologie")

physio_metric = st.radio(
    "Métrique physiologique",
    options=["VO2max", "FC de repos", "Prédictions de course"],
    horizontal=True,
    key="comparatif_physio_metric",
    label_visibility="collapsed",
)

if physio_metric == "VO2max":
    _render_annual_lines(
        vo2_df, "vo2max_smooth" if "vo2max_smooth" in vo2_df.columns else "vo2max",
        all_years=all_years, today_doy=today_doy,
        y_title="VO2max (ml/kg/min)",
    )
elif physio_metric == "FC de repos":
    _render_annual_lines(
        rhr_df, "resting_hr_smooth" if "resting_hr_smooth" in rhr_df.columns else "resting_hr",
        all_years=all_years, today_doy=today_doy,
        y_title="FC de repos (bpm)", value_suffix=" bpm", hover_decimals=0,
    )
else:
    distance_label = st.radio(
        "Distance",
        options=[label for label, _km, _key in RACE_TARGETS],
        horizontal=True,
        key="comparatif_pred_distance",
    )
    if pred_df.empty or distance_label not in pred_df.columns:
        st.info("Pas d'historique de prédictions Garmin sur les années sélectionnées.")
    else:
        pred_view = smooth_by_year(pred_df, distance_label, smooth_window, out_col="pred")
        pred_view["pred_min"] = pred_view["pred"] / 60
        _render_annual_lines(
            pred_view, "pred_min",
            all_years=all_years, today_doy=today_doy,
            y_title=f"Temps prédit sur {distance_label} (min)",
            value_suffix=" min", hover_decimals=1,
        )
        snap = snapshot_at_doy(pred_view, "pred", today_doy, tolerance=14)
        if snap:
            st.caption(
                "À la même date : "
                + " · ".join(
                    f"**{year}** {fmt_race_time(value)}"
                    for year, value in sorted(snap.items(), reverse=True)
                )
            )

st.divider()

# ---------------------------------------------------------------------------
# 4. Récupération
# ---------------------------------------------------------------------------
st.subheader("😴 Récupération")

recup_metric = st.radio(
    "Métrique de récupération",
    options=["Durée de sommeil", "Score de sommeil", "HRV nuit"],
    horizontal=True,
    key="comparatif_recup_metric",
    label_visibility="collapsed",
)

if recup_metric == "Durée de sommeil":
    _render_annual_lines(
        sleep_df, "sleep_h_smooth" if "sleep_h_smooth" in sleep_df.columns else "sleep_h",
        all_years=all_years, today_doy=today_doy,
        y_title="Durée de sommeil (h)", value_suffix=" h",
    )
elif recup_metric == "Score de sommeil":
    _render_annual_lines(
        sleep_df, "sleep_score_smooth" if "sleep_score_smooth" in sleep_df.columns else "sleep_score",
        all_years=all_years, today_doy=today_doy,
        y_title="Score de sommeil Garmin", hover_decimals=0,
    )
else:
    _render_annual_lines(
        hrv_df, "hrv_smooth" if "hrv_smooth" in hrv_df.columns else "hrv",
        all_years=all_years, today_doy=today_doy,
        y_title="HRV nuit (ms)", value_suffix=" ms", hover_decimals=0,
    )

render_garmin_attribution()
