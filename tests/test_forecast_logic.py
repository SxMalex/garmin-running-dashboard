"""Projections « si tu continues comme ça » : prudence, bornes, refus sans données."""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from forecast_logic import (
    RACE_MAX_MONTHLY_PCT,
    project,
    race_projection,
    trend_word,
    vo2max_projection,
)


def _series(days, start_value, per_day, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    d0 = date(2026, 1, 1)
    dates = [d0 + timedelta(days=i) for i in range(days)]
    values = [start_value + per_day * i + rng.normal(0, noise) for i in range(days)]
    return dates, values


def test_refuses_to_project_without_enough_history():
    dates, values = _series(10, 3000, -1)
    assert project(dates, values, [30], max_monthly_change=2, relative=True) is None
    assert project([], [], [30], max_monthly_change=2, relative=True) is None


def test_follows_a_clean_trend_with_diminishing_returns():
    dates, values = _series(90, 3000, -1.0)          # −1 s/jour sur un 10 km
    res = project(dates, values, [30, 90], max_monthly_change=5, relative=True)
    p30, p90 = res["projections"]
    assert p30.value < res["last_value"] and p90.value < p30.value
    gain30, gain90 = res["last_value"] - p30.value, res["last_value"] - p90.value
    assert gain30 < 30 and gain90 < 3 * gain30        # amorti : moins qu'une droite
    assert p30.low <= p30.value <= p30.high


def test_monthly_change_is_capped_both_ways():
    dates, values = _series(60, 3000, -20.0)          # −10 min/mois : absurde
    res = project(dates, values, [30], max_monthly_change=RACE_MAX_MONTHLY_PCT, relative=True)
    assert res["slope_per_30d"] >= -RACE_MAX_MONTHLY_PCT / 100 * res["last_value"] - 1e-6
    dates, values = _series(60, 3000, +20.0)
    res = project(dates, values, [30], max_monthly_change=RACE_MAX_MONTHLY_PCT, relative=True)
    assert res["slope_per_30d"] <= RACE_MAX_MONTHLY_PCT / 100 * res["last_value"] + 1e-6


def test_robust_to_one_absurd_prediction():
    dates, values = _series(60, 3000, 0.0, noise=3)
    values[40] = 1000                                  # prédiction aberrante un jour
    res = project(dates, values, [90], max_monthly_change=2, relative=True)
    assert abs(res["projections"][0].value - 3000) < 30


def test_band_widens_with_horizon():
    dates, values = _series(80, 3000, -0.5, noise=10)
    p30, p90 = project(dates, values, [30, 90], max_monthly_change=2, relative=True)["projections"]
    assert (p90.high - p90.low) > (p30.high - p30.low)


def test_race_projection_from_history_frame():
    dates, values = _series(90, 2700, -0.8)
    hist = pd.DataFrame({"date": pd.to_datetime(dates), "distance": "10 km", "time_sec": values})
    assert race_projection(hist, "10 km")["projections"][0].days == 30
    assert race_projection(hist, "Marathon") is None
    assert race_projection(pd.DataFrame(), "10 km") is None


def test_vo2max_projection_bounded_and_runs_only():
    dates, values = _series(120, 88.5, 0.2)            # tendance qui franchirait 90
    df = pd.DataFrame({"startTimeLocal": pd.to_datetime(dates), "activityType": "running",
                       "vo2max": values})
    df.loc[5, "activityType"] = "cycling"
    res = vo2max_projection(df)
    assert [p.days for p in res["projections"]] == [30, 60, 90, 120, 150, 180]
    assert all(p.value <= 90 and p.high <= 90 for p in res["projections"])
    assert res["slope_per_30d"] <= 1.0 + 1e-9
    assert vo2max_projection(df.drop(columns="vo2max")) is None


def test_trend_word():
    assert trend_word(-20, lower_is_better=True, tolerance=5) == "en progression"
    assert trend_word(+20, lower_is_better=True, tolerance=5) == "en retrait"
    assert trend_word(0.3, lower_is_better=False, tolerance=0.5) == "stable"


def test_v_shaped_season_projects_the_current_momentum():
    """Retour sur données réelles : 3 mois de baisse puis 5 semaines de nette reprise.
    La projection doit partir du niveau ACTUEL et prolonger la reprise, pas une
    moyenne de la saison (qui annonçait une régression de 2 min sur 5 km)."""
    d0 = date(2026, 5, 1)
    dates = [d0 + timedelta(days=i) for i in range(126)]
    values = [1440 + 1.2 * i if i < 90 else 1440 + 108 - 4.0 * (i - 90) for i in range(126)]
    res = project(dates, values, [30, 90], max_monthly_change=RACE_MAX_MONTHLY_PCT, relative=True)
    assert abs(res["last_value"] - values[-1]) < 15           # ancré sur la dernière semaine
    p30, p90 = res["projections"]
    assert p30.value < res["last_value"] and p90.value < p30.value   # la reprise continue
    assert res["last_value"] - p90.value <= 0.02 * 3 * res["last_value"]   # mais plafonnée


def test_integer_vo2max_gets_a_real_uncertainty_band():
    dates, _ = _series(60, 0, 0)
    df = pd.DataFrame({"startTimeLocal": pd.to_datetime(dates), "activityType": "running",
                       "vo2max": [47.0] * 60})
    p = vo2max_projection(df)["projections"][2]
    assert p.value == 47 and p.high - p.low >= 2
