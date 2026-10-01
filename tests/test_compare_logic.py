"""Calendrier et comparaison de deux sorties."""

from datetime import date, timedelta

import pandas as pd
import pytest

from compare_logic import (
    compare_runs,
    day_hover,
    equivalent_pace,
    factor_rows,
    filter_kind,
    health_before,
    km_splits_frame,
    month_cells,
    months_with_activities,
    pacing_tendency,
    run_summary,
    training_block,
    with_kind,
)
from next_session_logic import compute_pmc_series


def _act(i, day, km=10.0, pace=330, kind="running", event="training", hr=150):
    return {"activityId": i, "startTimeLocal": pd.Timestamp(f"{day} 08:00:00"), "activityName": f"S{i}",
            "activityType": kind, "distance_km": km, "duration_min": km * pace / 60,
            "avgPace": "", "avgPace_sec": pace if kind == "running" else 0, "avgHR": hr,
            "maxHR": 180, "avgCadence": 176, "calories": 500, "elevationGain": 50,
            "avgSpeed_ms": 1000 / pace, "startLat": None, "startLon": None,
            "workoutType": event, "trainingLoad": 80.0, "vo2max": 50}


def _history(start=date(2026, 3, 1), days=120, km=8.0):
    rows = [_act(i, start + timedelta(days=2 * i), km=km) for i in range(days // 2)]
    return pd.DataFrame(rows)


def _splits(paces, elev=None):
    elev = elev or [0] * len(paces)
    return [{"split": i + 1, "distance_m": 1000.0, "moving_s": float(p), "elapsed_s": float(p),
             "avg_hr": 150, "elev_diff": e} for i, (p, e) in enumerate(zip(paces, elev))]


def test_kind_filter():
    df = pd.DataFrame([_act(1, "2026-09-01", event="race"), _act(2, "2026-09-02"),
                       _act(3, "2026-09-03", kind="cycling", event="uncategorized")])
    assert filter_kind(df, "race")["activityId"].tolist() == [1]
    assert filter_kind(df, "training")["activityId"].tolist() == [2]
    assert sorted(filter_kind(df, "both")["activityId"]) == [1, 2]        # jamais le vélo


def test_month_cells_grid_and_main_activity():
    df = with_kind(pd.DataFrame([_act(1, "2026-09-01", km=5), _act(2, "2026-09-01", km=10, event="race"),
                                 _act(3, "2026-09-01", km=15), _act(4, "2026-09-30")]))
    cells = month_cells(df, 2026, 9)
    assert len(cells) == 30
    first = cells.iloc[0]
    assert first["weekday"] == 1 and first["week"] == 0              # 1er sept. 2026 = mardi
    assert first["main_id"] == 2 and first["kind"] == "race" and first["km"] == 30
    assert cells.iloc[-1]["week"] == 4
    assert cells.iloc[1]["kind"] == "none" and pd.isna(cells.iloc[1]["main_id"])


def test_months_navigation_includes_gaps():
    df = pd.DataFrame([_act(1, "2026-06-15"), _act(2, "2026-09-02")])
    assert months_with_activities(df) == [(2026, 9), (2026, 8), (2026, 7), (2026, 6)]
    assert months_with_activities(df.iloc[0:0]) == []


def test_grade_adjusted_pace_removes_the_hill():
    flat = km_splits_frame(_splits([300, 300]))
    hilly = km_splits_frame(_splits([300, 360], elev=[0, 50]))
    assert flat["gap_s"].tolist() == [300, 300]
    assert hilly["gap_s"].iloc[1] < 330                              # la côte explique l'essentiel
    assert km_splits_frame([]).empty


def test_run_summary_corrects_heat_and_measures_the_fade():
    row = _act(1, "2026-07-01", km=6, pace=320)
    cool = run_summary(row, _splits([310, 315, 320, 325, 330, 330]))
    hot = run_summary(row, _splits([310, 315, 320, 325, 330, 330]),
                      {"temp_c": 30, "dewpoint_c": 20})
    assert cool["adjusted_s"] == pytest.approx(cool["gap_s"])
    assert hot["adjusted_s"] < cool["adjusted_s"] * 0.97             # ~5 % de pénalité retirée
    assert cool["split_pct"] > 2                                     # ralentissement en 2e moitié
    assert run_summary(row)["split_pct"] is None                     # pas de splits : pas d'avis


def test_riegel_projection():
    assert equivalent_pace(300, 10, 10) == 300
    assert equivalent_pace(300, 21.1, 10) < 300                      # un semi → allure 10 km plus vive


def test_training_block_uses_the_single_tsb_and_the_eve():
    df = _history()
    pmc = compute_pmc_series(df, 300)
    day = date(2026, 6, 1)
    block = training_block(df, pmc, day, 300)
    eve = pmc[pmc["date"] == pd.Timestamp(day - timedelta(days=1))].iloc[0]
    assert block["ctl"] == round(float(eve["ctl"]), 1)
    assert block["tsb"] == round(block["ctl"] - block["atl"], 1)
    assert block["runs"] == 21 and block["km_week"] == pytest.approx(21 * 8 / 6)
    assert block["longest_km"] == 8
    empty = training_block(df, pmc, date(2020, 1, 1), 300)
    assert empty["runs"] == 0 and empty["ctl"] is None


def test_health_before_includes_the_race_night_only():
    idx = pd.date_range("2026-05-20", "2026-06-05", freq="D")
    frame = pd.DataFrame({"hrv": 50.0, "rhr": 48.0}, index=idx)
    frame.loc[pd.Timestamp("2026-06-02"), "hrv"] = 500                # après la sortie : ignoré
    sleep = [{"calendarDate": d.date().isoformat(), "sleepTimeSeconds": 7 * 3600} for d in idx]
    h = health_before(frame, sleep, date(2026, 6, 1))
    assert h == {"hrv": 50.0, "rhr": 48.0, "sleep_h": 7.0}
    assert health_before(None, None, date(2026, 6, 1)) == {"hrv": None, "rhr": None, "sleep_h": None}


def _side(day, pace, km=10, block=None, health=None, kind="race", weather=None):
    row = _act(1, day, km=km, pace=pace, event=kind)
    return {"run": run_summary(row, None, weather), "block": block or {}, "health": health or {}}


def test_verdict_orders_chronologically_and_lists_what_changed():
    a = _side("2026-03-01", 300, block={"km_week": 30, "ctl": 40, "tsb": -5},
              health={"sleep_h": 6.5, "hrv": 50})
    b = _side("2026-09-01", 288, block={"km_week": 40, "ctl": 52, "tsb": 6},
              health={"sleep_h": 6.6, "hrv": 51})
    for x, y in ((a, b), (b, a)):                                    # l'ordre d'appel est indifférent
        v = compare_runs(x, y)
        assert v["status"] == "good" and v["delta_pct"] == pytest.approx(-4)
        joined = " ".join(v["factors"])
        assert "Volume" in joined and "CTL" in joined and "TSB" in joined
        assert "Sommeil" not in joined and "HRV" not in joined       # sous les seuils
        assert any("ne prouvent rien" in n for n in v["notes"])


def test_verdict_same_level_and_missing_data():
    assert compare_runs(_side("2026-03-01", 300), _side("2026-04-01", 301))["status"] == "neutral"
    rows = factor_rows({"block": {"ctl": 40}}, {"block": {"ctl": None}})
    ctl = next(r for r in rows if r["key"] == "ctl")
    assert not ctl["notable"] and ctl["b"] == "—"


def test_verdict_projects_different_distances_and_flags_mixed_kinds():
    v = compare_runs(_side("2026-03-01", 300, km=10), _side("2026-04-01", 315, km=21.1, kind="training"))
    assert any("Riegel" in n for n in v["notes"])
    assert any("entraînement" in n for n in v["notes"])
    assert v["delta_pct"] < 5                                         # 315 au semi ≈ 301 au 10 km


def test_heat_note_when_conditions_differ():
    v = compare_runs(_side("2026-03-01", 300, weather={"temp_c": 10, "dewpoint_c": 2}),
                     _side("2026-07-01", 310, weather={"temp_c": 30, "dewpoint_c": 20}))
    assert any("Chaleur" in n for n in v["notes"])
    assert v["status"] == "good"                                     # plus lente brute, plus rapide corrigée


def test_pacing_tendency():
    assert pacing_tendency([None, 3.0]) is None
    assert pacing_tendency([3.0, 4.0])["status"] == "warning"
    assert "progressive" in pacing_tendency([-2.0, -1.5])["advice"]
    assert pacing_tendency([0.5, -0.2])["status"] == "good"


def test_day_hover_lists_every_run_and_escapes_names():
    acts = with_kind(pd.DataFrame([_act(1, "2026-09-20", km=21.1, pace=300, event="race"),
                                   _act(2, "2026-09-20", km=3, pace=360)]))
    acts.loc[1, "activityName"] = "<img src=x onerror=alert(1)>"
    text = day_hover(acts, date(2026, 9, 20))
    assert text.startswith("<b>Dim 20/09/2026</b>")
    assert "Course · S1" in text and "21.1 km · 1:45:30 · 5:00/km · 150 bpm" in text
    assert "18:00 · 6:00/km" in text                                  # durée sous l'heure en m:ss
    assert "<img" not in text and "&lt;img" in text
    cells = month_cells(acts, 2026, 9)
    assert cells.loc[19, "hover"] == text and cells.loc[0, "hover"] == ""
