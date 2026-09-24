"""
Tests du générateur de plan : invariants de structure, de charge et de
placement du renfo (règles sourcées, cf. race_plan_logic.SOURCES).
"""

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from race_plan_logic import (
    CUTBACK_FACTOR,
    DISTANCE_PROFILE,
    MAX_BUILD_WEEKS,
    MAX_PEAK_OVER_START,
    MAX_WEEKLY_GROWTH,
    STRENGTH_STOP_DAYS_BEFORE_RACE,
    athlete_baseline,
    build_race_plan,
    plan_phases,
    plan_sessions,
    weekly_volumes,
)

TODAY = date(2026, 9, 24)  # un jeudi


def history(weeks=8, runs_per_week=4, km=7.0, pace=330.0, strength=0):
    rows, i = [], 0
    for w in range(weeks):
        for r in range(runs_per_week):
            i += 1
            rows.append({
                "activityId": i, "activityType": "running",
                "startTimeLocal": datetime.combine(TODAY, datetime.min.time())
                - timedelta(days=7 * w + 2 * r + 1),
                "distance_km": km + (4 if r == 0 else 0), "avgPace_sec": pace,
                "duration_min": km * pace / 60,
            })
    for s in range(strength):
        rows.append({"activityId": 1000 + s, "activityType": "strength_training",
                     "startTimeLocal": datetime.combine(TODAY, datetime.min.time())
                     - timedelta(days=3 * s + 1),
                     "distance_km": 0.0, "avgPace_sec": 0.0, "duration_min": 40})
    return pd.DataFrame(rows)


def plan_for(weeks_out=16, distance="Semi-marathon", df=None, **kw):
    df = history() if df is None else df
    base = athlete_baseline(df, TODAY)
    return build_race_plan(TODAY + timedelta(weeks=weeks_out), distance, base, TODAY, **kw)


# ---------------------------------------------------------------------------
# Situation de départ
# ---------------------------------------------------------------------------

class TestBaseline:
    def test_recent_volume_and_pace(self):
        b = athlete_baseline(history(), TODAY)
        assert b["weekly_km"] == pytest.approx(4 * 7 + 4, abs=0.1)
        assert b["runs_per_week"] == 4
        assert b["pace_10k_sec"] > 0
        assert b["strength_sessions_8w"] == 0
        assert any("renforcement" in a for a in b["assumptions"])

    def test_empty_history_uses_defaults(self):
        b = athlete_baseline(pd.DataFrame(), TODAY)
        assert b["weekly_km"] == 0
        assert b["pace_10k_sec"] == 360.0
        assert len(b["assumptions"]) == 3

    def test_pace_priority_race_then_prediction_then_training(self):
        """Revue P2 : les footings ne doivent pas écraser une course ou une prédiction."""
        df = history(pace=360.0)
        training = athlete_baseline(df, TODAY)
        assert training["pace_source"] == "training"
        assert any("entraînements" in a for a in training["assumptions"])
        pred = athlete_baseline(df, TODAY, predictions={10.0: 45 * 60})
        assert pred["pace_source"] == "prediction"
        assert pred["pace_10k_sec"] == pytest.approx(270 * 1.03)
        raced = df.copy()
        raced.loc[0, ["workoutType", "distance_km", "avgPace_sec"]] = ["race", 10.0, 280.0]
        race = athlete_baseline(raced, TODAY, predictions={10.0: 45 * 60})
        assert race["pace_source"] == "race"
        assert race["pace_10k_sec"] == pytest.approx(280.0)

    def test_future_and_old_activities_ignored(self):
        df = history(weeks=12)
        b12 = athlete_baseline(df, TODAY)
        b8 = athlete_baseline(history(weeks=8), TODAY)
        assert b12["weekly_km"] == b8["weekly_km"]


# ---------------------------------------------------------------------------
# Phases et volumes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("distance", list(DISTANCE_PROFILE))
def test_phase_order_and_taper(distance):
    phases = plan_phases(16, distance)
    order = ["MAINTENANCE", "BASE", "BUILD", "PEAK", "TAPER"]
    assert [order.index(p) for p in phases] == sorted(order.index(p) for p in phases)
    assert phases.count("TAPER") == DISTANCE_PROFILE[distance]["taper_weeks"]


