import numpy as np
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

from stats_tabs._shared import add_trend_line

import chart_theme as ct


def render(running_filtered: pd.DataFrame, client, hr_zones: list) -> None:
    st.subheader("Analyse de la fréquence cardiaque")

    hr_data = running_filtered.dropna(subset=["avgHR"]).copy()
    hr_data = hr_data.sort_values("startTimeLocal")

    if hr_data.empty:
        st.info("Pas de données de fréquence cardiaque disponibles.")
        return

    col_left, col_right = st.columns([3, 2])

    with col_left:
        st.markdown("#### Évolution FC dans le temps")
        fig_hr = go.Figure()
        fig_hr.add_trace(go.Scatter(
            x=hr_data["startTimeLocal"],
            y=hr_data["avgHR"],
            mode="markers+lines",
            name="FC moyenne",
            line=dict(color=ct.rgba(ct.HR, 0.5), width=1),
            marker=dict(color=ct.rgba(ct.HR, 0.9), size=6),
            hovertemplate="<b>%{x|%d/%m/%Y}</b><br>FC : %{y:.0f} bpm<extra></extra>",
        ))
        if hr_data["maxHR"].notna().any():
            max_hr_data = hr_data.dropna(subset=["maxHR"])
            fig_hr.add_trace(go.Scatter(
                x=max_hr_data["startTimeLocal"],
                y=max_hr_data["maxHR"],
                mode="markers",
                name="FC max",
                marker=dict(color=ct.rgba(ct.HR_MAX, 0.75), size=5, symbol="triangle-up"),
                hovertemplate="<b>%{x|%d/%m/%Y}</b><br>FC max : %{y:.0f} bpm<extra></extra>",
            ))
        fig_hr, _ = add_trend_line(
            fig_hr, hr_data["startTimeLocal"], hr_data["avgHR"], ascending_better=False
        )
        fig_hr.update_layout(
            height=350,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(),
            yaxis=dict(title="bpm"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            margin=dict(l=0, r=0, t=30, b=0),
        )
        st.plotly_chart(fig_hr)

    with col_right:
        st.markdown("#### Distribution des zones FC")
        hr_zones_df = client.get_hr_zones(running_filtered, hr_zones=hr_zones)
        if not hr_zones_df.empty and hr_zones_df["nb_activites"].sum() > 0:
            fig_zones = go.Figure(go.Pie(
                labels=hr_zones_df["zone"],
                values=hr_zones_df["nb_activites"],
                hole=0.4,
                # ZONE_RAMP — rampe ordinale bleue validée (Z1 clair → Z5 foncé)
                marker=dict(colors=ct.ZONE_RAMP),
                textinfo="label+percent",
                textfont=dict(size=11),
            ))
            fig_zones.update_layout(
                height=350,
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                legend=dict(orientation="v", font=dict(size=10)),
                margin=dict(l=0, r=0, t=10, b=0),
                showlegend=True,
            )
            st.plotly_chart(fig_zones)
            st.caption("Distribution estimée d'après la FC moyenne par activité.")
        else:
            st.info("Pas assez de données FC pour les zones.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("FC moy globale",   f"{hr_data['avgHR'].mean():.0f} bpm")
    c2.metric("FC moy minimale",  f"{hr_data['avgHR'].min():.0f} bpm")
    c3.metric("FC moy maximale",  f"{hr_data['avgHR'].max():.0f} bpm")
    if len(hr_data) >= 5:
        z_hr = np.polyfit(range(len(hr_data)), hr_data["avgHR"], 1)
        hr_trend_txt = "Baisse 📈" if z_hr[0] < 0 else "Hausse 📉"
    else:
        hr_trend_txt = "N/A"
    c4.metric("Tendance FC", hr_trend_txt)

    st.markdown("#### Corrélation FC / Allure")
    corr_data = running_filtered.dropna(subset=["avgHR"]).copy()
    corr_data = corr_data[corr_data["avgPace_sec"] > 0]
    if len(corr_data) >= 5:
        fig_corr = px.scatter(
            corr_data,
            x="avgPace_sec",
            y="avgHR",
            color="distance_km",
            color_continuous_scale=[ct.ZONE_RAMP[0], ct.ZONE_RAMP[2], ct.SEQ_COLORSCALE[-1][1]],
            labels={
                "avgPace_sec": "Allure (sec/km)",
                "avgHR": "FC moyenne (bpm)",
                "distance_km": "Distance (km)",
            },
            hover_data={"avgPace": True, "startTimeLocal": "|%d/%m/%Y"},
        )
        fig_corr.update_traces(marker=dict(size=8))
        fig_corr.update_layout(
            height=300,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(
                title="Allure (sec/km) — valeur élevée = lent",
            ),
            yaxis=dict(),
            coloraxis_colorbar=dict(tickfont=dict()),
            margin=dict(l=0, r=0, t=10, b=0),
        )
        st.plotly_chart(fig_corr)
        corr_val = corr_data["avgPace_sec"].corr(corr_data["avgHR"])
        st.caption(f"Corrélation allure/FC : {corr_val:.2f} (proche de 1 = FC monte avec le pace)")
