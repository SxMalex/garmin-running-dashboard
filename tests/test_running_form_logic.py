"""Foulée à allure égale et pic de sortie unique."""

import numpy as np
import pytest
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


def _hist(days_km, today=TODAY):
    return pd.DataFrame({"startTimeLocal": [today - pd.Timedelta(days=d) for d, _ in days_km],
                         "activityType": "running", "distance_km": [k for _, k in days_km],
                         "avgPace_sec": 330.0, "activityName": "x"})


def test_plan_progression_is_not_flagged_against_itself():
    """Revue #2 : un plan semi à +10 %/semaine déclenchait « raccourcis-la » sur ses sorties longues."""
    df = _hist([(3, 11.64), (10, 8.0)])
    planned = [{"date": (TODAY + pd.Timedelta(days=d)).date().isoformat(), "kind": "long", "distance_km": km}
               for d, km in ((3, 12.8), (10, 14.1), (17, 15.5))]
    assert run_spike(df, planned, TODAY)["planned"] is None


def test_planned_jump_over_the_previous_planned_session_is_flagged():
    df = _hist([(3, 12.0)])
    planned = [{"date": (TODAY + pd.Timedelta(days=d)).date().isoformat(), "kind": "long", "distance_km": km}
               for d, km in ((3, 12.5), (10, 16.0))]
    p = run_spike(df, planned, TODAY)["planned"]
    assert p["km"] == 16 and p["ref_km"] == 12.5 and p["level"] == "watch"


def test_comeback_after_a_break_is_flagged():
    df = _hist([(2, 12.0), (70, 10.0), (75, 12.0)])        # 5 semaines sans courir, puis 12 km
    last = run_spike(df, today=TODAY)["last"]
    assert last["level"] == "comeback" and last["ratio"] is None
    from home_logic import spike_signal
    assert "Reprise" in spike_signal(run_spike(df, today=TODAY))["title"]
    idle = _hist([(40, 10.0), (45, 10.0)])
    p = run_spike(idle, [{"date": (TODAY + pd.Timedelta(days=2)).date().isoformat(), "kind": "long",
                          "distance_km": 15}], TODAY)["planned"]
    assert p["level"] == "comeback" and "Reprise" in spike_signal({"planned": p})["title"]


def test_first_runs_are_not_a_comeback():
    df = _hist([(2, 12.0)])                                 # aucun historique : premier pas, pas une reprise
    assert run_spike(df, today=TODAY)["last"] is None
    short = _hist([(2, 5.0), (70, 10.0)])                   # reprise courte : rien à dire
    assert run_spike(short, today=TODAY)["last"] is None


def test_planned_runs_follow_run_coach_when_it_leads():
    from datetime import date
    from running_form_logic import planned_runs
    df = _hist([(3, 10.0), (6, 10.0)])
    coach = {"tasks": [{"date": date(2026, 9, 27), "sport": "running", "duration_min": 99,
                        "session_key": "sortie_longue", "name": "Longue", "rest_day": False},
                       {"date": date(2026, 9, 26), "sport": "strength_training", "duration_min": 30,
                        "rest_day": False},
                       {"date": date(2026, 9, 28), "sport": "running", "rest_day": True}]}
    goal = [{"date": "2026-09-27", "kind": "long", "distance_km": 30}]
    runs = planned_runs(coach, goal, df, TODAY)
    assert runs == [{"date": "2026-09-27", "kind": "long", "distance_km": 18.0, "title": "Longue"}]
    assert planned_runs(None, goal, df, TODAY) == goal       # sans Run Coach : le plan Objectif


def test_run_coach_comeback_is_seen_without_recent_pace():
    """Revue : en reprise, aucune allure sur 30 j → distance 0 → alerte jamais levée avec Run Coach."""
    from datetime import date
    from running_form_logic import planned_runs
    df = _hist([(70, 10.0), (75, 12.0)])
    coach = {"tasks": [{"date": date(2026, 9, 27), "sport": "running", "duration_min": 90,
                        "session_key": "sortie_longue", "name": "Longue", "rest_day": False}]}
    runs = planned_runs(coach, None, df, TODAY)
    assert runs[0]["distance_km"] == pytest.approx(90 * 60 / 330, abs=0.1)
    assert run_spike(df, runs, TODAY)["planned"]["level"] == "comeback"


def test_run_coach_event_day_is_not_a_training_spike():
    from datetime import date
    from running_form_logic import planned_runs
    df = _hist([(3, 14.0)])
    coach = {"days_to_event": 3, "tasks": [
        {"date": date(2026, 9, 28), "sport": "running", "duration_min": 240, "session_key": "race",
         "name": "Marathon", "rest_day": False}]}
    assert planned_runs(coach, None, df, TODAY) == []


def test_planned_runs_tolerate_an_empty_account():
    from datetime import date
    from running_form_logic import planned_runs
    coach = {"tasks": [{"date": date(2026, 9, 27), "sport": "running", "duration_min": 40,
                        "rest_day": False}]}
    assert planned_runs(coach, None, pd.DataFrame(), TODAY)[0]["distance_km"] == 0
    assert planned_runs(coach, None, None, TODAY)[0]["distance_km"] == 0


def test_spike_text_says_when_the_reference_is_a_planned_session():
    from home_logic import spike_signal
    df = _hist([(3, 8.0)])
    planned = [{"date": (TODAY + pd.Timedelta(days=d)).date().isoformat(), "kind": "long", "distance_km": km}
               for d, km in ((2, 8.5), (9, 12.0))]
    sig = spike_signal(run_spike(df, planned, TODAY))
    assert "séance prévue avant" in sig["body"] and "plus longue sortie du mois" not in sig["body"]


def test_planned_sessions_without_any_run_history_compare_to_each_other():
    """Premier plan, aucune course encore : la 2e séance se compare à la 1re (référence « prévue »)."""
    df = pd.DataFrame({"startTimeLocal": [TODAY - pd.Timedelta(days=2)], "activityType": "cycling",
                       "distance_km": [30.0], "avgPace_sec": 0.0, "activityName": "vélo"})
    planned = [{"date": (TODAY + pd.Timedelta(days=d)).date().isoformat(), "kind": "run", "distance_km": km}
               for d, km in ((1, 5.0), (3, 10.0))]
    p = run_spike(df, planned, TODAY)["planned"]
    assert p["km"] == 10 and p["ref_km"] == 5 and p["ref_source"] == "planned" and p["level"] == "high"


def test_planned_runs_during_a_run_coach_outage_announce_nothing():
    """Intégration : COACH_UNKNOWN (dict vide, faux) retombait sur le plan Objectif."""
    from coach_logic import COACH_UNKNOWN
    from running_form_logic import planned_runs
    goal = [{"date": "2026-09-27", "kind": "long", "distance_km": 30}]
    assert planned_runs(COACH_UNKNOWN, goal, _hist([(3, 10.0)]), TODAY) == []