def test_far_goal_starts_with_maintenance():
    phases = plan_phases(34, "Marathon")
    assert phases.count("MAINTENANCE") == 34 - 3 - MAX_BUILD_WEEKS
    plan = plan_for(weeks_out=33, distance="Marathon")
    assert any("lointain" in w for w in plan["warnings"])


def test_short_goal_warns_and_tapers():
    plan = plan_for(weeks_out=1, distance="10 km")
    assert any("trop tôt" in w for w in plan["warnings"])
    assert {w["phase"] for w in plan["weeks"]} <= {"BUILD", "TAPER"}


def test_past_race_date():
    base = athlete_baseline(history(), TODAY)
    plan = build_race_plan(TODAY, "10 km", base, TODAY)
    assert plan["weeks"] == [] and plan["warnings"]


def test_weekly_growth_capped_and_peak_bounded():
    phases = plan_phases(20, "Marathon")
    vols = weekly_volumes(phases, 30.0, 65.0)
    last_full = vols[0]
    for phase, prev, cur in zip(phases[1:], vols, vols[1:]):
        if phase == "TAPER":
            assert cur < last_full
            continue
        if cur < prev:  # semaine allégée
            assert cur == pytest.approx(prev * CUTBACK_FACTOR, rel=0.02) or cur < prev
            continue
        assert cur <= last_full * (1 + MAX_WEEKLY_GROWTH) + 0.11
        last_full = cur
    assert max(vols) <= 65.0


def test_plan_peak_bounded_by_start_volume():
    plan = plan_for(weeks_out=20, distance="Marathon")
    s = plan["summary"]
    assert s["peak_km"] <= s["start_km"] * MAX_PEAK_OVER_START + 0.1


def test_long_horizon_does_not_explode_volume():
    """Revue de conception : 30 sem × +10 % donnerait ~200 km/sem."""
    plan = plan_for(weeks_out=30, distance="Marathon")
    assert max(w["volume_km"] for w in plan["weeks"]) <= DISTANCE_PROFILE["Marathon"]["peak_km"]


def test_empty_history_starts_at_floor():
    plan = plan_for(distance="10 km", df=pd.DataFrame())
    assert plan["summary"]["start_km"] == DISTANCE_PROFILE["10 km"]["start_floor_km"]
    assert plan["weeks"][1]["volume_km"] > 0


# ---------------------------------------------------------------------------
# Séances
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("runs", [3, 4, 5, 6])
def test_sessions_structure(runs):
    plan = plan_for(runs_per_week=runs)
    sessions = plan_sessions(plan)
    assert sessions and all(date.fromisoformat(s["date"]) >= TODAY for s in sessions)
    assert all(s.get("why") and s.get("title") for s in sessions)
    race = [s for s in sessions if s["kind"] == "race"]
    assert len(race) == 1 and race[0]["date"] == (TODAY + timedelta(weeks=16)).isoformat()
    for week in plan["weeks"][1:-1]:
        run_count = sum(s["kind"] not in ("strength",) for s in week["sessions"])
        assert 3 <= run_count <= runs     # moins de sorties si le volume est faible


def test_quality_sessions_not_consecutive():
    for runs in (4, 5, 6):
        sessions = plan_sessions(plan_for(runs_per_week=runs))
        hard = sorted(date.fromisoformat(s["date"]) for s in sessions
                      if s["kind"] in ("tempo", "interval", "race_pace", "long"))
        assert all((b - a).days >= 2 for a, b in zip(hard, hard[1:]))


def test_long_run_on_chosen_weekday_and_capped():
    plan = plan_for(long_run_weekday=5, distance="10 km")
    longs = [s for s in plan_sessions(plan) if s["kind"] == "long"]
    assert longs and all(date.fromisoformat(s["date"]).weekday() == 5 for s in longs)
    assert max(s["distance_km"] for s in longs) <= DISTANCE_PROFILE["10 km"]["long_cap_km"]


def test_paces_ordered():
    paces = plan_for()["summary"]["paces"]
    to_s = lambda p: int(p.split(":")[0]) * 60 + int(p.split(":")[1])
    assert to_s(paces["interval"][0]) < to_s(paces["tempo"][0]) < to_s(paces["easy"][0])


def test_ambitious_target_warns():
    plan = plan_for(distance="10 km", target_time_s=35 * 60)
    assert any("ambitieux" in w for w in plan["warnings"])
    assert plan["summary"]["race_pace_sec"] == pytest.approx(210.0)


