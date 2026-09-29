"""
Jour de course — plan d'allure kilomètre par kilomètre (GPX du parcours, pente →
coût énergétique ; stratégie progressive ou régulière), correction chaleur et
ravitaillement placé au kilomètre. Logique pure : `raceday_logic` ; habitude de
gestion des courses passées : `compare_logic.pacing_tendency`.
"""

from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import chart_theme as ct
import goal_store
from compare_logic import pacing_tendency, run_summary
from garmin_client import compute_km_splits
from progression_logic import fmt_race_time
from race_plan_logic import DISTANCES, athlete_baseline, parse_race_time, predictions_by_km
from raceday_logic import (
    PACING_STRATEGIES,
    GpxError,
    course_prediction,
    flat_equivalent_km,
    goal_distance_for,
    fmt_clock,
    fmt_pace,
    fueling_plan,
    half_split_pct,
    heat_slowdown,
    implausible_target,
    km_profile,
    pacing_plan,
    parse_gpx,
    reading_distance,
)
from ui_helpers import (
    athlete_id_is_reliable,
    cache_nonce,
    cached_load_activities,
    get_athlete_id,
    get_garmin_client,
    render_garmin_attribution,
    require_login,
)
from ui_mode import explain
from ui_theme import chip, html_block

st.set_page_config(page_title="Jour de course — Running Dashboard", page_icon="🏁", layout="wide")
require_login()
_athlete_id = get_athlete_id()


@st.cache_data(ttl=86400, show_spinner=False)
def load_predictions(athlete_id: int, nonce: int) -> dict:
    return get_garmin_client().get_race_predictions()


PAST_RACES = 3   # courses relues pour l'habitude de gestion (streams en cache 30 j)


@st.cache_data(ttl=3600, show_spinner=False)
def past_race_splits(athlete_id: int, ids: tuple[int, ...], nonce: int) -> list[float | None]:
    """Split 2e / 1re moitié (à pente égale) des dernières courses ; s'arrête au premier refus Garmin."""
    gc, out = get_garmin_client(), []
    df, _err = cached_load_activities(athlete_id)
    rows = df.drop_duplicates("activityId").set_index("activityId")
    for aid in ids:
        try:
            streams = gc.get_streams(aid, strict=True)
        except Exception:
            break
        out.append(run_summary(rows.loc[aid].to_dict() | {"activityId": aid},
                               compute_km_splits(streams))["split_pct"])
    return out


def _flat_profile(km_total: float) -> pd.DataFrame:
    """Sans GPX : un parcours plat de la distance de la course."""
    edges = list(np.arange(0, km_total * 1000, 1000.0)) + [km_total * 1000]
    rows = [{"km": k + 1, "length_m": edges[k + 1] - edges[k], "gain": 0.0, "loss": 0.0,
             "grade": 0.0, "ele_end": 0.0} for k in range(len(edges) - 1) if edges[k + 1] - edges[k] >= 50]
    return pd.DataFrame(rows)


st.title("Jour de course")
st.caption("Ton allure kilomètre par kilomètre à effort égal, la correction chaleur et le "
           "ravitaillement placé au bon endroit. Importe le GPX du parcours (site de "
           "l'organisateur, Strava, Garmin) ; sans GPX, le calcul suppose un parcours plat.")

# Sous un id de repli, l'objectif serait lu dans un autre dossier : pas de pré-remplissage.
goal = (goal_store.load(_athlete_id).get("goal") or {}) if athlete_id_is_reliable() else {}
default_distance = goal.get("distance") if goal.get("distance") in DISTANCES else "10 km"

@st.cache_data(max_entries=4, show_spinner="Lecture du parcours…")
def _read_course(data: bytes) -> tuple[pd.DataFrame, pd.DataFrame]:
    """GPX → (trace, profil au km), une fois par fichier : pas à chaque frappe dans la page."""
    track = parse_gpx(data)
    return track, km_profile(track)


