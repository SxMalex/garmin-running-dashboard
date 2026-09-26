"""
Serveur MCP : outils « raisonnement » (insights) et liste blanche de lecture.
Aucun appel réseau : GarminClient autour du faux client de tests_ui/.
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests_ui"))

import garmin_client as gc  # noqa: E402
import goal_store  # noqa: E402
import insights  # noqa: E402
from fake_garmin import FakeGarmin  # noqa: E402
from garmin_client import GarminClient  # noqa: E402
from garminconnect import Garmin  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(gc, "API_COOLDOWN_S", 0)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    return GarminClient(FakeGarmin(), athlete_id=42)


def _json_ok(obj):
    json.dumps(obj, allow_nan=False)
    return obj


def test_daily_briefing(client):
    b = _json_ok(insights.daily_briefing(client))
    assert b["load"]["tsb"] is not None
    assert b["recovery"]["hrv_status"] == "BALANCED"
    assert b["session"]["source"] == "dashboard"
    assert b["verdict"]["label"]


def test_briefing_session_matches_dashboard_path(client):
    """Même séance que l'Accueil (todays_session), par construction."""
    from forme_logic import parse_recovery
    from next_session_logic import SESSION_TYPES, todays_session
    df = client.get_activities(limit=gc.ACTIVITY_HISTORY_LIMIT)
    cdate = date.today().isoformat()
    r = parse_recovery(client.get_hrv(cdate), client.get_sleep(cdate))
    expected = todays_session(df, r["hrv_status"], r["sleep_score"], None)["rec"]
    got = insights.daily_briefing(client)["session"]
    assert got["type"] == SESSION_TYPES[expected["session_key"]]["label"]
    assert got["target_distance_km"] == pytest.approx(expected["target_dist_km"])


def test_training_load(client):
    t = _json_ok(insights.training_load(client, days=60))
    assert t["weeks"] and {"ctl", "atl", "tsb"} <= set(t["current"])


def test_activity_analysis(client):
    first_run = client.get_activities(limit=5).iloc[0]["activityId"]
    a = _json_ok(insights.activity_analysis(client, int(first_run)))
    assert "mask" not in a["cadence_lock"]
    assert "valid" in a["decoupling"]


def test_aerobic_trend_bounded(client):
    t = _json_ok(insights.aerobic_trend(client))
    assert t["runs_analysed"] <= insights.MAX_TREND_RUNS


def test_race_plan_preview_and_errors(client):
    race = (date.today() + timedelta(weeks=10)).isoformat()
    p = _json_ok(insights.race_plan_preview(client, "10 km", race, target_time="45:00"))
    assert p["weeks"] and "Objectif : 10 km" in p["brief"]
    assert "error" in insights.race_plan_preview(client, "12 km", race)


def test_current_goal(client):
    assert insights.current_goal(client)["goal"] is None
    goal_store.save_goal(42, {"distance": "Semi-marathon",
                              "race_date": (date.today() + timedelta(weeks=12)).isoformat()},
                         {"runs_per_week": 4})
    g = _json_ok(insights.current_goal(client))
    assert g["goal"]["distance"] == "Semi-marathon" and g["plan"]["weeks"]


# ---------------------------------------------------------------------------
# Liste blanche (serveur en lecture seule)
# ---------------------------------------------------------------------------

server = pytest.importorskip("server")


def test_whitelist_blocks_every_writing_method_of_the_lib():
    """Chaque méthode autorisée ne fait que des GET : vérifié sur son code source."""
    import inspect
    import re
    public = [m for m in dir(Garmin) if not m.startswith("_") and callable(getattr(Garmin, m))]
    allowed = [m for m in public if server._is_allowed(m)]
    writes = re.compile(r"\.(post|put|delete|patch)\(|[\"'](POST|PUT|DELETE|PATCH)[\"']")
    leaks = []
    for name in allowed:
        try:
            src = inspect.getsource(getattr(Garmin, name))
        except (OSError, TypeError):
            continue
        if writes.search(src):
            leaks.append(name)
    assert leaks == [], leaks
    assert {"get_stats", "connectapi", "download_activity", "get_scheduled_workouts"} <= set(allowed)
    for name in ("query_garmin_graphql", "upload_running_workout", "delete_workout",
                 "schedule_workout", "logout", "login", "_private"):
        assert not server._is_allowed(name), name


def test_garmin_call_refuses_writes():
    with pytest.raises(ValueError, match="lecture seule"):
        server.garmin_call("delete_workout", {"workout_id": 1})


def test_new_tools_registered():
    import asyncio
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {"daily_briefing", "training_load", "activity_analysis", "aerobic_trend",
            "race_plan_preview", "current_goal", "health_watch", "running_form", "garmin_call"} <= names


