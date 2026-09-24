"""Parcours de la page Objectif : objectif → plan → validation → envoi → retrait."""

from datetime import date, timedelta

import pytest

import goal_store

PAGE = "9_Objectif.py"


@pytest.fixture
def goal():
    goal_store.save_goal(42, {"distance": "10 km",
                              "race_date": (date.today() + timedelta(weeks=8)).isoformat(),
                              "target_text": ""},
                         {"runs_per_week": 4, "long_run_weekday": 6, "include_strength": True})


def _button(at, label):
    return next(b for b in at.button if label in b.label)


def _validated(logged_in):
    at = logged_in(PAGE).run()
    _button(at, "Valider ce plan").click().run()
    return at


def test_plan_rendered_with_why(logged_in, goal):
    at = logged_in(PAGE).run()
    assert not at.exception, [e.value for e in at.exception]
    labels = " | ".join(m.label for m in at.metric)
    assert all(k in labels for k in ("Semaines", "Volume", "Temps estimé", "Allure course"))
    assert any(c.value.startswith("Pourquoi :") for c in at.caption)
    assert any("Renfo" in m.value for m in at.markdown)


def test_validate_then_push_then_no_duplicates(logged_in, fake_api, goal, monkeypatch):
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    at = _validated(logged_in)
    assert any("Plan validé" in s.value for s in at.success)
    at.checkbox(key="push_confirm").check().run()
    _button(at, "Envoyer").click().run()
    assert not at.exception, [e.value for e in at.exception]
    pushed = goal_store.load(42)["pushed"]
    assert pushed and len(fake_api.scheduled) == len(pushed)
    assert all("[GD-" in e["name"] for e in pushed.values())
    # Tout est déjà envoyé : plus rien à pousser, rien de recréé.
    assert any("Rien à envoyer" in c.value for c in at.caption)
    n_workouts = len(fake_api.workouts)
    at.run()
    assert len(fake_api.workouts) == n_workouts


def test_lost_journal_does_not_duplicate(logged_in, fake_api, goal, monkeypatch):
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    at = _validated(logged_in)
    at.checkbox(key="push_confirm").check().run()
    _button(at, "Envoyer").click().run()
    first = len(fake_api.workouts)
    for key in list(goal_store.load(42)["pushed"]):
        goal_store.forget_push(42, key)            # journal local perdu
    at.run()
    at.checkbox(key="push_confirm").check().run()
    _button(at, "Envoyer").click().run()
    assert len(fake_api.workouts) == first        # réconciliation par étiquette


def test_failed_schedule_leaves_no_orphan(logged_in, fake_api, goal, monkeypatch):
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    at = _validated(logged_in)
    fake_api._library()
    fake_api.schedule_error = RuntimeError("429 Too Many Requests")
    at.checkbox(key="push_confirm").check().run()
    _button(at, "Envoyer").click().run()
    assert fake_api.workouts == {}
    assert goal_store.load(42)["pushed"] == {}


@pytest.mark.parametrize("setup,expected", [
    ("coach", "Run Coach est actif"),
    ("unknown", "Impossible de vérifier"),
    ("disabled", "Écriture Garmin désactivée"),
])
def test_push_blocked(logged_in, fake_api, goal, monkeypatch, setup, expected):
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "false" if setup == "disabled" else "true")
    if setup == "coach":
        fake_api.plans = [{"trainingPlanId": 7, "name": "Semi Run Coach",
                           "trainingStatus": {"statusKey": "Scheduled"}}]
    if setup == "unknown":
        fake_api.plans_error = RuntimeError("503")
    at = _validated(logged_in)
    assert any(expected in w.value for w in at.warning)
    assert not any("Envoyer" in b.label for b in at.button)


def test_remove_pushed(logged_in, fake_api, goal, monkeypatch):
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    at = _validated(logged_in)
    at.checkbox(key="push_confirm").check().run()
    _button(at, "Envoyer").click().run()
    assert fake_api.workouts
    _button(at, "Retirer").click().run()
    assert fake_api.workouts == {} and fake_api.scheduled == {}
    assert goal_store.load(42)["pushed"] == {}


def test_goal_form_saves(logged_in):
    at = logged_in(PAGE).run()
    at.selectbox(key="goal_distance").set_value("5 km")
    at.text_input(key="goal_target").input("22:30")
    next(b for b in at.button if "Enregistrer" in b.label).click().run()
    assert goal_store.load(42)["goal"]["distance"] == "5 km"
    assert not at.exception


