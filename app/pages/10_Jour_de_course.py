"""
Jour de course — plan d'allure kilomètre par kilomètre à effort égal (GPX du
parcours, pente → coût énergétique), correction chaleur et ravitaillement placé
au kilomètre. Logique pure : `raceday_logic`.
"""

from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import chart_theme as ct
import goal_store
from progression_logic import fmt_race_time
from race_plan_logic import DISTANCES, athlete_baseline, parse_race_time, predictions_by_km
from raceday_logic import (
    GpxError,
    fmt_clock,
    fmt_pace,
    fueling_plan,
    heat_slowdown,
    km_profile,
    pacing_plan,
    parse_gpx,
)
from ui_helpers import (
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

goal = goal_store.load(_athlete_id).get("goal") or {}
default_distance = goal.get("distance") if goal.get("distance") in DISTANCES else "10 km"

with st.container(key="card-rd-inputs"):
    c1, c2, c3 = st.columns([1.2, 1, 1], gap="medium")
    with c1:
        gpx_file = st.file_uploader("Parcours (GPX)", type=["gpx"], key="rd_gpx",
                                    help="Fichier GPX du parcours officiel. 5 Mo maximum.")
        if "rd_distance" not in st.session_state:
            st.session_state["rd_distance"] = default_distance
        distance = st.selectbox("Distance (si pas de GPX)", list(DISTANCES), key="rd_distance",
                                disabled=gpx_file is not None)
    with c2:
        preds = predictions_by_km(load_predictions(_athlete_id, cache_nonce()))
        pred = preds.get(DISTANCES[distance])
        pred_source = "Prédiction Garmin"
        if not pred:
            # Sans prédiction Garmin : ta forme réelle (même estimation que la page
            # Objectif — course récente, sinon entraînements), projetée par Riegel.
            df, _err = cached_load_activities(_athlete_id)
            if not df.empty:
                p10 = athlete_baseline(df, date.today(), preds)["pace_10k_sec"]
                pred = p10 * 10 * (DISTANCES[distance] / 10) ** 1.06
                pred_source = "Estimation d'après tes sorties"
        if "rd_target" not in st.session_state:
            st.session_state["rd_target"] = (goal.get("target_text") if goal.get("distance") == distance
                                             and goal.get("target_text") else
                                             (fmt_race_time(pred).replace("'", ":").replace('"', "")
                                              if pred else ""))
        target_text = st.text_input("Temps visé", key="rd_target", placeholder="ex. 1:45:00 ou 50:00",
                                    help="Par défaut : ton objectif enregistré, sinon la prédiction Garmin.")
        if pred:
            st.caption(f"{pred_source} sur {distance} : {fmt_race_time(pred)}")
    with c3:
        st.markdown("**Météo prévue** (facultatif)")
        temp_c = st.number_input("Température (°C)", min_value=-10.0, max_value=45.0, value=None,
                                 step=1.0, key="rd_temp")
        dew_c = st.number_input("Point de rosée (°C)", min_value=-20.0, max_value=35.0, value=None,
                                step=1.0, key="rd_dew", help="Donné par la plupart des applis météo. "
                                "Il mesure l'humidité : au-dessus de 15 °C, l'air devient lourd.")

# ---------------------------------------------------------------------------
# Profil et plan d'allure
# ---------------------------------------------------------------------------
track = None
if gpx_file is not None:
    try:
        track = parse_gpx(gpx_file.getvalue())
        profile = km_profile(track)
    except GpxError as e:
        st.error(str(e))
        st.stop()
    total_km = float(profile["length_m"].sum()) / 1000
else:
    total_km = DISTANCES[distance]
    profile = _flat_profile(total_km)

target_s = parse_race_time(target_text, distance if gpx_file is None else None)
if not target_s:
    st.info("Indique un temps visé (ex. 50:00 ou 1:45:00) pour calculer le plan.")
    st.stop()

heat = heat_slowdown(temp_c, dew_c)
plan = pacing_plan(profile, target_s)
fuel = fueling_plan(plan)

with st.container(key="card-rd-summary"):
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Allure moyenne", f"{fmt_pace(target_s / total_km)}/km",
              delta=f"{total_km:.2f} km en {fmt_race_time(target_s)}", delta_color="off", delta_arrow="off")
    k2.metric("Allure à plat", f"{fmt_pace(plan.attrs['flat_pace_s'])}/km",
              help="L'allure à tenir sur le plat ; elle ralentit en montée et accélère (un peu) en descente.")
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
            f"objectif réaliste ≈ {fmt_race_time(adjusted)}.") + "</div>")
    elif heat:
        html_block(f'<div style="margin-top:.6rem">{chip("Météo idéale", "good")} '
                   "Pas de correction chaleur.</div>")

fig = make_subplots(specs=[[{"secondary_y": True}]])
if track is not None:
    fig.add_trace(go.Scatter(x=track["dist"] / 1000, y=track["ele"], mode="lines", name="Altitude",
                             line=dict(color=ct.VIOLET, width=1.5), fill="tozeroy",
                             fillcolor=ct.rgba(ct.VIOLET, 0.12),
                             hovertemplate="km %{x:.1f} · %{y:.0f} m<extra></extra>"), secondary_y=True)
fig.add_trace(go.Bar(x=plan["km"] - 0.5, y=plan["pace_s"] / 60, name="Allure", width=0.85,
                     marker_color=[ct.ORANGE if g > 0.02 else ct.AQUA if g < -0.02 else ct.BLUE
                                   for g in plan["grade"]],
                     customdata=np.stack([plan["pace_s"].map(fmt_pace), plan["grade"] * 100,
                                          plan["elapsed_s"].map(fmt_race_time)], axis=1),
                     hovertemplate="km %{x:.0f} · %{customdata[0]}/km · pente %{customdata[1]:+.1f} %"
                                   "<br>passage %{customdata[2]}<extra></extra>"), secondary_y=False)
for e in fuel["events"]:
    fig.add_vline(x=e["km"], line=dict(color=ct.INK_MUTED, width=1, dash="dot"))
fig.update_yaxes(title_text="min/km", autorange="reversed", secondary_y=False)
fig.update_yaxes(title_text="altitude (m)", secondary_y=True, showgrid=False)
fig.update_layout(height=360, margin=dict(l=0, r=0, t=30, b=0), legend=dict(orientation="h", y=1.12),
                  xaxis=dict(title="km"), bargap=0.1)
st.plotly_chart(fig)
st.caption("Barres : allure par kilomètre (orange = montée, turquoise = descente). "
           "Pointillés : prises de ravitaillement.")

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