def test_briefing_without_runs_does_not_crash(tmp_path, monkeypatch):
    """Revue : historique vide ou vélo seul → pas de KeyError / ValueError."""
    monkeypatch.setattr(gc, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(gc, "API_COOLDOWN_S", 0)
    api = FakeGarmin(n_runs=0)
    b = _json_ok(insights.daily_briefing(GarminClient(api, athlete_id=43)))
    assert "note" in b["session"]


def test_current_goal_returns_frozen_plan(client):
    from workout_export import plan_id_of
    goal = {"distance": "Semi-marathon",
            "race_date": (date.today() + timedelta(weeks=12)).isoformat(), "target_text": "1:45"}
    prefs = {"runs_per_week": 4}
    goal_store.save_goal(42, goal, prefs)
    from race_plan_logic import athlete_baseline, build_race_plan
    frozen = build_race_plan(date.fromisoformat(goal["race_date"]), "Semi-marathon",
                             athlete_baseline(None, date.today()), date.today())
    frozen["summary"]["marker"] = "figé"
    goal_store.validate_plan(42, plan_id_of(goal, prefs), frozen)
    g = insights.current_goal(client)
    assert g["validated"] is True and g["plan"]["summary"]["marker"] == "figé"


def test_preview_reads_hmm_for_half(client):
    race = (date.today() + timedelta(weeks=12)).isoformat()
    p = insights.race_plan_preview(client, "Semi-marathon", race, target_time="1:45")
    assert p["summary"]["target_time_s"] == 6300


def test_import_does_not_touch_data_dir(monkeypatch):
    """Revue : importer le serveur ne doit pas rediriger DATA_DIR."""
    import importlib
    monkeypatch.delenv("DATA_DIR", raising=False)
    importlib.reload(server)
    import os
    assert "DATA_DIR" not in os.environ


def test_garmin_call_refuses_request_bodies():
    with pytest.raises(ValueError, match="lecture seule"):
        server.garmin_call("connectapi", {"path": "/x", "headers": {"X-HTTP-Method-Override": "DELETE"}})


@pytest.mark.parametrize("key, value", [
    ("proxies", {"https": "http://evil.example.com"}),
    ("verify", False),
    ("files", {"f": "x"}),
    ("cookies", {"a": "b"}),
])
def test_garmin_call_refuses_dangerous_kwargs(key, value):
    """proxies/verify=False/files/cookies partiraient avec le Bearer (liste blanche)."""
    with pytest.raises(ValueError, match="lecture seule"):
        server.garmin_call("connectapi", {"path": "/x", key: value})


def test_garmin_call_connectapi_allows_path_and_params(monkeypatch):
    """Seuls path/params sont nécessaires (seul usage réel : GarminClient._connect_range)."""
    fake = FakeGarmin(n_runs=0)
    monkeypatch.setattr(server, "_get_client", lambda: fake)
    server.garmin_call("connectapi", {"path": "/x", "params": {"a": 1}})
    assert "connectapi:/x" in fake.calls


def test_health_watch_tool_is_json_and_honest(client, monkeypatch):
    import json
    from datetime import date, timedelta
    import insights
    today = date.today()
    nights = [(today - timedelta(days=i)).isoformat() for i in range(34, -1, -1)]
    monkeypatch.setattr(client, "get_sleep_range", lambda s, e: [
        {"calendarDate": d, "averageRespirationValue": 14.0 + (0.1 if i % 2 else 0)} for i, d in enumerate(nights)])
    monkeypatch.setattr(client, "get_hrv_range", lambda s, e: [])
    monkeypatch.setattr(client, "get_resting_hr_range", lambda s, e: [
        {"calendarDate": d, "restingHR": 48 + (i % 3)} for i, d in enumerate(nights)])
    out = insights.health_watch(client)
    json.dumps(out)                                   # sérialisable tel quel
    assert out["available"] and out["level"] == 0
    by = {s["signal"]: s for s in out["signals"]}
    assert by["HRV nocturne"]["status"] == "missing" and "caveat" in out
    monkeypatch.setattr(client, "get_resting_hr_range", lambda s, e: [])
    monkeypatch.setattr(client, "get_sleep_range", lambda s, e: [])
    assert insights.health_watch(client) == {"available": False,
                                              "note": "Pas de nuit mesurée ces 2 derniers jours."}


def test_running_form_tool_is_json(client):
    import json
    import insights
    out = insights.running_form(client)
    json.dumps(out)
    assert "spike" in out and "reading_guide" in out
    assert isinstance(out["form_at_equal_pace"], list)
    assert all("series" not in r for r in out["form_at_equal_pace"])