def test_bad_target_time_rejected(logged_in):
    at = logged_in(PAGE).run()
    at.text_input(key="goal_target").input("vite")
    next(b for b in at.button if "Enregistrer" in b.label).click().run()
    assert any("illisible" in e.value for e in at.error)
    assert "goal" not in goal_store.load(42)


# ---------------------------------------------------------------------------
# Revue P2
# ---------------------------------------------------------------------------

def _push_all(at):
    at.checkbox(key="push_confirm").check().run()
    _button(at, "Envoyer").click().run()
    return at


def test_push_error_stays_visible(logged_in, fake_api, goal, monkeypatch):
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    at = _validated(logged_in)
    fake_api._library()
    fake_api.schedule_error = RuntimeError("API Error 429 - Too Many Requests")
    _push_all(at)
    assert any("Erreur Garmin" in e.value for e in at.error)


def test_reconciliation_rebuilds_journal(logged_in, fake_api, goal, monkeypatch):
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    at = _push_all(_validated(logged_in))
    n = len(goal_store.load(42)["pushed"])
    for key in list(goal_store.load(42)["pushed"]):
        goal_store.forget_push(42, key)
    _push_all(at.run())
    journal = goal_store.load(42)["pushed"]
    assert len(journal) == n and all(e.get("reconciled") for e in journal.values())
    assert any("déjà présente" in i.value for i in at.info)


def test_coach_guard_reads_fresh_plans(logged_in, fake_api, goal, monkeypatch):
    """Un plan Run Coach démarré après la mise en cache doit bloquer l'envoi."""
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    logged_in("main.py").run()                       # remplit le cache sans plan
    fake_api.plans = [{"trainingPlanId": 7, "name": "Run Coach",
                       "trainingStatus": {"statusKey": "Scheduled"}}]
    at = _validated(logged_in)
    assert any("Run Coach est actif" in w.value for w in at.warning)


def test_changed_plan_requires_removing_old_sessions(logged_in, fake_api, goal, monkeypatch):
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    _push_all(_validated(logged_in))
    doc = goal_store.load(42)
    goal_store.save_goal(42, doc["goal"], {**doc["prefs"], "runs_per_week": 5})
    at = _validated(logged_in)
    assert any("ancien plan" in w.value for w in at.warning)
    assert not any(b.label == "📲 Envoyer" for b in at.button)
    _button(at, "Retirer les séances de l'ancien plan").click().run()
    assert fake_api.workouts == {}
    at.run()
    assert any(b.label == "📲 Envoyer" for b in at.button)


def test_frozen_plan_is_what_is_shown(logged_in, goal):
    at = _validated(logged_in)
    doc = goal_store.load(42)
    first = doc["validated"]["plan"]["weeks"][0]["sessions"][0]
    first["title"] = "Séance figée témoin"
    goal_store.validate_plan(42, doc["validated"]["plan_id"], doc["validated"]["plan"])
    at.run()
    assert any("Séance figée témoin" in m.value for m in at.markdown)


def test_race_day_passed_does_not_crash(logged_in):
    goal_store.save_goal(42, {"distance": "10 km",
                              "race_date": (date.today() - timedelta(days=3)).isoformat(),
                              "target_text": ""}, {"runs_per_week": 4})
    at = logged_in(PAGE).run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("passée" in i.value for i in at.info)
    _button(at, "Nouvel objectif").click().run()
    assert "goal" not in goal_store.load(42)


def test_remove_refuses_non_dashboard_workout(logged_in, fake_api, goal, monkeypatch):
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    at = _push_all(_validated(logged_in))
    wid = next(iter(fake_api.workouts))
    fake_api.workouts[wid] = {"workoutName": "Ma séance perso"}   # journal pointant ailleurs
    _button(at, "Retirer").click().run()
    assert wid in fake_api.workouts
    assert any("n'a pas été retirée" in w.value for w in at.warning)


def test_implausible_target_rejected_at_save(logged_in):
    at = logged_in(PAGE).run()
    at.selectbox(key="goal_distance").set_value("Semi-marathon")
    at.text_input(key="goal_target").input("0:01:45")
    next(b for b in at.button if "Enregistrer" in b.label).click().run()
    assert any("invraisemblable" in e.value for e in at.error)



# ---------------------------------------------------------------------------
# Revue groupée (P2 corrigé)
# ---------------------------------------------------------------------------

