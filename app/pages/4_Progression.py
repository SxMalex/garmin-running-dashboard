"""
Page Progression — prédictions de course (natives + Riegel) et VO2max, avec
leur projection « si tu continues comme ça » (forecast_logic), records
personnels et efficacité aérobie. Répond à « est-ce que je progresse ? ».
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from forecast_logic import race_projection, trend_word, vo2max_projection
from running_form_logic import form_report
from ui_theme import chip, esc, html_block
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
    cache_nonce,
    cached_load_activities,
    get_athlete_id,
    get_garmin_client,
    render_garmin_attribution,
    require_login,
)

import chart_theme as ct

st.set_page_config(
    page_title="Progression — Running Dashboard",
    page_icon="📈",
    layout="wide",
)

require_login()
render_physio_settings()

_athlete_id = get_athlete_id()
FORECAST_LOOKBACK_DAYS = 180  # historique chargé pour projeter, même si l'affichage est plus court


# ---------------------------------------------------------------------------
# Chargement (caché)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=86400, show_spinner=False)
def load_records(athlete_id: int, nonce: int) -> list:
    return get_garmin_client().get_personal_records()


@st.cache_data(ttl=86400, show_spinner=False)
def load_predictions(athlete_id: int, nonce: int) -> dict:
    return get_garmin_client().get_race_predictions()


@st.cache_data(ttl=86400, show_spinner="Chargement de l'historique des prédictions…")
def load_predictions_history(athlete_id: int, start: str, end: str, nonce: int) -> list:
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

st.title("Tendances")
st.caption("Records personnels, prédictions de course et évolution du VO2max.")

df, error = cached_load_activities(_athlete_id)
if error:
    st.error(f"Erreur Garmin : {error}")
    st.stop()
running_df = df[df["activityType"] == "running"].copy() if not df.empty else pd.DataFrame()

# ---------------------------------------------------------------------------
# Prédictions de course — natives Garmin + Riegel, et projection 30 / 90 jours
# ---------------------------------------------------------------------------
st.subheader("Prédictions de course")

predictions = load_predictions(_athlete_id, cache_nonce())
riegel = riegel_estimates(running_df)

# La projection lit les 8 dernières semaines (et le niveau des 7 derniers jours) ;
# on charge 6 mois pour que la vue « 180 jours » reste possible (max endpoint : 365 j).
fetch_days = max(history_days, FORECAST_LOOKBACK_DAYS)
hist_raw = load_predictions_history(_athlete_id, (date.today() - timedelta(days=fetch_days)).isoformat(),
                                    date.today().isoformat(), cache_nonce())
hist_df = predictions_history_df(hist_raw)
projections = {label: race_projection(hist_df, label) for label, _km, _k in RACE_TARGETS}

if predictions or riegel:
    with st.container(key="card-pred-kpi"):
        cols = st.columns(4)
        for col, (label, dist_km, garmin_key) in zip(cols, RACE_TARGETS):
            now_sec = predictions.get(garmin_key) or riegel.get(label)
            if not now_sec:
                continue
            proj = projections.get(label)
            p90 = proj["projections"][-1] if proj else None
            col.metric(
                label, fmt_race_time(now_sec),
                delta=(f"≈ {fmt_race_time(p90.value)} dans 90 j" if p90 else fmt_race_pace(now_sec, dist_km)),
                delta_color="off", delta_arrow="off",
                help=("Estimation si tu continues comme ces 8 dernières semaines — "
                      f"fourchette {fmt_race_time(p90.low)} – {fmt_race_time(p90.high)}." if p90
                      else "Pas assez d'historique pour une projection."),
            )
            col.caption(f"{fmt_race_pace(now_sec, dist_km)}"
                        + ("" if predictions.get(garmin_key) else " · estimation Riegel"))
else:
    st.info("Pas de prédictions disponibles.")

if not hist_df.empty:
    view = st.segmented_control(
        "Vue", ["separes", "ensemble"], key="pred_view", required=True, default="separes",
        format_func={"separes": "4 graphes", "ensemble": "Superposé (% de progression)"}.get,
        label_visibility="collapsed")
    shown_from = pd.Timestamp(date.today() - timedelta(days=history_days))
    colors = dict(zip([label for label, _km, _k in RACE_TARGETS], ct.CAT))

    def _forecast_x(proj):
        return [proj["last_date"] + pd.Timedelta(days=p.days) for p in proj["projections"]]

    if view == "separes":
        fig = make_subplots(rows=2, cols=2, shared_xaxes=True, vertical_spacing=0.12,
                            horizontal_spacing=0.08,
                            subplot_titles=[label for label, _km, _k in RACE_TARGETS])
        for n, (label, _km, _k) in enumerate(RACE_TARGETS):
            row, col = n // 2 + 1, n % 2 + 1
            serie = hist_df[(hist_df["distance"] == label) & (hist_df["date"] >= shown_from)].sort_values("date")
            if serie.empty:
                continue
            fig.add_trace(go.Scatter(
                x=serie["date"], y=serie["time_sec"] / 60, mode="lines", name=label,
                line=dict(color=colors[label], width=2),
                customdata=serie["time_sec"].map(fmt_race_time),
                hovertemplate="%{x|%d/%m/%Y} · %{customdata}<extra>" + label + "</extra>"),
                row=row, col=col)
            proj = projections.get(label)
            if proj:
                xs = [proj["last_date"], *_forecast_x(proj)]
                mid = [proj["last_value"] / 60, *[p.value / 60 for p in proj["projections"]]]
                lo = [proj["last_value"] / 60, *[p.low / 60 for p in proj["projections"]]]
                hi = [proj["last_value"] / 60, *[p.high / 60 for p in proj["projections"]]]
                fig.add_trace(go.Scatter(x=xs + xs[::-1], y=hi + lo[::-1], fill="toself", mode="lines",
                                         fillcolor=ct.rgba(colors[label], 0.12), line=dict(width=0),
                                         hoverinfo="skip", showlegend=False), row=row, col=col)
                fig.add_trace(go.Scatter(
                    x=xs, y=mid, mode="lines+markers", line=dict(color=colors[label], width=2, dash="dot"),
                    marker=dict(size=[0, *[8] * len(proj["projections"])]), showlegend=False,
                    customdata=[fmt_race_time(proj["last_value"]),
                                *[f"{fmt_race_time(p.value)} (entre {fmt_race_time(p.low)} et "
                                  f"{fmt_race_time(p.high)})" for p in proj["projections"]]],
                    hovertemplate="Estimation au %{x|%d/%m} · %{customdata}<extra></extra>"),
                    row=row, col=col)
            fig.update_yaxes(autorange="reversed", title_text="min", row=row, col=col)
        fig.update_layout(height=560, showlegend=False, margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig)
    else:
        fig = go.Figure()
        for label, _km, _k in RACE_TARGETS:
            serie = hist_df[(hist_df["distance"] == label) & (hist_df["date"] >= shown_from)].sort_values("date")
            if serie.empty:
                continue
            ref = float(serie["time_sec"].iloc[0])
            # Gain en % : positif = plus rapide qu'au début de la période
            fig.add_trace(go.Scatter(
                x=serie["date"], y=(ref - serie["time_sec"]) / ref * 100, mode="lines", name=label,
                line=dict(color=colors[label], width=2),
                customdata=serie["time_sec"].map(fmt_race_time),
                hovertemplate="%{x|%d/%m/%Y} · %{customdata} (%{y:+.1f} %)<extra>" + label + "</extra>"))
            proj = projections.get(label)
            if proj:
                xs = [proj["last_date"], *_forecast_x(proj)]
                ys = [(ref - proj["last_value"]) / ref * 100,
                      *[(ref - p.value) / ref * 100 for p in proj["projections"]]]
                fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines+markers", showlegend=False,
                                         line=dict(color=colors[label], width=2, dash="dot"),
                                         marker=dict(size=[0, *[7] * len(proj["projections"])]),
                                         hovertemplate="Estimation au %{x|%d/%m} · %{y:+.1f} %<extra>"
                                                       + label + "</extra>"))
        fig.add_hline(y=0, line=dict(color=ct.BASELINE, width=1))
        fig.update_layout(height=380, yaxis=dict(title="Progression (%)", ticksuffix=" %"),
                          legend=dict(orientation="h", y=1.1), margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig)
    st.caption("Traits pleins : prédictions Garmin. **Pointillés : estimation à 30 et 90 jours si tu "
               "continues à t'entraîner comme ces 8 dernières semaines** (tendance robuste, gains amortis, "
               "plafonnée à 2 %/mois) ; la bande montre l'incertitude. Axe inversé en vue séparée : "
               "plus bas = plus rapide.")
    explain("forecast")

st.divider()

# ---------------------------------------------------------------------------
# VO2max — évolution et projection 1 à 6 mois
# ---------------------------------------------------------------------------
st.subheader("VO2max")

if not running_df.empty and "vo2max" in running_df.columns:
    vo2 = running_df.dropna(subset=["vo2max"]).sort_values("startTimeLocal")
else:
    vo2 = pd.DataFrame()

if not vo2.empty:
    vproj = vo2max_projection(df)
    v1, v2 = st.columns([1, 3], gap="large")
    current = vo2.iloc[-1]["vo2max"]
    best = vo2["vo2max"].max()
    with v1, st.container(key="card-vo2-kpi"):
        st.metric("Actuelle", f"{current:.0f}", delta=f"record {best:.0f}", delta_color="off", delta_arrow="off")
        if vproj:
            by_days = {p.days: p for p in vproj["projections"]}
            for months in (1, 3, 6):
                p = by_days[months * 30]
                st.metric(f"Dans {months} mois (estimation)", f"≈ {p.value:.0f}",
                          delta=f"entre {p.low:.0f} et {p.high:.0f}", delta_color="off", delta_arrow="off")
            st.caption("Tendance " + trend_word(vproj["slope_per_30d"], lower_is_better=False, tolerance=0.2)
                       + f" ({vproj['slope_per_30d']:+.1f} / mois sur {vproj['basis_days']} jours).")
        else:
            st.caption("Pas assez de mesures récentes (8 sur 4 semaines) pour une projection.")
    with v2:
        fig_vo2 = go.Figure(go.Scatter(
            x=vo2["startTimeLocal"], y=vo2["vo2max"], mode="lines+markers", name="VO2max Garmin",
            line=dict(color=ct.MAGENTA, width=2), marker=dict(size=6),
            hovertemplate="%{x|%d/%m/%Y} · VO2max %{y:.0f}<extra></extra>",
        ))
        if vproj:
            xs = [vproj["last_date"], *[vproj["last_date"] + pd.Timedelta(days=p.days)
                                         for p in vproj["projections"]]]
            mid = [vproj["last_value"], *[p.value for p in vproj["projections"]]]
            lo = [vproj["last_value"], *[p.low for p in vproj["projections"]]]
            hi = [vproj["last_value"], *[p.high for p in vproj["projections"]]]
            fig_vo2.add_trace(go.Scatter(x=xs + xs[::-1], y=hi + lo[::-1], fill="toself", mode="lines",
                                         fillcolor=ct.rgba(ct.MAGENTA, 0.12), line=dict(width=0),
                                         hoverinfo="skip", showlegend=False))
            fig_vo2.add_trace(go.Scatter(
                x=xs, y=mid, mode="lines+markers", name="Estimation 1-6 mois",
                line=dict(color=ct.MAGENTA, width=2, dash="dot"),
                marker=dict(size=[0, *[7] * len(vproj["projections"])]),
                customdata=[f"{m} mois" for m in range(0, 7)],
                hovertemplate="Dans %{customdata} · ≈ %{y:.1f}<extra>estimation</extra>"))
        fig_vo2.update_layout(height=340, yaxis=dict(title="VO2max"), legend=dict(orientation="h", y=1.1),
                              margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig_vo2)
    st.caption(
        f"Estimation Garmin portée par chaque sortie ({len(vo2)} mesures). Pointillés : projection si tu "
        "continues au même rythme (plafonnée à ±1 point par mois, gains amortis) — une indication, "
        "pas une promesse : une coupure ou un nouveau bloc la changent.")
    explain("vo2max")
else:
    st.info("Pas de VO2max sur les activités chargées.")

st.divider()

# ---------------------------------------------------------------------------
# Forme de foulée à allure égale
# ---------------------------------------------------------------------------
st.subheader("Forme de foulée, à allure égale")
form = form_report(df)
if not form:
    st.info("Pas assez de sorties avec dynamique de course (contact au sol, foulée, puissance) sur "
            "18 semaines : il en faut 5 dans les 6 dernières semaines et 5 dans les 12 d'avant. "
            "Toutes les montres ne la mesurent pas.")
else:
    _WORD = {"warning": ("à surveiller", "warning"), "good": ("mieux", "good"), "neutral": ("stable", "neutral")}
    with st.container(key="card-form"):
        cols = st.columns(len(form))
        for col, r in zip(cols, form):
            word, status = _WORD[r["status"]]
            with col:
                html_block(f'<div class="gd-kicker">{esc(r["label"])}</div>'
                           f'<div><span class="gd-big" style="font-size:2.4rem">{r["delta"]:+.1f}</span> '
                           f'{esc(r["unit"])} {chip(word, status)}</div>')
                st.caption(("Attention : " + r["meaning"] + ".") if r["status"] == "warning"
                           else f"{r['recent_n']} sorties récentes vs {r['ref_n']} avant.")
        pick = st.segmented_control("Métrique", [r["column"] for r in form], key="form_metric",
                                    format_func={r["column"]: r["label"] for r in form}.get,
                                    required=True, default=form[0]["column"],
                                    label_visibility="collapsed")
        r = next(x for x in form if x["column"] == pick)
        series = r["series"].sort_values("t")
        split = pd.Timestamp(date.today()) - pd.Timedelta(weeks=6)
        fig_f = go.Figure()
        fig_f.add_vrect(x0=split, x1=pd.Timestamp(date.today()), fillcolor=ct.rgba(ct.BLUE, 0.07),
                        line_width=0, annotation_text="6 dernières semaines",
                        annotation_position="top left", annotation_font=dict(color=ct.INK_MUTED, size=11))
        fig_f.add_trace(go.Scatter(x=series["t"], y=series["resid"], mode="markers", name="Sortie",
                                   marker=dict(color=ct.BLUE, size=8, opacity=0.8),
                                   customdata=np.stack([series["y"], series["speed"] * 3.6], axis=1),
                                   hovertemplate="%{x|%d/%m} · %{customdata[0]:.1f} " + r["unit"]
                                                 + " à %{customdata[1]:.1f} km/h<br>écart à allure égale "
                                                 "%{y:+.1f}<extra></extra>"))
        trend_f = series.set_index("t")["resid"].rolling("28D", min_periods=3).median()
        fig_f.add_trace(go.Scatter(x=trend_f.index, y=trend_f.to_numpy(), mode="lines", name="Tendance 4 sem.",
                                   line=dict(color=ct.INK_SECONDARY, width=2, dash="dot"), hoverinfo="skip"))
        fig_f.add_hline(y=0, line=dict(color=ct.BASELINE, width=1))
        fig_f.update_layout(height=280, margin=dict(l=0, r=0, t=30, b=0), showlegend=False,
                            yaxis=dict(title=f"écart ({r['unit']}) à allure égale"))
        st.plotly_chart(fig_f)
        st.caption("Chaque point : la sortie comparée à ce que tu produis habituellement à la même "
                   "vitesse. Au-dessus de zéro = plus que d'habitude.")
explain("foulee")

st.divider()

# ---------------------------------------------------------------------------
# Records personnels
# ---------------------------------------------------------------------------
st.subheader("🏅 Records personnels")

records = parse_personal_records(load_records(_athlete_id, cache_nonce()))

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
                          delta=pr["date_str"] or None, delta_color="off", delta_arrow="off")
                if pr["activity_name"]:
                    st.caption(pr["activity_name"])

# En fin de page : peut demander jusqu'à DECOUPLING_TREND_MAX_RUNS appels API
# au premier chargement, sans retarder les records et prédictions.
st.divider()
render_aerobic_progress(df, _athlete_id)

render_garmin_attribution()
