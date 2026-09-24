"""Export des séances au format Garmin, garde du push, persistance du journal."""

from datetime import date, timedelta

import pytest

import goal_store
from race_plan_logic import athlete_baseline, build_race_plan, plan_sessions
from workout_export import (
    coach_state,
    is_dashboard_workout,
    plan_id_of,
    push_gate,
    pushable_sessions,
    session_key,
    workout_name,
    workout_payload,
)
import pandas as pd

TODAY = date(2026, 9, 24)


@pytest.fixture
def sessions():
    base = athlete_baseline(pd.DataFrame(), TODAY)
    plan = build_race_plan(TODAY + timedelta(weeks=10), "10 km", base, TODAY, runs_per_week=5)
    return plan_sessions(plan)


def _first(sessions, kind):
    return next(s for s in sessions if s["kind"] == kind)


class TestPayload:
    def test_interval_structure(self, sessions):
        p = workout_payload(_first(sessions, "interval"), "abc123")
        assert p["sportType"]["sportTypeKey"] == "running"
        steps = p["workoutSegments"][0]["workoutSteps"]
        assert [s["type"] for s in steps] == ["ExecutableStepDTO", "RepeatGroupDTO", "ExecutableStepDTO"]
        assert [s["stepType"]["stepTypeKey"] for s in steps] == ["warmup", "repeat", "cooldown"]
        repeat = steps[1]
        assert repeat["numberOfIterations"] >= 2
        assert repeat["endConditionValue"] == float(repeat["numberOfIterations"])
        work, rec = repeat["workoutSteps"]
        assert work["targetType"]["workoutTargetTypeKey"] == "pace.zone"
        assert work["targetValueOne"] < work["targetValueTwo"]  # m/s : lente < rapide
        assert rec["targetType"]["workoutTargetTypeKey"] == "no.target"
        orders = [steps[0]["stepOrder"], repeat["stepOrder"], work["stepOrder"],
                  rec["stepOrder"], steps[2]["stepOrder"]]
        assert orders == sorted(orders) and len(set(orders)) == 5

    def test_pace_zone_values(self, sessions):
        s = _first(sessions, "easy")
        step = workout_payload(s, "x")["workoutSegments"][0]["workoutSteps"][0]
        assert step["targetValueOne"] == pytest.approx(1000 / s["steps"][0]["pace_slow"], abs=1e-3)

    def test_strength_uses_strength_sport_and_lap_button(self, sessions):
        p = workout_payload(_first(sessions, "strength"), "x")
        assert p["sportType"]["sportTypeId"] == 5
        step = p["workoutSegments"][0]["workoutSteps"][0]
        assert step["endCondition"]["conditionTypeKey"] == "lap.button"

    def test_race_not_exportable(self, sessions):
        with pytest.raises(ValueError):
            workout_payload(_first(sessions, "race"), "x")

    def test_name_tagged_and_bounded(self, sessions):
        s = _first(sessions, "tempo")
        name = workout_name("abc123", s)
        assert len(name) <= 60 and "[GD-abc123-" in name
        assert is_dashboard_workout({"workoutName": name}, "abc123")
        assert not is_dashboard_workout({"workoutName": name}, "zzz999")
        assert not is_dashboard_workout({"workoutName": "Footing du club"})

    def test_all_plan_sessions_exportable_or_race(self, sessions):
        for s in sessions:
            if s["kind"] != "race":
                workout_payload(s, "x")

    def test_plan_id_stable(self):
        a = plan_id_of({"d": "10 km"}, {"runs": 4})
        assert a == plan_id_of({"d": "10 km"}, {"runs": 4})
        assert a != plan_id_of({"d": "10 km"}, {"runs": 5})


