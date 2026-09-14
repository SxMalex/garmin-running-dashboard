import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime

import chart_theme
from next_session_logic import (
    THRESHOLD_SLIDER_MAX,
    THRESHOLD_SLIDER_MIN,
    THRESHOLD_SLIDER_STEP,
    compute_pmc_series,
    cross_training_factor,
    reference_threshold_sec,
)

# Barres de TSS journalier : la valeur est déjà portée par l'axe de droite, donc
# ces deux teintes translucides sont redondantes et non porteuses d'identité.
# Slots catégoriels libres — le bleu, l'orange et l'aqua sont pris par les
# courbes CTL / ATL / TSB.
_TSS_RUN_COLOR = chart_theme.rgba(chart_theme.BLUE, 0.20)
_TSS_CROSS_COLOR = chart_theme.rgba(chart_theme.MAGENTA, 0.30)


# La PMC est rejouée à chaque mouvement du slider d'allure seuil.
# On hashe le DataFrame sur (longueur, date max) — la donnée ne change qu'au
# refresh Garmin, donc cette signature est stable et bien plus rapide qu'un
# hash complet du DataFrame.
@st.cache_data(
    ttl=3600,
    show_spinner=False,
    hash_funcs={
        pd.DataFrame: lambda df: (
            len(df),
            str(df["startTimeLocal"].max()) if len(df) else "",
        )
    },
)
def _cached_pmc_series(activities_df: pd.DataFrame, threshold_sec: int) -> pd.DataFrame:
    return compute_pmc_series(activities_df, threshold_sec)