def test_recalculate_after_push_flags_changed_sessions(logged_in, fake_api, goal, monkeypatch):
    """Revue : « Recalculer » ne doit pas mélanger deux versions du plan sur la montre."""
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    at = _push_all(_validated(logged_in))
    doc = goal_store.load(42)
    plan = doc["validated"]["plan"]
    for w in plan["weeks"]:                       # simule un recalcul qui change les séances
        for sess in w["sessions"]:
            if sess["kind"] == "easy":
                sess["distance_km"] += 2
                sess["duration_min"] += 12
    goal_store.validate_plan(42, doc["validated"]["plan_id"], plan)
    at.run()
    assert any("ne correspondent plus" in w.value for w in at.warning)
    assert not any(b.label == "📲 Envoyer" for b in at.button)


def test_reconciliation_schedules_unscheduled_workout(logged_in, fake_api, goal, monkeypatch):
    """Revue : séance créée mais jamais planifiée → planifiée au rattachement."""
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    at = _push_all(_validated(logged_in))
    n = len(fake_api.scheduled)
    for key in list(goal_store.load(42)["pushed"]):
        goal_store.forget_push(42, key)
    fake_api.scheduled.clear()                    # la planification n'a jamais eu lieu
    _push_all(at.run())
    assert len(fake_api.scheduled) == n
    assert all(e["schedule_id"] for e in goal_store.load(42)["pushed"].values())


def test_open_tab_until_race_day_does_not_crash(logged_in, goal):
    at = logged_in(PAGE, goal_date=date.today()).run()
    assert not at.exception, [e.value for e in at.exception]


def test_find_orphan_dashboard_workouts(logged_in, fake_api, goal, monkeypatch):
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    at = _push_all(_validated(logged_in))
    n = len(fake_api.workouts)
    for key in list(goal_store.load(42)["pushed"]):
        goal_store.forget_push(42, key)            # journal perdu
    fake_api.workouts[99999] = {"workoutName": "Footing du club"}  # séance perso : intouchable
    at.run()
    _button(at, "Rechercher les séances du dashboard").click().run()
    assert any(f"{n} séance(s) du dashboard" in w.value for w in at.warning)
    _button(at, "Retirer ces séances retrouvées").click().run()
    assert list(fake_api.workouts) == [99999]


def test_home_and_mcp_announce_the_validated_plan_session(logged_in, fake_api, goal):
    """Contre-validation : après validation, Accueil et MCP = séance du plan (et de la montre)."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "garmin_mcp"))
    import insights
    from garmin_client import GarminClient
    from race_plan_logic import plan_sessions

    _validated(logged_in)
    plan = goal_store.load(42)["validated"]["plan"]
    first_run = next(s for s in plan_sessions(plan)
                     if s["kind"] not in ("strength",) and s["date"] >= date.today().isoformat())

    home = logged_in("main.py").run()
    assert not home.exception, [e.value for e in home.exception]
    html = " ".join(m.value for m in home.markdown)
    assert first_run["title"] in html and "ton plan Objectif" in html

    brief = insights.daily_briefing(GarminClient(fake_api, athlete_id=42))
    assert brief["session"]["source"] == "plan_objectif"
    assert brief["session"]["name"] == first_run["title"]



def test_run_coach_banner_before_validation(logged_in, fake_api, goal):
    """Contre-validation D : Run Coach actif signalé avant même la validation."""
    fake_api.plans = [{"trainingPlanId": 7, "name": "Semi Run Coach",
                       "trainingStatus": {"statusKey": "Scheduled"}}]
    at = logged_in(PAGE).run()
    assert any("Run Coach actif" in i.value for i in at.info)
    assert any("Valider ce plan" in b.label for b in at.button)


def test_next_session_page_shows_plan_session(logged_in, goal):
    """Revue : la page Prochaine sortie affichait un libellé générique et l'allure d'échauffement."""
    from race_plan_logic import plan_sessions
    _validated(logged_in)
    plan = goal_store.load(42)["validated"]["plan"]
    first = next(s for s in plan_sessions(plan)
                 if s["kind"] != "strength" and s["date"] >= date.today().isoformat())
    at = logged_in("5_Next_Session.py").run()
    assert not at.exception, [e.value for e in at.exception]
    assert any(first["title"] in m.value for m in at.markdown)
    assert "Allure d'ensemble" in {m.label for m in at.metric}
