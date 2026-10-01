"""Prompts MCP : déclarés sur le serveur, adaptés à la situation, sans chiffre inventé."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests_ui"))

import prompts  # noqa: E402

COACH = {"coach": {"name": "Semi Run Coach", "days_to_event": 40}, "goal": None, "health": None}
GOAL = {"coach": None, "health": None,
        "goal": {"distance": "10 km", "race_date": "2026-11-15", "target": "45:00", "validated": True}}
NOTHING = {"coach": None, "goal": None, "health": None}
SICK = {**NOTHING, "health": {"level": 2, "title": "Ton corps lutte peut-être contre quelque chose"}}


def test_all_prompts_are_registered():
    import server
    names = {p.name for p in asyncio.run(server.mcp.list_prompts())}
    assert {"bilan_semaine", "pourquoi_fatigue", "prepa_course", "debrief", "ajuste_plan",
            "seance_du_jour"} <= names


def test_run_coach_forbids_rewriting_sessions():
    for text in (prompts.prepa_course(COACH, "Semi-marathon", "2026-11-08"),
                 prompts.ajuste_plan(COACH, "vacances du 3 au 10")):
        assert "Semi Run Coach" in text and "Ne " in text
        assert "race_plan_preview" not in text          # pas de plan concurrent


def test_without_coach_the_plan_tools_are_used():
    text = prompts.prepa_course(NOTHING, "10 km", "2026-11-15", "45:00")
    assert "race_plan_preview(distance=« 10 km », race_date=2026-11-15, target_time=« 45:00 »)" in text
    assert "current_goal" in prompts.ajuste_plan(GOAL, "genou sensible")
    assert "validé" in prompts.bilan_semaine(GOAL)


def test_health_alert_comes_first():
    for text in (prompts.bilan_semaine(SICK), prompts.seance_du_jour(SICK)):
        assert "Veille santé" in text and "health_watch" in text


def test_level_changes_the_vocabulary():
    assert "sans jargon" in prompts.bilan_semaine(NOTHING, "debutant")
    assert "vocabulaire technique" in prompts.bilan_semaine(NOTHING, "confirme")
    assert "sans jargon" in prompts.bilan_semaine(NOTHING, "n'importe quoi")     # repli


@pytest.mark.parametrize("build", [
    lambda s: prompts.bilan_semaine(s), lambda s: prompts.pourquoi_fatigue(s),
    lambda s: prompts.debrief(s), lambda s: prompts.seance_du_jour(s)])
def test_every_prompt_forbids_invented_numbers_and_diagnosis(build):
    text = build(NOTHING)
    assert "n'invente aucun chiffre" in text and "pas médecin" in text


def test_situation_survives_a_garmin_outage(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import garmin_client as gcm
    # Cache vierge : sinon une réponse laissée par un test précédent (même athlète 42)
    # serait servie à la place de la panne (la lecture passe par le cache).
    monkeypatch.setattr(gcm, "CACHE_DIR", tmp_path / "cache")
    from fake_garmin import FakeGarmin
    from garmin_client import GarminClient
    api = FakeGarmin()
    api.plans_error = RuntimeError("Garmin down")
    sit = prompts.situation(GarminClient(api, athlete_id=42))
    assert sit["coach"] == "inconnu"
    assert "inconnu" in prompts.ajuste_plan(sit, "voyage")


def test_prompt_renders_even_without_garmin_connection():
    def broken():
        raise RuntimeError("pas de tokenstore")
    sit = prompts.safe_situation(broken)
    assert sit["coach"] == "inconnu"
    assert "Ma contrainte : voyage" in prompts.ajuste_plan(sit, "voyage")


def test_past_race_plan_is_not_announced_as_what_the_watch_follows(tmp_path, monkeypatch):
    """Revue #2 : seul plan_id était comparé ; un objectif passé restait « validé »."""
    from datetime import date, timedelta
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import goal_store
    from fake_garmin import FakeGarmin
    from garmin_client import GarminClient
    from workout_export import plan_id_of
    goal = {"distance": "10 km", "race_date": (date.today() - timedelta(days=3)).isoformat(),
            "target_text": "45:00"}
    goal_store.save_goal(42, goal, {})
    goal_store.validate_plan(42, plan_id_of(goal, {}), {"weeks": []})
    sit = prompts.situation(GarminClient(FakeGarmin(), athlete_id=42))
    assert sit["goal"]["validated"] is False and sit["goal"]["past"] is True
    text = prompts.ajuste_plan(sit, "voyage")
    assert "la montre reçoit" not in text and "terminé" in text


def test_situation_reads_the_plans_once(tmp_path, monkeypatch):
    """Contre-validation : un appel strict frais PUIS load_coach_context — deux lectures."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fake_garmin import FakeGarmin
    from garmin_client import GarminClient
    import garmin_client as gcm
    monkeypatch.setattr(gcm, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(gcm, "API_COOLDOWN_S", 0)
    calls = []
    real = GarminClient.get_training_plans
    monkeypatch.setattr(GarminClient, "get_training_plans",
                        lambda self, *a, **k: calls.append(k) or real(self, *a, **k))
    prompts.situation(GarminClient(FakeGarmin(), athlete_id=42))
    assert calls == [{"strict": True, "use_cache": True}]
