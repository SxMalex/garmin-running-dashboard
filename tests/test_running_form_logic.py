"""Foulée à allure égale et pic de sortie unique."""

import numpy as np
import pandas as pd

from running_form_logic import FORM_METRICS, form_report, pace_adjusted_trend, run_spike, spike_level

TODAY = pd.Timestamp("2026-09-25")
GCT = FORM_METRICS[0]


def _runs(n=36, drift_ms=0.0, seed=0, **extra):
    """Une sortie tous les 3,5 jours sur 18 semaines ; GCT = f(vitesse) + dérive récente."""
    rng = np.random.default_rng(seed)
    t = pd.date_range(end=TODAY - pd.Timedelta(days=1), periods=n, freq="84h")
    speed = rng.uniform(2.6, 3.6, n)
    recent = t >= TODAY - pd.Timedelta(weeks=6)
    gct = 400 - 45 * speed + rng.normal(0, 2, n) + np.where(recent, drift_ms, 0)
    return pd.DataFrame({"startTimeLocal": t, "activityType": "running", "avgSpeed_ms": speed,
                         "avgGroundContact_ms": gct, "distance_km": rng.uniform(6, 12, n), **extra})


def test_speed_alone_does_not_trigger_a_drift():
    """Des sorties plus lentes ont un contact au sol plus long : ce n'est pas une dérive."""
    df = _runs()
    recent = df["startTimeLocal"] >= TODAY - pd.Timedelta(weeks=6)
    df.loc[recent, "avgSpeed_ms"] -= 0.4                       # plus lent récemment
    df.loc[recent, "avgGroundContact_ms"] += 45 * 0.4          # …donc GCT plus long, normalement
    r = pace_adjusted_trend(df, GCT, TODAY)
    assert abs(r["delta"]) < 3 and r["status"] == "neutral"


def test_drift_at_equal_pace_is_detected():
    r = pace_adjusted_trend(_runs(drift_ms=12), GCT, TODAY)
    assert 9 < r["delta"] < 15 and r["status"] == "warning"
    assert pace_adjusted_trend(_runs(drift_ms=-12), GCT, TODAY)["status"] == "good"


def test_needs_runs_in_both_windows_and_a_sensor():
    assert pace_adjusted_trend(_runs(n=6), GCT, TODAY) is None
    no_sensor = _runs().drop(columns="avgGroundContact_ms")
    assert form_report(no_sensor, TODAY) == []


def test_spike_levels():
    assert spike_level(1.05) == "ok" and spike_level(1.2) == "watch" and spike_level(1.4) == "high"
    assert spike_level(None) == "none"


def test_last_run_spike_against_the_previous_30_days():
    df = pd.DataFrame({"startTimeLocal": pd.to_datetime(["2026-09-01", "2026-09-10", "2026-09-24"]),
                       "activityType": "running", "distance_km": [10.0, 12.0, 16.0],
                       "activityName": ["a", "b", "Longue"]})
    s = run_spike(df, today=TODAY)["last"]
    assert s["ref_km"] == 12 and round(s["ratio"], 2) == 1.33 and s["level"] == "high"


def test_planned_long_run_spike_is_announced_before_it_happens():
    df = pd.DataFrame({"startTimeLocal": pd.to_datetime(["2026-09-10", "2026-09-20"]),
                       "activityType": "running", "distance_km": [14.0, 10.0]})
    planned = [{"date": "2026-09-26", "kind": "easy", "distance_km": 8},
               {"date": "2026-09-28", "kind": "long", "distance_km": 18, "title": "Sortie longue"},
               {"date": "2026-10-04", "kind": "race", "distance_km": 21.1}]
    p = run_spike(df, planned, TODAY)["planned"]
    assert p["km"] == 18 and p["ref_km"] == 14 and p["level"] == "watch"
    assert run_spike(df, [{"date": "2026-09-28", "kind": "long", "distance_km": 15}], TODAY)["planned"] is None


def test_old_last_run_is_not_reported():
    df = pd.DataFrame({"startTimeLocal": pd.to_datetime(["2026-08-01", "2026-08-10"]),
                       "activityType": "running", "distance_km": [8.0, 20.0]})
    assert run_spike(df, today=TODAY)["last"] is None