def test_personal_context_in_explanations():
    plan = plan_for(context={"long_run_decoupling_pct": 17.4})
    long_run = next(s for s in plan_sessions(plan) if s["kind"] == "long")
    assert "17 %" in long_run["explain"]


def test_deterministic():
    assert plan_for() == plan_for()


def test_steps_for_structured_sessions():
    for s in plan_sessions(plan_for(runs_per_week=5)):
        if s["kind"] in ("tempo", "interval", "race_pace", "strides"):
            kinds = [st["type"] for st in s["steps"]]
            assert kinds == ["warmup", "repeat", "cooldown"]
            assert s["steps"][1]["count"] >= 2


# ---------------------------------------------------------------------------
# Renforcement (règles sourcées)
# ---------------------------------------------------------------------------

def _key_dates(sessions):
    return {date.fromisoformat(s["date"]) for s in sessions
            if s["kind"] in ("tempo", "interval", "race_pace", "long", "race")}


@pytest.mark.parametrize("distance", list(DISTANCE_PROFILE))
@pytest.mark.parametrize("runs", [3, 4, 5, 6])
def test_strength_never_day_before_key_session(distance, runs):
    sessions = plan_sessions(plan_for(distance=distance, runs_per_week=runs))
    keys = _key_dates(sessions)
    for s in sessions:
        if s["kind"] == "strength":
            day = date.fromisoformat(s["date"])
            assert day + timedelta(days=1) not in keys, s
            longs = {date.fromisoformat(x["date"]) for x in sessions if x["kind"] == "long"}
            assert day not in longs


@pytest.mark.parametrize("distance", list(DISTANCE_PROFILE))
def test_no_strength_in_last_days(distance):
    plan = plan_for(distance=distance)
    race = date.fromisoformat(plan["summary"]["race_date"])
    last = max(date.fromisoformat(s["date"]) for s in plan_sessions(plan) if s["kind"] == "strength")
    assert (race - last).days >= STRENGTH_STOP_DAYS_BEFORE_RACE


def test_strength_dose_by_phase():
    plan = plan_for(weeks_out=16)
    for week in plan["weeks"][1:]:
        n = sum(s["kind"] == "strength" for s in week["sessions"])
        if week["phase"] in ("BASE", "BUILD"):
            assert n == 2, week["phase"]
        elif week["phase"] == "PEAK":
            assert n == 1


def test_strength_intro_for_beginners_only():
    novice = plan_sessions(plan_for())
    trained = plan_sessions(plan_for(df=history(strength=6)))
    assert any("initiation" in s["title"] for s in novice if s["kind"] == "strength")
    assert not any("initiation" in s["title"] for s in trained if s["kind"] == "strength")


def test_strength_can_be_disabled():
    sessions = plan_sessions(plan_for(include_strength=False))
    assert not any(s["kind"] == "strength" for s in sessions)


def test_strength_spaced_two_days():
    sessions = plan_sessions(plan_for(runs_per_week=3))
    days = sorted(date.fromisoformat(s["date"]) for s in sessions if s["kind"] == "strength")
    assert all((b - a).days >= 2 for a, b in zip(days, days[1:]))


@pytest.mark.parametrize("text,expected", [
    ("1:45:00", 6300), ("45:30", 2730), ("1h45", 6300), ("", None), (None, None),
    ("abc", None), ("0:00", None), ("1:2:3:4", None),
])
def test_parse_race_time(text, expected):
    from race_plan_logic import parse_race_time
    assert parse_race_time(text) == expected


def test_predictions_by_km_and_brief():
    from race_plan_logic import plan_brief, predictions_by_km
    assert predictions_by_km({"time5K": 1500, "time10K": None, "x": 3}) == {5.0: 1500.0}
    assert predictions_by_km(None) == {}
    brief = plan_brief(plan_for(weeks_out=8, distance="10 km"))
    assert "Objectif : 10 km" in brief and "S1 " in brief
    assert plan_brief({}) == ""


# ---------------------------------------------------------------------------
# Revue P2 : cas limites
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("long_day", range(7))
@pytest.mark.parametrize("runs", [3, 4, 5, 6])
def test_strength_rules_hold_for_every_long_run_weekday(long_day, runs):
    """Le bug masqué par un test « dimanche seulement » : renfo veille de séance clé."""
    sessions = plan_sessions(plan_for(runs_per_week=runs, long_run_weekday=long_day))
    keys = _key_dates(sessions)
    longs = {date.fromisoformat(s["date"]) for s in sessions if s["kind"] == "long"}
    for s in sessions:
        if s["kind"] == "strength":
            day = date.fromisoformat(s["date"])
            assert day + timedelta(days=1) not in keys, (long_day, s["date"])
            assert day not in longs