def _default_target(total_km: float, label: str | None) -> tuple[str, float | None, str]:
    """(texte par défaut, temps prédit, source) pour le parcours courant."""
    preds = predictions_by_km(load_predictions(_athlete_id, cache_nonce()))
    pred, source = course_prediction(preds, total_km), "Prédiction Garmin"
    if not pred:
        # Sans prédiction Garmin : ta forme réelle (même estimation que la page
        # Objectif — course récente, sinon entraînements), projetée par Riegel.
        df, _err = cached_load_activities(_athlete_id)
        if not df.empty:
            p10 = athlete_baseline(df, date.today(), preds)["pace_10k_sec"]
            pred = p10 * 10 * (total_km / 10) ** 1.06
            source = "Estimation d'après tes sorties"
    if label and goal.get("distance") == label and goal.get("target_text"):
        return goal["target_text"], pred, source
    return (fmt_race_time(pred).replace("'", ":").replace('"', "") if pred else ""), pred, source


track = None
with st.container(key="card-rd-inputs"):
    c1, c2, c3 = st.columns([1.2, 1, 1], gap="medium")
    with c1:
        gpx_file = st.file_uploader("Parcours (GPX)", type=["gpx"], key="rd_gpx",
                                    help="Fichier GPX du parcours officiel. 5 Mo maximum.")
        if "rd_distance" not in st.session_state:
            st.session_state["rd_distance"] = default_distance
        distance = st.selectbox("Distance (si pas de GPX)", list(DISTANCES), key="rd_distance",
                                disabled=gpx_file is not None)
        if gpx_file is not None:
            try:
                track, profile = _read_course(gpx_file.getvalue())
            except GpxError as e:
                st.error(str(e))
                st.stop()
            total_km = float(profile["length_m"].sum()) / 1000
            course_id, course_label = f"gpx:{gpx_file.name}:{total_km:.2f}", None
            # Le GPX officiel de la course visée (±5 %) reprend l'objectif enregistré :
            # sinon l'import écrasait « 1:45:00 » par la prédiction Garmin.
            course_label = goal_distance_for(total_km, goal.get("distance"), DISTANCES)
            if track.attrs.get("note"):
                st.caption(track.attrs["note"])
        else:
            total_km = DISTANCES[distance]
            profile = _flat_profile(total_km)
            course_id, course_label = distance, distance
    with c2:
        default_text, pred, pred_source = _default_target(total_km, course_label)
        # Nouveau parcours (distance changée, GPX importé) : le temps visé repart du
        # défaut de CE parcours. Sinon « 50:00 » restait en passant au semi.
        _prev = st.session_state.get("_rd_course")
        st.session_state["_rd_course"] = course_id
        if "rd_target" not in st.session_state or (_prev is not None and _prev != course_id):
            st.session_state["rd_target"] = default_text
        target_text = st.text_input("Temps visé", key="rd_target", placeholder="ex. 1:45:00 ou 50:00",
                                    help="Par défaut : ton objectif enregistré, sinon la prédiction "
                                         "Garmin. Remis à jour quand tu changes de parcours.")
        if pred:
            where = course_label or f"{total_km:.1f} km"
            st.caption(f"{pred_source} sur {where} : {fmt_race_time(pred)}")
    with c3:
        st.markdown("**Météo prévue** (facultatif)")
        temp_c = st.number_input("Température (°C)", min_value=-10.0, max_value=45.0, value=None,
                                 step=1.0, key="rd_temp")
        dew_c = st.number_input("Point de rosée (°C)", min_value=-20.0, max_value=35.0, value=None,
                                step=1.0, key="rd_dew", help="Donné par la plupart des applis météo. "
                                "Il mesure l'humidité : au-dessus de 15 °C, l'air devient lourd.")

# ---------------------------------------------------------------------------
# Temps visé → plan (garde-fou : une allure absurde est un format mal lu)
# ---------------------------------------------------------------------------
target_s = parse_race_time(target_text, course_label or reading_distance(total_km))
if not target_s:
    st.info("Indique un temps visé (ex. 50:00 ou 1:45:00) pour calculer le plan.")
    st.stop()
_problem = implausible_target(target_s, total_km, flat_equivalent_km(profile))
if _problem:
    st.warning(_problem)
    st.stop()

heat = heat_slowdown(temp_c, dew_c)

# ---------------------------------------------------------------------------
# Stratégie : progressive par défaut. « Régulière » sur un parcours plat donne
# la même allure à chaque kilomètre — c'est ce qui faisait un bracelet figé.
# ---------------------------------------------------------------------------
if "rd_strategy" not in st.session_state:
    st.session_state["rd_strategy"] = "progressive"
