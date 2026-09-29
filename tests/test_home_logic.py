"""Accueil : semaine en 7 cases et signaux (logique pure)."""

from datetime import date, datetime

import pandas as pd

from home_logic import (
    HOME_HEADLINES,
    home_signals,
    planned_from_coach,
    planned_from_goal,
    run_totals,
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
    assert [g["run"] for g in goal] == [True, False, True]
    coach = planned_from_coach({"tasks": [
        {"date": THU, "name": "Seuil 3×10", "sport": "running"},
        {"date": THU, "name": "Gainage", "sport": "strength_training"}, {"date": None}]})
    assert coach == [{"date": "2026-09-24", "label": "Seuil 3×10", "run": True},
                     {"date": "2026-09-24", "label": "Gainage", "run": False}]
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



# ---------------------------------------------------------------------------
# Revue PR 1 (lot L)
# ---------------------------------------------------------------------------

def test_renfo_du_matin_ne_marque_pas_la_course_du_jour_faite():
    """Revue : la case du jour passait « fait » après un renfo alors que la carte annonçait le seuil."""
    df = _acts(("2026-09-24 07:00", "strength_training", 0.0))
    today = week_days(df, THU, [{"date": "2026-09-24", "label": "Seuil 8 km", "run": True}])[3]
    assert today["state"] == "today" and today["what"] == "Seuil 8 km"


def test_course_prevue_sans_drapeau_run_compte_comme_course():
    df = _acts(("2026-09-24 07:00", "cycling", 30.0))
    assert week_days(df, THU, [{"date": "2026-09-24", "label": "Seuil"}])[3]["state"] == "today"


def test_renfo_prevu_et_fait_marque_la_journee():
    df = _acts(("2026-09-24 07:00", "strength_training", 0.0))
    today = week_days(df, THU, [{"date": "2026-09-24", "label": "Renfo 30 min", "run": False}])[3]
    assert today["state"] == "done" and today["what"] == "Renfo"


def test_renfo_et_course_prevus_le_meme_jour():
    """Plan Objectif : renfo + course le même jour (Run Coach aussi) — le renfo seul ne suffit pas."""
    df = _acts(("2026-09-24 07:00", "strength_training", 0.0))
    planned = planned_from_goal([
        {"date": "2026-09-24", "kind": "strength", "duration_min": 30},
        {"date": "2026-09-24", "kind": "tempo", "title": "Seuil", "distance_km": 8.0}])
    today = week_days(df, THU, planned)[3]
    assert today["state"] == "today" and today["what"] == "Seuil 8 km"
    df = _acts(("2026-09-24 07:00", "strength_training", 0.0), ("2026-09-24 18:00", "running", 8.0))
    assert week_days(df, THU, planned)[3]["state"] == "done"


def test_sport_croise_sans_plan_reste_fait():
    df = _acts(("2026-09-24 07:00", "cycling", 30.0))
    assert week_days(df, THU)[3]["state"] == "done"


def test_jour_passe_avec_renfo_seul_reste_fait():
    """La règle ne vaut que pour aujourd'hui : un jour passé raconte ce qui a été fait."""
    df = _acts(("2026-09-23 07:00", "strength_training", 0.0))
    days = week_days(df, THU, [{"date": "2026-09-23", "label": "Seuil 8 km", "run": True}])
    assert days[2]["state"] == "done"


def _runs(*rows):
    return pd.DataFrame([{"startTimeLocal": datetime.fromisoformat(d), "activityType": t,
                          "distance_km": km, "avgPace_sec": pace, "duration_min": minutes,
                          "avgHR": hr, "elevationGain": 10.0}
                         for d, t, km, pace, minutes, hr in rows])


def test_run_totals_sur_la_periode_seulement():
    """
    Revue : « Allure moyenne » et « FC moyenne » de la carte portaient sur tout
    l'historique. Un coureur qui a progressé voyait une allure « du mois » plus
    lente que toutes ses sorties du mois.
    """
    df = _runs(("2025-01-10 08:00", "running", 10.0, 420.0, 70.0, 160.0),
               ("2026-08-31 23:59", "running", 10.0, 420.0, 70.0, 160.0),
               ("2026-09-01 00:00", "running", 10.0, 300.0, 50.0, 150.0),
               ("2026-09-20 08:00", "running", 5.0, 330.0, 27.5, 140.0),
               ("2026-09-21 08:00", "cycling", 30.0, 0.0, 60.0, 120.0))
    got = run_totals(df, date(2026, 9, 1))
    assert got["runs"] == 2 and got["km"] == 15.0 and got["elevation"] == 20.0
    # temps total ÷ distance : (3000 + 1650) / 15 = 310 s/km (pas la moyenne 315)
    assert got["pace_sec"] == 310.0
    # FC pondérée par la durée : (150×50 + 140×27,5) / 77,5
    assert abs(got["hr"] - (150 * 50 + 140 * 27.5) / 77.5) < 1e-9


def test_run_totals_sans_course_ni_donnee():
    empty = {"km": 0.0, "runs": 0, "elevation": 0.0, "pace_sec": None, "hr": None}
    assert run_totals(None, THU) == empty and run_totals(pd.DataFrame(), THU) == empty
    df = _runs(("2026-09-24 08:00", "cycling", 30.0, 0.0, 60.0, 120.0))
    assert run_totals(df, THU) == empty
    df = _runs(("2026-09-24 08:00", "running", 0.0, 0.0, 0.0, float("nan")))
    got = run_totals(df, THU)
    assert got["runs"] == 1 and got["pace_sec"] is None and got["hr"] is None


def test_planned_from_suggestion_only_for_a_run_suggested_today():
    from datetime import date, timedelta
    from home_logic import planned_from_suggestion
    today = date(2026, 9, 29)
    rec = {"suggested_date": today, "session": {"label": "Endurance fondamentale"}}
    assert planned_from_suggestion(rec, today) == [{"date": "2026-09-29", "label": "Endurance fondamentale",
                                                   "run": True}]
    assert planned_from_suggestion({**rec, "suggested_date": today + timedelta(days=1)}, today) == []
    assert planned_from_suggestion(None, today) == []
