import numpy as np
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from stats_tabs._shared import add_trend_line

import chart_theme as ct

_CADENCE_LOW  = 170
_CADENCE_HIGH = 180


def render(running_filtered: pd.DataFrame) -> None:
    st.subheader("Évolution de la cadence de course")

    cad_data = running_filtered.dropna(subset=["avgCadence"]).copy()
    cad_data = cad_data[cad_data["avgCadence"] > 0].sort_values("startTimeLocal")

    if cad_data.empty:
        st.info("Pas de données de cadence disponibles.")
        return

    fig_cad = go.Figure()
    fig_cad.add_hrect(
        y0=_CADENCE_LOW, y1=_CADENCE_HIGH,
        fillcolor=ct.rgba(ct.GOOD, 0.08),
        line_width=0,
        annotation_text="Zone optimale",
        annotation_position="top right",
        annotation_font=dict(color=ct.GOOD, size=11),
    )
    fig_cad.add_trace(go.Scatter(
        x=cad_data["startTimeLocal"],
        y=cad_data["avgCadence"],
        mode="markers+lines",
        name="Cadence",
        line=dict(color=ct.rgba(ct.CADENCE, 0.5), width=1),
        marker=dict(
            color=cad_data["avgCadence"].apply(
                lambda c: ct.GOOD if _CADENCE_LOW <= c <= _CADENCE_HIGH
                else (ct.SERIOUS if c < _CADENCE_LOW else ct.CRITICAL)
            ),
            size=7,
        ),
        hovertemplate="<b>%{x|%d/%m/%Y}</b><br>Cadence : %{y:.0f} spm<extra></extra>",
    ))
    fig_cad, _ = add_trend_line(
        fig_cad, cad_data["startTimeLocal"], cad_data["avgCadence"], ascending_better=True
    )
    fig_cad.update_layout(
        height=380,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(),
        yaxis=dict(title="Cadence (spm)"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=0, r=0, t=30, b=0),
    )
    st.plotly_chart(fig_cad)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cadence moyenne", f"{cad_data['avgCadence'].mean():.0f} spm")
    c2.metric("Cadence max",     f"{cad_data['avgCadence'].max():.0f} spm")
    pct_optimal = (
        ((cad_data["avgCadence"] >= _CADENCE_LOW) & (cad_data["avgCadence"] <= _CADENCE_HIGH)).sum()
        / len(cad_data) * 100
    )
    c3.metric("Dans la zone optimale", f"{pct_optimal:.0f}%")
    if len(cad_data) >= 5:
        z_cad = np.polyfit(range(len(cad_data)), cad_data["avgCadence"], 1)
        cad_trend_txt = "Amélioration 📈" if z_cad[0] > 0 else "Baisse 📉"
    else:
        cad_trend_txt = "N/A"
    c4.metric("Tendance", cad_trend_txt)

    st.info(
        "💡 Une cadence optimale se situe entre **170 et 180 spm** pour la plupart des coureurs. "
        "Une cadence élevée réduit l'impact au sol et diminue le risque de blessure."
    )

    st.markdown("#### Distribution de la cadence")
    fig_cad_hist = go.Figure(go.Histogram(
        x=cad_data["avgCadence"],
        nbinsx=20,
        marker_color=ct.rgba(ct.CADENCE, 0.85),
        hovertemplate="Cadence : %{x:.0f} spm<br>Sorties : %{y}<extra></extra>",
    ))
    for x_val, label in [(_CADENCE_LOW, "170 spm"), (_CADENCE_HIGH, "180 spm")]:
        fig_cad_hist.add_vline(
            x=x_val,
            line_color=ct.rgba(ct.GOOD, 0.7),
            line_dash="dash",
            annotation_text=label,
            annotation_font=dict(color=ct.GOOD),
        )
    fig_cad_hist.update_layout(
        height=280,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(title="Cadence (spm)"),
        yaxis=dict(title="Nombre de sorties"),
        margin=dict(l=0, r=0, t=10, b=0),
        showlegend=False,
    )
    st.plotly_chart(fig_cad_hist)
