import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from formatting import seconds_to_pace_str
from stats_tabs._shared import WORKOUT_COLORS, add_trend_line
from ui_helpers import get_athlete_id, get_garmin_client


@st.cache_data(ttl=3600, show_spinner="Chargement des splits km par km...")
def _load_splits_data(athlete_id: int, activity_ids: tuple[int, ...]) -> pd.DataFrame:
    return get_garmin_client().get_splits_aggregate(list(activity_ids))


def render(running_filtered: pd.DataFrame) -> None:
    st.subheader("Évolution de l'allure dans le temps")

    pace_data = running_filtered[running_filtered["avgPace_sec"] > 0].copy()
    pace_data = pace_data.sort_values("startTimeLocal")
    pace_data["pace_min"] = pace_data["avgPace_sec"] / 60

    if pace_data.empty:
        st.info("Pas de données d'allure disponibles.")
        return

    fig_pace = go.Figure()
    fig_pace.add_trace(go.Scatter(
        x=pace_data["startTimeLocal"],
        y=pace_data["pace_min"],
        mode="markers+lines",
        name="Allure",
        marker=dict(
            color=pace_data["distance_km"],
            colorscale=[[0.0, "#9ec5f4"], [0.5, "#3987e5"], [1.0, "#104281"]],
            size=8,
            colorbar=dict(title="Distance (km)", tickfont=dict(color="#c6c8ce")),
            showscale=True,
        ),
        hovertemplate=(
            "<b>%{x|%d/%m/%Y}</b><br>"
            "Allure : %{customdata[0]}<br>"
            "Distance : %{customdata[1]:.1f} km<extra></extra>"
        ),
        customdata=list(zip(pace_data["avgPace"], pace_data["distance_km"])),
    ))

    fig_pace, pace_slope = add_trend_line(
        fig_pace, pace_data["startTimeLocal"], pace_data["pace_min"], ascending_better=False
    )

    y_min = pace_data["pace_min"].min()
    y_max = pace_data["pace_min"].max()
    padding = (y_max - y_min) * 0.1 if y_max > y_min else 0.5

    fig_pace.update_layout(
        height=400,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#c6c8ce"),
        xaxis=dict(gridcolor="#232833", title="Date"),
        yaxis=dict(
            gridcolor="#232833",
            title="Allure (min/km)",
            range=[y_max + padding, y_min - padding],
            tickformat=".1f",
        ),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=0, r=0, t=30, b=0),
        hovermode="closest",
    )
    st.plotly_chart(fig_pace)
    st.caption("⬇️ L'axe Y est inversé : une allure plus basse indique une vitesse plus élevée.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Meilleure allure", seconds_to_pace_str(pace_data["avgPace_sec"].min()))
    c2.metric("Allure moyenne", seconds_to_pace_str(pace_data["avgPace_sec"].mean()))
    c3.metric("Allure médiane", seconds_to_pace_str(float(pace_data["avgPace_sec"].median())))
    c4.metric(
        "Tendance",
        ("Amélioration 📈" if pace_slope < 0 else "Ralentissement 📉")
        if pace_slope is not None else "N/A",
    )

    st.markdown("#### Allure moyenne par tranche de distance")
    bins = [0, 5, 10, 15, 21.1, 30, 42.2, 999]
    labels_dist = ["< 5 km", "5-10 km", "10-15 km", "15-21 km", "Semi-marathon", "30-42 km", "Marathon+"]
    pace_data["distance_bin"] = pd.cut(
        pace_data["distance_km"], bins=bins, labels=labels_dist, right=False
    )
    pace_by_dist = (
        pace_data.groupby("distance_bin", observed=True)["avgPace_sec"]
        .mean()
        .reset_index()
    )
    pace_by_dist["allure"] = pace_by_dist["avgPace_sec"].apply(seconds_to_pace_str)
    pace_by_dist["pace_min"] = pace_by_dist["avgPace_sec"] / 60

    fig_pace_dist = go.Figure(go.Bar(
        x=pace_by_dist["distance_bin"].astype(str),
        y=pace_by_dist["pace_min"],
        text=pace_by_dist["allure"],
        textposition="outside",
        marker_color="rgba(57,135,229,0.85)",
    ))
    fig_pace_dist.update_layout(
        height=300,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#c6c8ce"),
        xaxis=dict(gridcolor="#232833"),
        yaxis=dict(
            gridcolor="#232833",
            title="Allure (min/km)",
            tickformat=".1f",
        ),
        margin=dict(l=0, r=0, t=30, b=0),
        showlegend=False,
    )
    st.plotly_chart(fig_pace_dist)

    types_present = running_filtered["workoutLabel"].unique()
    if len(types_present) > 1:
        st.markdown("#### Allure par type de sortie")
        type_pace = pace_data.copy()
        fig_box = go.Figure()
        for wtype in sorted(types_present):
            subset = type_pace[type_pace["workoutLabel"] == wtype]["pace_min"]
            if subset.empty:
                continue
            fig_box.add_trace(go.Box(
                y=subset,
                name=wtype,
                marker_color=WORKOUT_COLORS.get(wtype, "#3987e5"),
                boxmean=True,
                hovertemplate=f"<b>{wtype}</b><br>%{{y:.2f}} min/km<extra></extra>",
            ))
        y_vals = type_pace["pace_min"]
        y_slow = y_vals.quantile(0.98) * 1.05
        y_fast = y_vals.quantile(0.02) * 0.95
        fig_box.update_layout(
            height=300,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#c6c8ce"),
            yaxis=dict(
                gridcolor="#232833",
                title="Allure (min/km)",
                range=[max(y_slow, y_fast), min(y_slow, y_fast)],
                tickformat=".1f",
            ),
            margin=dict(l=0, r=0, t=10, b=0),
        )
        st.plotly_chart(fig_box)

    st.markdown("#### Analyse des splits par kilomètre")
    st.caption("Évolution de l'allure km par km sur les 10 dernières sorties de la période.")
    recent_ids = tuple(
        running_filtered.sort_values("startTimeLocal", ascending=False)
        .head(10)["activityId"]
        .astype(int)
        .tolist()
    )
    if recent_ids:
        athlete_id = get_athlete_id()
        splits_df = _load_splits_data(athlete_id, recent_ids)
        if not splits_df.empty:
            splits_clean = splits_df[
                (splits_df["split"] <= 25)
                & (splits_df["pace_sec"] > 180)
                & (splits_df["pace_sec"] < 600)
            ].copy()

            if not splits_clean.empty:
                avg_splits = splits_clean.groupby("split")["pace_min"].mean().reset_index()
                avg_splits["pace_str"] = avg_splits["pace_min"].apply(
                    lambda p: f"{int(p)}:{int((p % 1) * 60):02d}/km"
                )

                fig_splits = go.Figure()
                for aid in splits_clean["activityId"].unique():
                    act_s = splits_clean[splits_clean["activityId"] == aid].sort_values("split")
                    fig_splits.add_trace(go.Scatter(
                        x=act_s["split"],
                        y=act_s["pace_min"],
                        mode="lines",
                        line=dict(color="rgba(57,135,229,0.15)", width=1),
                        showlegend=False,
                        hoverinfo="skip",
                    ))
                fig_splits.add_trace(go.Scatter(
                    x=avg_splits["split"],
                    y=avg_splits["pace_min"],
                    mode="lines+markers",
                    name="Allure moyenne",
                    line=dict(color="rgba(201,133,0,0.95)", width=2.5),
                    marker=dict(size=6),
                    customdata=avg_splits["pace_str"],
                    hovertemplate="<b>Km %{x}</b><br>%{customdata}<extra></extra>",
                ))

                q05 = splits_clean["pace_min"].quantile(0.05)
                q95 = splits_clean["pace_min"].quantile(0.95)
                pad = (q95 - q05) * 0.15
                fig_splits.update_layout(
                    height=350,
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#c6c8ce"),
                    xaxis=dict(gridcolor="#232833", title="Kilomètre", dtick=1),
                    yaxis=dict(
                        gridcolor="#232833",
                        title="Allure (min/km)",
                        range=[q95 + pad, q05 - pad],
                        tickformat=".1f",
                    ),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                    margin=dict(l=0, r=0, t=30, b=0),
                    hovermode="x unified",
                )
                st.plotly_chart(fig_splits)

                max_split = int(splits_clean["split"].max())
                mid = max(1, max_split // 2)
                first_h = splits_clean[splits_clean["split"] <= mid]["pace_sec"].mean()
                second_h = splits_clean[splits_clean["split"] > mid]["pace_sec"].mean()
                diff = second_h - first_h
                if abs(diff) < 5:
                    split_msg = "💚 Allure régulière (split neutre)"
                elif diff > 0:
                    split_msg = f"🔴 Split positif : ralentissement moyen de {diff:.0f} sec/km en 2e moitié"
                else:
                    split_msg = f"🟢 Split négatif : accélération moyenne de {abs(diff):.0f} sec/km en 2e moitié"
                st.caption(split_msg)
            else:
                st.info("Pas de splits valides disponibles pour la période.")
        else:
            st.info("Splits non encore chargés — revenez dans quelques instants.")
