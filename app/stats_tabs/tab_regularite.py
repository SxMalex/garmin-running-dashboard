import numpy as np
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date, timedelta


def render(running_df: pd.DataFrame) -> None:
    st.subheader("Calendrier de régularité")

    available_years = sorted(running_df["startTimeLocal"].dt.year.unique(), reverse=True)
    # On n'utilise pas `index=0` car il serait ignoré si `heatmap_year` existe
    # déjà en session_state — le selectbox initialise depuis la session.
    selected_year = st.selectbox("Année", available_years, key="heatmap_year")

    year_df = running_df[running_df["startTimeLocal"].dt.year == selected_year].copy()
    year_df["date"] = pd.to_datetime(year_df["startTimeLocal"].dt.date)
    daily = year_df.groupby("date").agg(
        distance_km=("distance_km", "sum"),
        nb_sorties=("activityId", "count"),
    ).reset_index()

    jan1  = pd.Timestamp(f"{selected_year}-01-01")
    dec31 = pd.Timestamp(f"{selected_year}-12-31")
    full_year = pd.DataFrame({"date": pd.date_range(jan1, dec31, freq="D")})
    full_year = full_year.merge(daily, on="date", how="left")
    full_year["distance_km"] = full_year["distance_km"].fillna(0.0)
    full_year["nb_sorties"]  = full_year["nb_sorties"].fillna(0).astype(int)
    full_year["dow"] = full_year["date"].dt.dayofweek

    grid_start = jan1 - pd.Timedelta(days=jan1.dayofweek)
    full_year["col"] = ((full_year["date"] - grid_start) / pd.Timedelta(days=1)).astype(int) // 7
    n_weeks = int(full_year["col"].max()) + 1

    z          = np.full((7, n_weeks), np.nan)
    hover_text = np.full((7, n_weeks), "", dtype=object)
    for _, row in full_year.iterrows():
        dow = int(row["dow"])
        col = int(row["col"])
        km  = float(row["distance_km"])
        z[dow, col] = km
        date_str = row["date"].strftime("%A %d %B")
        if km > 0:
            detail = f" ({int(row['nb_sorties'])} sorties)" if row["nb_sorties"] > 1 else ""
            hover_text[dow, col] = f"{date_str}<br>🏃 {km:.1f} km{detail}"
        else:
            hover_text[dow, col] = f"{date_str}<br>Repos"

    x_tickvals, x_ticktext = [], []
    for month in range(1, 13):
        m_start  = pd.Timestamp(f"{selected_year}-{month:02d}-01")
        col_idx  = int((m_start - grid_start) / pd.Timedelta(days=1)) // 7
        if 0 <= col_idx < n_weeks:
            x_tickvals.append(col_idx)
            x_ticktext.append(m_start.strftime("%b"))

    colorscale = [
        [0.00, "#151b26"], [0.05, "#104281"], [0.25, "#1c5cab"],
        [0.50, "#3987e5"], [0.75, "#6da7ec"], [1.00, "#9ec5f4"],
    ]
    max_km = float(full_year["distance_km"].max()) or 1.0

    fig_cal = go.Figure(go.Heatmap(
        z=z,
        x=list(range(n_weeks)),
        y=["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"],
        text=hover_text,
        hovertemplate="%{text}<extra></extra>",
        colorscale=colorscale,
        zmin=0, zmax=max_km,
        xgap=3, ygap=3,
        showscale=True,
        colorbar=dict(
            title="km", thickness=12, len=0.85,
            tickfont=dict(color="#c6c8ce", size=10),
            titlefont=dict(color="#c6c8ce"),
        ),
    ))
    fig_cal.update_layout(
        height=210,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#c6c8ce"),
        xaxis=dict(
            tickmode="array", tickvals=x_tickvals, ticktext=x_ticktext,
            tickfont=dict(size=11), showgrid=False, side="top",
        ),
        yaxis=dict(autorange="reversed", tickfont=dict(size=10), showgrid=False),
        margin=dict(l=40, r=80, t=30, b=10),
    )
    st.plotly_chart(fig_cal)

    dates_with_run: set[date] = set(
        full_year[full_year["distance_km"] > 0]["date"].dt.date
    )
    today = date.today()

    def _streak_back(d: date, date_set: set) -> int:
        count = 0
        while d in date_set:
            count += 1
            d -= timedelta(days=1)
        return count

    current_streak = _streak_back(today, dates_with_run)
    if current_streak == 0:
        current_streak = _streak_back(today - timedelta(days=1), dates_with_run)

    streak_max = temp_streak = 0
    for _, row in full_year.sort_values("date").iterrows():
        if row["distance_km"] > 0:
            temp_streak += 1
            streak_max = max(streak_max, temp_streak)
        else:
            temp_streak = 0

    n_jours = len(dates_with_run & {
        (jan1 + pd.Timedelta(days=i)).date() for i in range((dec31 - jan1).days + 1)
    })
    total_km_year = full_year["distance_km"].sum()

    full_year["month"] = full_year["date"].dt.month
    monthly_runs = full_year[full_year["distance_km"] > 0].groupby("month").size()
    best_month_num  = int(monthly_runs.idxmax()) if not monthly_runs.empty else None
    best_month_name = (
        pd.Timestamp(f"{selected_year}-{best_month_num:02d}-01").strftime("%B")
        if best_month_num else "—"
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Jours courus",    str(n_jours))
    c2.metric("Volume total",    f"{total_km_year:.0f} km")
    c3.metric("Streak max",      f"{streak_max} j")
    c4.metric("Streak actuel",   f"{current_streak} j")
    c5.metric("Mois le + actif", best_month_name)
