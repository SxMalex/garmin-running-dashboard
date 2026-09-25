"""Helpers partagés entre les onglets de 2_Stats.py."""

import numpy as np
import plotly.graph_objects as go

import chart_theme as ct

# Une seule définition : chart_theme (sous-ensemble validé de CAT).
WORKOUT_COLORS = ct.WORKOUT_COLORS


def add_trend_line(
    fig: go.Figure, x, y, ascending_better: bool = False
) -> tuple[go.Figure, float | None]:
    """
    Ajoute une ligne de tendance (régression linéaire) à un graphique Plotly.
    Retourne (fig, pente) — pente=None si moins de 5 points.
    ascending_better=True → vert si pente > 0 (cadence) ; False → vert si pente < 0 (allure, FC).
    """
    if len(y) < 5:
        return fig, None
    x_num = np.arange(len(y))
    z = np.polyfit(x_num, y, 1)
    slope = z[0]
    color = (
        "rgba(12,163,12,0.85)"
        if (slope > 0) == ascending_better
        else "rgba(208,59,59,0.8)"
    )
    fig.add_trace(go.Scatter(
        x=x,
        y=np.poly1d(z)(x_num),
        mode="lines",
        name="Tendance",
        line=dict(color=color, width=2, dash="dot"),
    ))
    return fig, slope