with st.container(key="card-rd-strategy"):
    s1, s2 = st.columns([1, 1.4], gap="large")
    with s1:
        strategy = st.segmented_control(
            "Stratégie d'allure", list(PACING_STRATEGIES), format_func=PACING_STRATEGIES.get,
            key="rd_strategy", help="Progressive : départ retenu, milieu stable, fin plus rapide "
            "(léger negative split). Régulière : même effort du début à la fin.") or "progressive"
        apply_heat = False
        if heat and heat["mid"] > 0 and not heat["hard"]:
            if "rd_heat_apply" not in st.session_state:
                st.session_state["rd_heat_apply"] = True
            apply_heat = st.checkbox("Caler le bracelet sur l'objectif corrigé de la chaleur",
                                     key="rd_heat_apply")
    with s2:
        _df, _err = cached_load_activities(_athlete_id)
        _races = (_df[(_df["activityType"] == "running") & (_df["workoutType"] == "race")]
                  .sort_values("startTimeLocal", ascending=False).head(PAST_RACES)
                  if not _df.empty else _df)
        tendency = (pacing_tendency(past_race_splits(_athlete_id, tuple(int(i) for i in _races["activityId"]),
                                                     cache_nonce())) if not _races.empty else None)
        if tendency:
            fade = chip(f"2e moitié {tendency['mean']:+.1f} %", tendency["status"])
            html_block(f'<div class="gd-kicker">Tes {tendency["n"]} dernières courses</div>'
                       f'<div>{fade} {tendency["advice"]}</div>')
        else:
            st.caption("Pas assez de courses enregistrées (type « course » sur Garmin) pour lire "
                       "ta gestion habituelle : la stratégie progressive reste le choix prudent.")

plan_target_s = target_s * (1 + heat["mid"] / 100) if apply_heat else target_s
plan = pacing_plan(profile, plan_target_s, strategy)
fuel = fueling_plan(plan)

with st.container(key="card-rd-summary"):
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Allure moyenne", f"{fmt_pace(plan_target_s / total_km)}/km",
              delta=f"{total_km:.2f} km en {fmt_race_time(plan_target_s)}", delta_color="off", delta_arrow="off")
    k2.metric("Départ → arrivée", f"{fmt_pace(plan['pace_s'].iloc[0])} → {fmt_pace(plan['pace_s'].iloc[-1])}",
              delta=f"2e moitié {half_split_pct(plan):+.1f} %", delta_color="off", delta_arrow="off",
              help="Allure du premier et du dernier kilomètre. Elle ralentit aussi en montée et "
                   "accélère (un peu) en descente.")
    k3.metric("Dénivelé", f"+{profile['gain'].sum():.0f} m / −{profile['loss'].sum():.0f} m")
    lo, hi = fuel["carbs_g_per_h"]
    k4.metric("Glucides", f"{lo}-{hi} g/h" if hi else "eau seule",
              delta=f"{len(fuel['events'])} prise(s)" if fuel["events"] else None,
              delta_color="off", delta_arrow="off")
    if heat and heat["mid"] > 0:
        adjusted = target_s * (1 + heat["mid"] / 100)
        status = "serious" if heat["hard"] else "warning"
        html_block(f'<div style="margin-top:.6rem">{chip("Chaleur", status)} ' + (
            "Au-delà du barème : pas de course à l'objectif, vise l'arrivée et bois tôt."
            if heat["hard"] else
            f"À effort égal, compte {heat['low']:g} à {heat['high']:g} % plus lent : "
            f"objectif réaliste ≈ {fmt_race_time(adjusted)}"
            + (" — le bracelet est calé dessus." if apply_heat else " — bracelet sur le temps visé.")
            ) + "</div>")
    elif heat:
        html_block(f'<div style="margin-top:.6rem">{chip("Météo idéale", "good")} '
                   "Pas de correction chaleur.</div>")