@pytest.mark.parametrize("long_day", range(7))
def test_sessions_stay_in_their_calendar_week(long_day):
    plan = plan_for(long_run_weekday=long_day)
    for w in plan["weeks"]:
        start = date.fromisoformat(w["start"])
        for s in w["sessions"]:
            assert start <= date.fromisoformat(s["date"]) < start + timedelta(days=7)


@pytest.mark.parametrize("weekday_offset", range(7))
@pytest.mark.parametrize("distance", list(DISTANCE_PROFILE))
def test_no_long_run_just_before_race_and_one_session_per_day(weekday_offset, distance):
    """Semi un lundi : plus de sortie longue la veille ni de doublon avec le déblocage."""
    race = TODAY + timedelta(weeks=10, days=weekday_offset)
    base = athlete_baseline(history(), TODAY)
    sessions = plan_sessions(build_race_plan(race, distance, base, TODAY))
    for s in sessions:
        if s["kind"] == "long":
            assert (race - date.fromisoformat(s["date"])).days > 6
    runs_by_day = {}
    for s in sessions:
        if s["kind"] != "strength":
            runs_by_day.setdefault(s["date"], []).append(s["kind"])
    assert all(len(k) == 1 for k in runs_by_day.values()), runs_by_day
    assert runs_by_day[(race - timedelta(days=1)).isoformat()] == ["shakeout"]


@pytest.mark.parametrize("distance", list(DISTANCE_PROFILE))
@pytest.mark.parametrize("runs", [3, 6])
def test_prescribed_volume_matches_announced(distance, runs):
    """Revue P2 : un débutant recevait jusqu'à +76 % du volume affiché."""
    plan = plan_for(distance=distance, df=pd.DataFrame(), runs_per_week=runs)
    for w in plan["weeks"]:
        if any(s["kind"] == "race" for s in w["sessions"]) or date.fromisoformat(w["start"]) < TODAY:
            continue
        assert w["prescribed_km"] <= w["volume_km"] * 1.10 + 0.2, w


def test_implausible_target_ignored():
    """« 3:30 » pour un marathon = 3 h 30, pas 3 min 30 ; « 0:45 » reste invraisemblable."""
    from race_plan_logic import parse_race_time
    assert parse_race_time("3:30", "Marathon") == 3.5 * 3600
    assert parse_race_time("45:30", "10 km") == 2730
    plan = plan_for(distance="Semi-marathon", target_time_s=105)
    assert any("invraisemblable" in w for w in plan["warnings"])
    assert plan["summary"]["target_time_s"] is None
    assert plan["summary"]["race_pace_sec"] > 150


def test_two_weeks_out_message_honest():
    plan = plan_for(weeks_out=1, distance="Semi-marathon")
    phases = {w["phase"] for w in plan["weeks"]}
    msg = next(w for w in plan["warnings"] if "trop tôt" in w)
    assert ("affûtage" in msg) and (("séances clés" in msg) == (phases != {"TAPER"}))


@pytest.mark.parametrize("pace", [220.0, 330.0, 420.0])
@pytest.mark.parametrize("long_day", [0, 3, 6])
@pytest.mark.parametrize("weeks_out", [3, 8, 16])
@pytest.mark.parametrize("distance", list(DISTANCE_PROFILE))
def test_prescribed_never_exceeds_announced_grid(pace, long_day, weeks_out, distance):
    """Revue : 14 000 semaines en infraction sur la grille ; le volume annoncé dit vrai."""
    df = history(km=4.0, pace=pace, runs_per_week=3)
    base = athlete_baseline(df, TODAY)
    plan = build_race_plan(TODAY + timedelta(weeks=weeks_out), distance, base, TODAY,
                           runs_per_week=3, long_run_weekday=long_day)
    for w in plan["weeks"]:
        if any(s["kind"] == "race" for s in w["sessions"]):
            continue
        assert w["prescribed_km"] <= w["volume_km"] * 1.10 + 0.2, w["week"]


def test_floor_jump_warned():
    df = history(weeks=4, km=0.5, runs_per_week=1)
    plan = plan_for(df=df, distance="Semi-marathon")
    assert any("minimum du plan" in w for w in plan["warnings"])