def render(activities_df: pd.DataFrame, cutoff: datetime) -> None:
    st.subheader("Charge d'entraînement — CTL / ATL / TSB")
    st.caption(
        "Modèle fitness/fatigue (PMC) sur **toutes** tes activités · "
        "**CTL** = forme chronique (42 j) · "
        "**ATL** = fatigue aiguë (7 j) · "
        "**TSB** = fraîcheur = CTL − ATL"
    )

    # Même seuil que compute_tsb et la page Comparatif : un seul chiffre de CTL
    # dans toute l'application quand le curseur est au repos.
    auto_sec = reference_threshold_sec(activities_df)

    if "charge_threshold" not in st.session_state:
        st.session_state["charge_threshold"] = auto_sec

    col_slider, col_info = st.columns([2, 3])
    with col_slider:
        threshold_pace_sec = st.slider(
            "Allure seuil (sec/km)",
            min_value=THRESHOLD_SLIDER_MIN,
            max_value=THRESHOLD_SLIDER_MAX,
            step=THRESHOLD_SLIDER_STEP,
            key="charge_threshold",
        )
        st.caption(
            f"**{threshold_pace_sec // 60}:{threshold_pace_sec % 60:02d} /km** · "
            f"1 h à cette allure = 100 TSS  "
            f"(auto : {auto_sec // 60}:{auto_sec % 60:02d} /km)"
        )
    with col_info:
        st.info(
            "L'**allure seuil** (lactate threshold) est votre allure de course "
            "soutenable sur ~1 heure — environ votre allure semi-marathon. "
            "Elle calibre l'Intensity Factor : IF = allure_seuil / allure_moy. "
            "Les activités sans allure (wing, vélo, natation, muscu…) sont "
            "converties depuis la charge d'entraînement Garmin, recalibrée sur "
            "tes courses — le curseur fait donc bouger les deux parts ensemble."
        )

    pmc = _cached_pmc_series(activities_df, threshold_pace_sec)
    if pmc.empty:
        st.info("Pas de données d'activité disponibles.")
        return

    pmc_view = pmc[pmc["date"] >= pd.Timestamp(cutoff)].copy()

    last    = pmc.iloc[-1]
    tsb_now = last["tsb"]
    if tsb_now > 25:
        tsb_status, tsb_color = "Sous-entraîné", "off"
    elif tsb_now >= 5:
        tsb_status, tsb_color = "Forme optimale ✓", "normal"
    elif tsb_now >= -20:
        tsb_status, tsb_color = "Charge normale", "off"
    else:
        tsb_status, tsb_color = "Sur-entraîné ⚠️", "inverse"

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("CTL — Forme",    f"{last['ctl']:.1f}", help="Charge chronique sur 42 jours (fitness)")
    m2.metric("ATL — Fatigue",  f"{last['atl']:.1f}", help="Charge aiguë sur 7 jours (fatigue)")
    m3.metric("TSB — Fraîcheur", f"{tsb_now:.1f}", delta=tsb_status, delta_color=tsb_color,
              help="CTL − ATL. Identique au TSB du haut de page quand le curseur "
                   "d'allure seuil est sur sa valeur automatique.")
    _cross_today = last.get("tss_cross", 0.0)
    m4.metric(
        "TSS aujourd'hui", f"{last['tss']:.0f}",
        delta=f"dont {_cross_today:.0f} hors course" if _cross_today else None,
        delta_color="off",
        help="Training Stress Score du jour, course et autres sports confondus",
    )

    # Part du sport croisé sur la période affichée : sans ce repère, un CTL élevé
    # après une semaine de wing ressemble à une erreur de calcul.
    _view_cross = pmc_view["tss_cross"].sum()
    _view_total = pmc_view["tss"].sum()
    if _view_cross > 0:
        _k = cross_training_factor(activities_df, threshold_pace_sec)
        st.caption(
            f"Sur la période affichée, **{100 * _view_cross / _view_total:.0f} %** "
            f"de la charge vient d'activités hors course "
            f"(charge Garmin × {_k:.2f} pour la ramener sur l'échelle du TSS)."
        )

    # Le curseur ne pilote que ce bloc : sans cet avertissement, un seuil manuel
    # ferait réapparaître deux TSB différents sur la page.
    if threshold_pace_sec != auto_sec:
        st.caption(
            "⚠️ Seuil réglé manuellement — ces valeurs ne correspondent plus au "
            "TSB affiché en haut de page, qui utilise le seuil automatique "
            f"({auto_sec // 60}:{auto_sec % 60:02d} /km)."
        )

    st.divider()

    fig = go.Figure()
    tss_bars = pmc_view[pmc_view["tss"] > 0]
    fig.add_trace(go.Bar(
        x=tss_bars["date"], y=tss_bars["tss_run"], name="TSS course",
        marker_color=_TSS_RUN_COLOR, yaxis="y2",
        hovertemplate="<b>%{x|%d/%m/%Y}</b><br>TSS course : %{y:.0f}<extra></extra>",
    ))
    if tss_bars["tss_cross"].sum() > 0:
        fig.add_trace(go.Bar(
            x=tss_bars["date"], y=tss_bars["tss_cross"], name="TSS autres sports",
            marker_color=_TSS_CROSS_COLOR, yaxis="y2",
            hovertemplate="<b>%{x|%d/%m/%Y}</b><br>TSS hors course : %{y:.0f}<extra></extra>",
        ))
    fig.add_trace(go.Scatter(
        x=pmc_view["date"], y=pmc_view["tsb"].clip(lower=0),
        fill="tozeroy", fillcolor="rgba(12,163,12,0.10)",
        line=dict(width=0), showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=pmc_view["date"], y=pmc_view["tsb"].clip(upper=0),
        fill="tozeroy", fillcolor="rgba(208,59,59,0.10)",
        line=dict(width=0), showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=pmc_view["date"], y=pmc_view["ctl"],
        mode="lines", name="CTL — Forme",
        line=dict(color="#3987e5", width=2.5),
        hovertemplate="<b>%{x|%d/%m/%Y}</b><br>CTL : %{y:.1f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=pmc_view["date"], y=pmc_view["atl"],
        mode="lines", name="ATL — Fatigue",
        line=dict(color="#d95926", width=2),
        hovertemplate="<b>%{x|%d/%m/%Y}</b><br>ATL : %{y:.1f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=pmc_view["date"], y=pmc_view["tsb"],
        mode="lines", name="TSB — Fraîcheur",
        line=dict(color="#199e70", width=2, dash="dot"),
        hovertemplate="<b>%{x|%d/%m/%Y}</b><br>TSB : %{y:.1f}<extra></extra>",
    ))
    fig.add_hline(y=0, line_color="#3a3f4a", line_dash="dot")

    tss_max = pmc_view["tss"].max() if not pmc_view.empty else 100
    fig.update_layout(
        height=420,
        barmode="stack",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#c6c8ce"),
        xaxis=dict(gridcolor="#232833"),
        yaxis=dict(
            gridcolor="#232833", title="CTL / ATL / TSB",
            zeroline=True, zerolinecolor="#3a3f4a",
        ),
        yaxis2=dict(
            title="TSS journalier", overlaying="y", side="right",
            showgrid=False, range=[0, max(tss_max * 4, 100)],
            tickfont=dict(color="rgba(57,135,229,0.5)"),
            titlefont=dict(color="rgba(57,135,229,0.5)"),
        ),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=0, r=60, t=40, b=0),
        hovermode="x unified",
    )
    st.plotly_chart(fig)

    st.markdown("#### Interprétation du TSB")
    iz1, iz2, iz3, iz4 = st.columns(4)
    iz1.info("**TSB > 25**\nTrop frais\nSous-entraîné")
    iz2.success("**TSB 5 → 25**\nForme optimale\nIdéal compétition")
    iz3.warning("**TSB −20 → 5**\nCharge normale\nPhase d'entraînement")
    iz4.error("**TSB < −20**\nSur-entraîné\nRécupération requise")