fig = make_subplots(specs=[[{"secondary_y": True}]])
if track is not None:
    fig.add_trace(go.Scatter(x=track["dist"] / 1000, y=track["ele"], mode="lines", name="Altitude",
                             line=dict(color=ct.VIOLET, width=1.5), fill="tozeroy",
                             fillcolor=ct.rgba(ct.VIOLET, 0.12),
                             hovertemplate="km %{x:.1f} · %{y:.0f} m<extra></extra>"), secondary_y=True)
# Allure en marches (une par kilomètre) sur un axe resserré : des barres partant
# de zéro écrasaient 5:03 → 4:56 en une rangée de barres identiques.
_x = np.append(plan["km"].to_numpy() - 1, plan["km"].iloc[-1] - 1 + plan["length_m"].iloc[-1] / 1000)
_y = np.append(plan["pace_s"].to_numpy(), plan["pace_s"].iloc[-1]) / 60
fig.add_trace(go.Scatter(x=_x, y=_y, mode="lines", line=dict(color=ct.PACE, width=2.5, shape="hv"),
                         name="Allure", hoverinfo="skip"), secondary_y=False)
fig.add_trace(go.Scatter(x=plan["km"] - 0.5, y=plan["pace_s"] / 60, mode="markers", showlegend=False,
                         marker=dict(size=9, color=[ct.ORANGE if g > 0.02 else ct.AQUA if g < -0.02
                                                    else ct.BLUE for g in plan["grade"]],
                                     line=dict(color=ct.SURFACE_2, width=1.5)),
                         customdata=np.stack([plan["pace_s"].map(fmt_pace), plan["grade"] * 100,
                                              plan["elapsed_s"].map(fmt_race_time)], axis=1),
                         hovertemplate="km %{x:.0f} · %{customdata[0]}/km · pente %{customdata[1]:+.1f} %"
                                       "<br>passage %{customdata[2]}<extra></extra>"), secondary_y=False)
for e in fuel["events"]:
    fig.add_vline(x=e["km"], line=dict(color=ct.INK_MUTED, width=1, dash="dot"))
_lo, _hi = float(plan["pace_s"].min()), float(plan["pace_s"].max())
_ticks = list(range(int(_lo // 5 * 5), int(_hi) + 6, 5 if _hi - _lo <= 40 else 15))
fig.update_yaxes(title_text="allure (min/km)", autorange=False, range=[(_hi + 6) / 60, (_lo - 6) / 60],
                 tickvals=[t / 60 for t in _ticks], ticktext=[fmt_pace(t) for t in _ticks],
                 secondary_y=False)
fig.update_yaxes(title_text="altitude (m)", secondary_y=True, showgrid=False)
fig.update_layout(height=360, margin=dict(l=0, r=0, t=30, b=0), legend=dict(orientation="h", y=1.12),
                  xaxis=dict(title="km"))
st.plotly_chart(fig)
st.caption("Allure kilomètre par kilomètre, plus haut = plus rapide (point orange = montée, "
           "turquoise = descente). Pointillés : prises de ravitaillement.")

col_tab, col_fuel = st.columns([3, 2], gap="large")
with col_tab:
    st.markdown("#### Bracelet d'allure")
    table = pd.DataFrame({"km": plan["km"], "pente": (plan["grade"] * 100).round(1),
                          "allure": plan["pace_s"].map(fmt_pace), "passage": plan["elapsed_s"].map(fmt_race_time)})
    st.dataframe(table, hide_index=True, width="stretch",
                 column_config={"pente": st.column_config.NumberColumn("pente", format="%+.1f %%")})
    st.download_button("Télécharger le bracelet (CSV)", table.to_csv(index=False).encode(),
                       file_name=f"bracelet_{date.today().isoformat()}.csv", mime="text/csv",
                       icon=":material/download:")
with col_fuel, st.container(key="card-rd-fuel"):
    st.markdown("#### Ravitaillement")
    if fuel["events"]:
        for e in fuel["events"]:
            st.markdown(f"- **km {e['km']}** ({fmt_clock(e['at_s'])}) : {e['what']}")
        st.caption(f"Eau : {fuel['water_ml_per_h'][0]}-{fuel['water_ml_per_h'][1]} ml/h selon la chaleur. "
                   + fuel["note"])
    else:
        st.write(fuel["note"])
explain("jour_de_course")
render_garmin_attribution()