class TestGate:
    def test_states(self):
        active = {"trainingPlanList": [{"trainingPlanId": 1,
                                        "trainingStatus": {"statusKey": "Scheduled"}}]}
        assert coach_state(active) == "active"
        assert coach_state({"trainingPlanList": []}) == "none"
        assert coach_state({}, error=RuntimeError("429")) == "unknown"

    @pytest.mark.parametrize("enabled,state,allowed", [
        (True, "none", True), (False, "none", False),
        (True, "active", False), (True, "unknown", False),
    ])
    def test_gate(self, enabled, state, allowed):
        ok, reason = push_gate(enabled, state)
        assert ok is allowed
        assert bool(reason) is (not allowed)

    def test_pushable_window_and_dedup(self, sessions):
        picked = pushable_sessions(sessions, TODAY.isoformat(), {})
        assert picked
        assert all(TODAY <= date.fromisoformat(s["date"]) <= TODAY + timedelta(days=14)
                   for s in picked)
        assert all(s["kind"] != "race" for s in picked)
        first = picked[0]
        pushed = {session_key(first): {"date": first["date"], "kind": first["kind"]}}
        again = pushable_sessions(sessions, TODAY.isoformat(), pushed)
        assert session_key(first) not in {session_key(s) for s in again}

    def test_dedup_by_slot_when_plan_changes(self, sessions):
        """Revue P2 : même jour, type changé après recalcul → pas de 2e séance."""
        run = next(s for s in sessions if s["kind"] == "tempo")
        pushed = {"x": {"date": run["date"], "kind": "tempo"}}
        changed = [dict(run, kind="strides")]
        assert pushable_sessions(changed, run["date"], pushed) == []
        strength_same_day = [dict(run, kind="strength")]
        assert pushable_sessions(strength_same_day, run["date"], pushed)

    def test_past_sessions_never_pushed(self, sessions):
        later = (TODAY + timedelta(days=20)).isoformat()
        assert all(s["date"] >= later for s in pushable_sessions(sessions, later, {}))

    def test_stale_and_future_pushes(self):
        from workout_export import future_pushes, stale_pushes
        pushed = {"a": {"date": "2026-09-20", "plan_id": "old"},
                  "b": {"date": "2026-09-30", "plan_id": "old"},
                  "c": {"date": "2026-09-30", "plan_id": "new"}}
        assert set(stale_pushes(pushed, "new", "2026-09-24")) == {"b"}
        assert set(future_pushes(pushed, "2026-09-24")) == {"b", "c"}


class TestGoalStore:
    @pytest.fixture(autouse=True)
    def _data_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))

    def test_roundtrip_and_journal_survives_goal_change(self):
        goal_store.save_goal(1, {"distance": "10 km"}, {"runs": 4})
        goal_store.record_push(1, "2026-10-01-tempo", {"workout_id": 5, "schedule_id": 9})
        goal_store.save_goal(1, {"distance": "Semi-marathon"}, {"runs": 4})
        doc = goal_store.load(1)
        assert doc["goal"]["distance"] == "Semi-marathon"
        assert doc["pushed"]["2026-10-01-tempo"]["workout_id"] == 5

    def test_isolated_by_athlete_and_empty_default(self):
        goal_store.save_goal(1, {"distance": "10 km"}, {})
        assert goal_store.load(2) == {}

    def test_corrupt_file_set_aside_and_flagged(self, tmp_path):
        """Revue P2 : un journal tronqué n'est plus réécrit en silence."""
        path = tmp_path / "3" / "goal.json"
        path.parent.mkdir()
        path.write_text("{pas du json")
        assert goal_store.load(3) == {"recovered_from_corrupt": True}
        assert not list(path.parent.glob("goal.json.bad-*"))   # une lecture ne modifie rien
        doc = goal_store.save_goal(3, {"distance": "10 km"}, {})
        assert list(path.parent.glob("goal.json.bad-*"))       # l'écriture, sous verrou
        assert "recovered_from_corrupt" not in doc

    def test_non_utf8_is_corrupt_not_crash(self, tmp_path):
        path = tmp_path / "4" / "goal.json"
        path.parent.mkdir()
        path.write_bytes(b"\xff\xfe\x00garbage")
        assert goal_store.load(4) == {"recovered_from_corrupt": True}

    def test_other_io_errors_propagate(self, tmp_path, monkeypatch):
        """Revue : EACCES/EMFILE ne doivent pas être pris pour une corruption."""
        goal_store.save_goal(5, {"distance": "10 km"}, {})
        import builtins
        real_open = builtins.open

        def denied(path, *a, **k):
            if str(path).endswith("5/goal.json"):
                raise PermissionError("EACCES")
            return real_open(path, *a, **k)

        monkeypatch.setattr(builtins, "open", denied)
        with pytest.raises(PermissionError):
            goal_store.load(5)

    def test_concurrent_writers_lose_nothing(self):
        """Revue P2 : 2 threads × 100 record_push → 200 entrées."""
        import threading
        errors = []

        def worker(n):
            try:
                for i in range(100):
                    goal_store.record_push(9, f"{n}-{i}", {"workout_id": i})
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert len(goal_store.load(9)["pushed"]) == 200

    def test_forget_and_clear(self):
        goal_store.save_goal(1, {"distance": "10 km"}, {})
        goal_store.validate_plan(1, "abc", {"weeks": [], "summary": {}})
        assert goal_store.load(1)["validated"]["plan"] == {"weeks": [], "summary": {}}
        goal_store.record_push(1, "k", {"workout_id": 1})
        goal_store.forget_push(1, "k")
        doc = goal_store.clear_goal(1)
        assert "goal" not in doc and "validated" not in doc and doc["pushed"] == {}

    def test_lock_is_reentrant(self):
        with goal_store.locked(1):
            goal_store.record_push(1, "k", {"workout_id": 1})   # ne se bloque pas
        assert goal_store.load(1)["pushed"]["k"]["workout_id"] == 1

    def test_no_tmp_left(self, tmp_path):
        goal_store.save_goal(1, {}, {})
        assert not list(tmp_path.rglob("*.tmp"))
