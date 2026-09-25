"""
Projection « si tu continues à t'entraîner comme ça » : prédictions de course
(30 / 90 jours) et VO2max (1 à 6 mois). Logique pure, testée.

Ce n'est PAS une prévision au sens statistique fort : c'est la tendance récente
prolongée prudemment. Trois garde-fous, parce qu'une droite prolongée promet
n'importe quoi :

1. **Tendance robuste** (Theil-Sen) sur les dernières semaines seulement —
   « si tu continues comme ça » = ton rythme actuel, pas celui d'il y a 4 mois
   (une saison en V, baisse puis reprise, donnerait sinon une pente nulle) —
   et **départ ancré sur ton niveau de la dernière semaine** (médiane), pas sur
   une moyenne de la fenêtre. Une prédiction aberrante un jour ne pèse rien.
2. **Rendements décroissants** : la pente s'amortit (constante `tau`) — on
   progresse vite au début d'un bloc, puis on plafonne.
3. **Bornes physiologiques** : variation mensuelle plafonnée (≈ 2 %/mois sur un
   temps de course, ≈ 1 point/mois de VO2max), dans les deux sens.

La marge d'incertitude s'élargit avec l'horizon (dispersion des résidus ×
√(1 + h / durée observée)). Sans assez d'historique, on ne projette rien.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

# Race times : ≤ 2 %/mois d'amélioration ou de régression (ordre de grandeur des
# gains sur un bloc bien mené) ; VO2max Garmin : ≤ 1 point/mois.
RACE_MAX_MONTHLY_PCT = 2.0
VO2_MAX_MONTHLY_POINTS = 1.0
DEFAULT_TAU_DAYS = 75.0
MIN_POINTS = 8
MIN_SPAN_DAYS = 28


@dataclass(frozen=True)
class Projection:
    days: int
    value: float
    low: float
    high: float


def _theil_sen(x: np.ndarray, y: np.ndarray, max_pairs: int = 20000) -> tuple[float, float]:
    """Pente et ordonnée robustes (médiane des pentes deux à deux)."""
    n = len(x)
    i, j = np.triu_indices(n, k=1)
    if len(i) > max_pairs:  # sous-échantillonnage déterministe des paires
        keep = np.linspace(0, len(i) - 1, max_pairs).astype(int)
        i, j = i[keep], j[keep]
    dx = x[j] - x[i]
    ok = dx != 0
    slope = float(np.median((y[j] - y[i])[ok] / dx[ok])) if ok.any() else 0.0
    intercept = float(np.median(y - slope * x))
    return slope, intercept


def project(dates, values, horizons_days, *, lookback_days: int = 56,
            max_monthly_change: float, relative: bool, tau_days: float = DEFAULT_TAU_DAYS,
            min_sigma: float = 0.0, anchor_days: int = 7,
            lower: float | None = None, upper: float | None = None) -> dict | None:
    """
    Projette la série (`dates`, `values`) aux horizons donnés (jours après la
    dernière mesure). `max_monthly_change` est en % de la dernière valeur si
    `relative`, sinon en unités. Retourne None si l'historique récent est trop
    court : mieux vaut ne rien afficher qu'une projection inventée.

    Retour : {"last_date", "last_value", "slope_per_30d", "basis_days", "n",
              "projections": [Projection, …]}
    """
    s = pd.Series(pd.to_numeric(pd.Series(values), errors="coerce").to_numpy(),
                  index=pd.to_datetime(pd.Series(dates)).to_numpy()).dropna().sort_index()
    s = s[np.isfinite(s.to_numpy())]
    if s.empty:
        return None
    s = s.groupby(level=0).mean()                       # une valeur par jour
    last_date = s.index.max()
    s = s[s.index >= last_date - pd.Timedelta(days=lookback_days)]
    span = (s.index.max() - s.index.min()).days
    if len(s) < MIN_POINTS or span < MIN_SPAN_DAYS:
        return None

    x = ((s.index - last_date) / pd.Timedelta(days=1)).to_numpy(float)
    y = s.to_numpy(float)
    slope, intercept = _theil_sen(x, y)
    # Départ = niveau actuel (médiane de la dernière semaine), pas l'ordonnée
    # de la droite : sinon la projection « saute » loin de la dernière mesure.
    last_value = float(np.median(y[x >= -anchor_days]))
    resid = y - (intercept + slope * x)
    sigma = max(float(1.4826 * np.median(np.abs(resid - np.median(resid)))), min_sigma)

    cap_per_day = (max_monthly_change / 100 * abs(last_value) if relative
                   else max_monthly_change) / 30.0
    slope_c = max(-cap_per_day, min(cap_per_day, slope))

    out = []
    for h in horizons_days:
        # Amortissement : l'effet cumulé tend vers slope × tau (plateau).
        change = slope_c * tau_days * (1 - math.exp(-h / tau_days))
        value = last_value + change
        half = max(sigma, 1e-9) * math.sqrt(1 + h / max(span, 1))
        low, high = value - half, value + half
        if lower is not None:
            value, low, high = max(value, lower), max(low, lower), max(high, lower)
        if upper is not None:
            value, low, high = min(value, upper), min(low, upper), min(high, upper)
        out.append(Projection(days=int(h), value=float(value), low=float(low), high=float(high)))
    return {"last_date": last_date, "last_value": last_value,
            "slope_per_30d": slope_c * 30, "basis_days": span, "n": int(len(s)),
            "projections": out}


def race_projection(history: pd.DataFrame, distance: str,
                    horizons=(30, 90)) -> dict | None:
    """Projection d'un temps prédit (colonnes date, distance, time_sec)."""
    if history is None or history.empty:
        return None
    serie = history[history["distance"] == distance]
    last = serie["time_sec"].dropna()
    floor = 0.01 * float(last.iloc[-1]) if not last.empty else 0.0   # ±1 % au minimum
    return project(serie["date"], serie["time_sec"], horizons,
                   max_monthly_change=RACE_MAX_MONTHLY_PCT, relative=True,
                   min_sigma=floor, lower=1.0)


def vo2max_projection(activities: pd.DataFrame,
                      horizons=(30, 60, 90, 120, 150, 180)) -> dict | None:
    """Projection de la VO2max Garmin portée par les courses (colonne vo2max)."""
    if activities is None or activities.empty or "vo2max" not in activities.columns:
        return None
    runs = activities[activities["activityType"] == "running"]
    # VO2max Garmin : entier, rarement mesurée → fenêtre plus longue et ±1
    # point d'incertitude au minimum (sinon « entre 47 et 47 »).
    return project(runs["startTimeLocal"], runs["vo2max"], horizons, lookback_days=90,
                   max_monthly_change=VO2_MAX_MONTHLY_POINTS, relative=False,
                   min_sigma=1.0, anchor_days=14, lower=20.0, upper=90.0)


def trend_word(slope_per_30d: float, *, lower_is_better: bool, tolerance: float) -> str:
    """« en progression » / « stable » / « en retrait » selon le sens utile."""
    if abs(slope_per_30d) < tolerance:
        return "stable"
    improving = slope_per_30d < 0 if lower_is_better else slope_per_30d > 0
    return "en progression" if improving else "en retrait"
