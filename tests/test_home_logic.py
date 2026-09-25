"""Accueil : semaine en 7 cases et signaux (logique pure)."""

from datetime import date, datetime

import pandas as pd

from home_logic import (
    HOME_HEADLINES,
    home_signals,
    planned_from_coach,
    planned_from_goal,
    short_label,
    week_days,
)

THU = date(2026, 9, 24)   # un jeudi


def _acts(*rows):
    return pd.DataFrame([{"startTimeLocal": datetime.fromisoformat(d), "activityType": t,
                          "distance_km": km} for d, t, km in rows])


def test_week_runs_monday_to_sunday_with_states():
    df = _acts(("2026-09-22 07:00", "running", 9.2), ("2026-09-23 18:00", "strength", 0.0))
    days = week_days(df, THU, [{"date": "2026-09-24", "label": "Footing 10 km"},
                               {"date": "2026-09-27", "label": "Sortie longue 18 km"}])
    assert [d["date"] for d in days] == [date(2026, 9, 21 + i) for i in range(7)]
    states = [d["state"] for d in days]
    assert states == ["rest", "done", "done", "today", "rest", "rest", "plan"]
    assert days[1]["what"] == "Course 9 km" and days[2]["what"] == "Renfo"
    assert days[3]["what"] == "Footing 10 km" and days[3]["is_today"]
    assert [d["short"] for d in days] == ["", "9", "R", "10", "", "", "18"]
    assert days[0]["what"] == "—" and days[4]["what"] == "Repos"


def test_done_beats_planned_and_runs_beat_cross_training():
    df = _acts(("2026-09-24 07:00", "cycling", 30.0), ("2026-09-24 18:00", "running", 5.0))
    today = week_days(df, THU, [{"date": "2026-09-24", "label": "Seuil"}])[3]
    assert today["state"] == "done" and today["what"] == "Course 5 km"


def test_empty_history():
    days = week_days(pd.DataFrame(), THU)
    assert len(days) == 7 and days[3]["state"] == "today"


def test_planned_adapters():
    goal = planned_from_goal([
        {"date": "2026-09-24", "kind": "easy", "title": "Footing", "distance_km": 10.0},
        {"date": "2026-09-25", "kind": "strength", "duration_min": 30},
        {"date": "2026-11-15", "kind": "race", "title": "🏁 Semi"}])
    assert [g["label"] for g in goal] == ["Footing 10 km", "Renfo 30 min", "Course !"]
    coach = planned_from_coach({"tasks": [{"date": THU, "name": "Seuil 3×10"}, {"date": None}]})
    assert coach == [{"date": "2026-09-24", "label": "Seuil 3×10"}]
    assert planned_from_coach(None) == []


def test_signals_follow_acwr_zones():
    for zone, status in [("optimal", "good"), ("vigilance", "warning"),
                         ("risque", "serious"), ("sous_charge", "info")]:
        sig = home_signals({"acwr": 1.1, "acwr_zone": zone}, None, None)
        assert sig[0]["status"] == status, zone
    assert "1,10" in home_signals({"acwr": 1.1, "acwr_zone": "optimal"}, None, None)[0]["body"]


def test_signals_never_invent_data():
    assert home_signals({}, None, []) == []
    assert home_signals(None, None, None) == []


def test_efficiency_and_shoes():
    sig = home_signals({}, 3.4, [{"name": "Pegasus", "distance_km": 640, "retired": False},
                                 {"name": "Vieille", "distance_km": 1200, "retired": True}])
    assert sig[0]["title"] == "+3,4 % d'efficacité" and sig[0]["status"] == "good"
    assert sig[1]["title"] == "Pegasus : 640 km" and sig[1]["status"] == "warning"
    assert home_signals({}, -4.0, None)[0]["status"] == "warning"
    assert home_signals({}, 0.5, None)[0]["title"] == "Efficacité stable"
    worn = home_signals({}, None, [{"name": "X", "distance_km": 900}])[0]
    assert worn["status"] == "serious"


def test_at_most_four_signals():
    sig = home_signals({"acwr": 1.4, "acwr_zone": "vigilance", "monotony_high": True}, 5.0,
                       [{"name": "A", "distance_km": 100}])
    assert len(sig) == 4


def test_headlines_cover_every_verdict_level():
    assert set(HOME_HEADLINES) == {0, 1, 2}


def test_short_label():
    assert short_label("Course 12 km") == "12"
    assert short_label("Sortie longue 18,5 km") == "18"
    assert short_label("Renfo 30 min") == "R"
    assert short_label("Repos") == "" and short_label("—") == ""


def test_short_label_uses_last_km_occurrence():
    """Fuzz repro_short_label : l'allure d'un fractionné (« 5 km ») précède la
    distance totale de la séance (« 9.1 km ») — c'est cette dernière qu'il
    faut afficher, pas la 1re occurrence."""
    assert short_label("Fractionné allure 5 km 9.1 km") == "9"

