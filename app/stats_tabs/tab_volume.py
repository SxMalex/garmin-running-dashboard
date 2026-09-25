import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from stats_tabs._shared import WORKOUT_COLORS

import chart_theme as ct


def render(running_filtered: pd.DataFrame, client) -> None:
    st.subheader("Volume hebdomadaire et mensuel")

    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown("#### Par semaine")
        weekly = client.get_weekly_stats(running_filtered)

        if not weekly.empty:
            weekly["week_label"] = weekly["week"].dt.strftime("S%V %d/%m")

            fig_w = go.Figure()
            fig_w.add_trace(go.Bar(
                x=weekly["week_label"],
                y=weekly["km_total"],
                name="km/semaine",
                marker_color="rgba(57,135,229,0.85)",
                hovertemplate="<b>%{x}</b><br>%{y:.1f} km<br>%{customdata} sorties<extra></extra>",
                customdata=weekly["nb_sorties"],
            ))

            if len(weekly) >= 4:
                weekly["rolling_avg"] = weekly["km_total"].rolling(4, min_periods=2).mean()
                fig_w.add_trace(go.Scatter(
                    x=weekly["week_label"],
                    y=weekly["rolling_avg"],
                    mode="lines",
                    name="Moy. mobile (4 sem.)",
                    line=dict(color="rgba(201,133,0,0.95)", width=2),
                ))

            fig_w.update_layout(
                height=350,
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(tickangle=-45),
                yaxis=dict(title="km"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
                margin=dict(l=0, r=0, t=30, b=60),
                hovermode="x unified",
            )
            st.plotly_chart(fig_w)

            c1, c2, c3 = st.columns(3)
            c1.metric("Semaine max", f"{weekly['km_total'].max():.1f} km")
            c2.metric("Semaine moy.", f"{weekly['km_total'].mean():.1f} km")
            c3.metric("Semaines actives", str(len(weekly)))
        else:
            st.info("Pas assez de données.")

    with col_right:
        st.markdown("#### Par mois")
        monthly = client.get_monthly_stats(running_filtered)

        if not monthly.empty:
            fig_m = go.Figure()
            fig_m.add_trace(go.Bar(
                x=monthly["month_label"],
                y=monthly["km_total"],
                name="km/mois",
                marker_color=[
                    f"rgba({int(100 + 155 * i / max(len(monthly)-1, 1))}, 156, 252, 0.8)"
                    for i in range(len(monthly))
                ],
                hovertemplate="<b>%{x}</b><br>%{y:.1f} km<br>%{customdata} sorties<extra></extra>",
                customdata=monthly["nb_sorties"],
                text=monthly["km_total"].map("{:.0f}".format),
                textposition="outside",
            ))
            fig_m.update_layout(
                height=350,
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(tickangle=-30),
                yaxis=dict(title="km"),
                margin=dict(l=0, r=0, t=30, b=60),
                showlegend=False,
                hovermode="x unified",
            )
            st.plotly_chart(fig_m)

            c1, c2, c3 = st.columns(3)
            c1.metric("Mois record", f"{monthly['km_total'].max():.1f} km")
            c2.metric("Mois moyen", f"{monthly['km_total'].mean():.1f} km")
            c3.metric("Total période", f"{monthly['km_total'].sum():.0f} km")
        else:
            st.info("Pas assez de données.")

    st.markdown("#### Distribution des distances")
    fig_hist = px.histogram(
        running_filtered,
        x="distance_km",
        nbins=20,
        labels={"distance_km": "Distance (km)", "count": "Nombre de sorties"},
        color_discrete_sequence=["rgba(57,135,229,0.85)"],
    )
    fig_hist.update_layout(
        height=280,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(),
        yaxis=dict(title="Sorties"),
        margin=dict(l=0, r=0, t=10, b=0),
        showlegend=False,
    )
    st.plotly_chart(fig_hist)

    st.markdown("#### Répartition par type de sortie")
    type_stats = running_filtered.groupby("workoutLabel").agg(
        km=("distance_km", "sum"),
        nb=("activityId", "count"),
    ).reset_index().sort_values("km", ascending=False)

    fig_types = go.Figure(go.Bar(
        x=type_stats["workoutLabel"],
        y=type_stats["km"],
        marker_color=[WORKOUT_COLORS.get(t, ct.BLUE) for t in type_stats["workoutLabel"]],
        text=type_stats["km"].map("{:.0f} km".format),
        textposition="outside",
        hovertemplate="<b>%{x}</b><br>%{y:.0f} km · %{customdata} sorties<extra></extra>",
        customdata=type_stats["nb"],
    ))
    fig_types.update_layout(
        height=260,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(),
        yaxis=dict(title="km"),
        margin=dict(l=0, r=0, t=30, b=0),
        showlegend=False,
    )
    st.plotly_chart(fig_types)
