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
    # FakeGarmin enregistre une course aujourd'hui : la séance du jour est donc
    # sautée (todays_session) et l'Accueil annonce la suivante.
    first_run = next(s for s in plan_sessions(plan)
                     if s["kind"] not in ("strength",) and s["date"] > date.today().isoformat())

    home = logged_in("main.py").run()
    assert not home.exception, [e.value for e in home.exception]
    card = next(m.value for m in home.markdown if "gd-session-title" in m.value
                and "Séance du jour" in m.value)
    assert first_run["title"] in card and "plan Objectif" in card

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
    first = next(s for s in plan_sessions(plan)   # > : course déjà faite aujourd'hui
                 if s["kind"] != "strength" and s["date"] > date.today().isoformat())
    at = logged_in("5_Next_Session.py").run()
    assert not at.exception, [e.value for e in at.exception]
    assert any(first["title"] in m.value for m in at.markdown)
    assert "Allure d'ensemble" in {m.label for m in at.metric}


def test_meal_prompt_uses_the_validated_plan(logged_in, goal):
    """Audit : le prompt « Idées de repas » disait « Aucun plan actif » alors que
    l'Accueil annonçait une séance du plan Objectif validé."""
    _validated(logged_in)
    at = logged_in("7_AI_Coach.py").run()
    radio = next(r for r in at.radio if any("repas" in str(o) for o in r.options))
    radio.set_value(next(o for o in radio.options if "repas" in str(o))).run()
    assert not at.exception, [e.value for e in at.exception]
    prompt = at.code[0].value
    assert "Aucun plan d'entraînement actif" not in prompt
    assert "plan Objectif validé" in prompt and "À retenir pour la prochaine course" in prompt


def test_objectif_writes_nothing_under_a_fallback_athlete_id(logged_in, fake_api, monkeypatch):
    """Revue #1 : un 429 sur socialProfile rangeait l'objectif sous un autre dossier."""
    import garmin_client
    import goal_store
    at = logged_in("9_Objectif.py")
    for writer in ("save_goal", "clear_goal", "validate_plan", "record_push", "forget_push"):
        monkeypatch.setattr(goal_store, writer, lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("écriture sous un id de repli")))
    garmin_client._SESSION.update(athlete_id=123456, reliable=False, checked_at=10**12)
    at.session_state["garmin_athlete_id_reliable"] = False
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("identifiant de ton compte" in w.value for w in at.warning)
    # Ni formulaire ni bouton d'écriture : rien à cliquer pour ranger au mauvais endroit
    assert not [b for b in at.button if not b.key.startswith("gd-")] and not at.text_input


# ---------------------------------------------------------------------------
# Revue PR #1, lot G
# ---------------------------------------------------------------------------
from workout_export import UNVERIFIED_FINGERPRINT, workout_tag  # noqa: E402

RUN_COACH = [{"trainingPlanId": 7, "name": "Run Coach", "trainingStatus": {"statusKey": "Scheduled"}}]


def _count_plan_reads(fake_api, monkeypatch):
    calls = []
    original = fake_api.get_training_plans
    monkeypatch.setattr(fake_api, "get_training_plans",
                        lambda *a, **k: calls.append(1) or original(*a, **k))
    return calls


def test_ticking_boxes_does_not_call_garmin(logged_in, fake_api, goal, monkeypatch):
    """Revue : chaque case cochée coûtait un get_training_plans frais (0,4 s)."""
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    at = _validated(logged_in)
    calls = _count_plan_reads(fake_api, monkeypatch)
    choice = at.multiselect(key="push_choice")
    choice.set_value(choice.value[:1]).run()
    at.checkbox(key="push_confirm").check().run()
    at.checkbox(key="push_confirm").uncheck().run()
    at.checkbox(key="push_confirm").check().run()
    assert calls == []
    _button(at, "Envoyer").click().run()
    assert calls == [1]                                    # la garde relit au clic, une fois
    assert len(goal_store.load(42)["pushed"]) == 1


@pytest.mark.parametrize("change,expected", [
    ("coach", "Run Coach est actif"),
    ("down", "Impossible de vérifier"),
])
def test_guard_is_reread_at_click(logged_in, fake_api, goal, monkeypatch, change, expected):
    """Run Coach démarré (ou Garmin en panne) entre l'affichage et le clic : rien n'est envoyé."""
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    at = _validated(logged_in)
    at.checkbox(key="push_confirm").check().run()
    if change == "coach":
        fake_api.plans = RUN_COACH
    else:
        fake_api.plans_error = RuntimeError("API Error 503")
    _button(at, "Envoyer").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert "upload_workout" not in fake_api.calls
    assert goal_store.load(42)["pushed"] == {}
    assert any(expected in w.value for w in at.warning)


def test_displayed_state_is_reread_after_a_minute(logged_in, fake_api, goal, monkeypatch):
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    at = _validated(logged_in)
    fake_api.plans = RUN_COACH
    at.run()                                               # < 1 min : état mémorisé
    assert any(b.label == "📲 Envoyer" for b in at.button)
    stamp, state = at.session_state["objectif_coach_state"]
    at.session_state["objectif_coach_state"] = (stamp - 61, state)
    at.run()
    assert any("Run Coach est actif" in w.value for w in at.warning)
    assert not any(b.label == "📲 Envoyer" for b in at.button)


def _lose_journal():
    for key in list(goal_store.load(42)["pushed"]):
        goal_store.forget_push(42, key)


def _uploads(fake_api):
    return fake_api.calls.count("upload_workout")


def test_lost_journal_then_recalculated_plan_is_not_trusted(logged_in, fake_api, goal, monkeypatch):
    """Revue : séance créée (réponse perdue), plan recalculé (mêmes noms, autres allures),
    puis « Envoyer » : la montre gardait les anciennes cibles, marquées à jour."""
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    at = _push_all(_validated(logged_in))
    n_workouts, n_schedules, n_uploads = len(fake_api.workouts), len(fake_api.scheduled), _uploads(fake_api)
    _lose_journal()
    fake_api.scheduled.clear()                            # la planification n'a jamais eu lieu
    doc = goal_store.load(42)
    plan = doc["validated"]["plan"]
    changed = set()
    for w in plan["weeks"]:
        for s in w["sessions"]:
            if s["kind"] == "easy":
                for step in s["steps"]:
                    step["pace_fast"] += 3
                    step["pace_slow"] += 3
                changed.add(f"{s['date']}-{s['kind']}")
    goal_store.validate_plan(42, doc["validated"]["plan_id"], plan)
    _push_all(at.run())
    journal = goal_store.load(42)["pushed"]
    flagged = {k for k, e in journal.items() if e["fingerprint"] == UNVERIFIED_FINGERPRINT}
    assert flagged and flagged == changed & set(journal)
    assert all(e["reconciled"] for e in journal.values())
    assert len(fake_api.workouts) == n_workouts and _uploads(fake_api) == n_uploads
    assert len(fake_api.scheduled) == n_schedules - len(flagged)   # les périmées ne sont pas planifiées
    assert any("ne correspondent pas au plan affiché" in w.value for w in at.warning)
    assert any("ne correspondent plus au plan affiché" in w.value for w in at.warning)
    _button(at, "Retirer les séances de l'ancien plan").click().run()
    _push_all(at.run())
    journal = goal_store.load(42)["pushed"]
    assert all(e["fingerprint"] != UNVERIFIED_FINGERPRINT for e in journal.values())
    assert _uploads(fake_api) == n_uploads + len(flagged)
    assert len(fake_api.workouts) == n_workouts          # remplacées, pas doublées


def test_same_slot_new_title_is_found_by_tag(logged_in, fake_api, goal, monkeypatch):
    """Journal perdu, créneau passé à une autre séance (autre titre, même étiquette) :
    l'ancienne n'est pas laissée à côté de la nouvelle."""
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    at = _push_all(_validated(logged_in))
    _lose_journal()
    doc = goal_store.load(42)
    plan, plan_id = doc["validated"]["plan"], doc["validated"]["plan_id"]
    target = next(s for w in plan["weeks"] for s in w["sessions"]
                  if s["kind"] == "easy" and s["date"] >= date.today().isoformat())
    target.update(kind="shakeout", title="Déblocage")
    goal_store.validate_plan(42, plan_id, plan)
    n_uploads = _uploads(fake_api)
    _push_all(at.run())
    assert _uploads(fake_api) == n_uploads
    entry = goal_store.load(42)["pushed"][f"{target['date']}-shakeout"]
    assert entry["fingerprint"] == UNVERIFIED_FINGERPRINT and "Déblocage" not in entry["name"]
    _button(at, "Retirer les séances de l'ancien plan").click().run()
    _push_all(at.run())
    tag = workout_tag(plan_id, target)
    names = [w["workoutName"] for w in fake_api.workouts.values() if tag in w["workoutName"]]
    assert len(names) == 1 and names[0].startswith("Déblocage")


def test_lost_journal_and_session_moved_in_garmin_is_not_rescheduled(logged_in, fake_api, goal, monkeypatch):
    """Revue : séance décalée d'un jour dans Garmin Connect, journal perdu → pas de doublon."""
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    at = _push_all(_validated(logged_in))
    sid, (wid, day) = next(iter(fake_api.scheduled.items()))
    fake_api.scheduled[sid] = (wid, (date.fromisoformat(day) + timedelta(days=1)).isoformat())
    n_schedules = len(fake_api.scheduled)
    _lose_journal()
    _push_all(at.run())
    assert len(fake_api.scheduled) == n_schedules
    assert [s for s, (w, _) in fake_api.scheduled.items() if w == wid] == [sid]
    entry = next(e for e in goal_store.load(42)["pushed"].values() if e["workout_id"] == wid)
    assert entry["schedule_id"] == sid and entry["fingerprint"] != UNVERIFIED_FINGERPRINT


def test_garmin_failure_while_reattaching_a_found_session_is_shown_and_stops(logged_in, fake_api, goal,
                                                                             monkeypatch):
    """Journal perdu, séance retrouvée, mais Garmin tombe en la rattachant : erreur affichée,
    rien de recréé ni de planifié en double, la page tient."""
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    at = _push_all(_validated(logged_in))
    _lose_journal()
    uploads, scheduled = _uploads(fake_api), len(fake_api.scheduled)

    def down(year, month):
        raise RuntimeError("API Error 503 - Service Unavailable")

    monkeypatch.setattr(fake_api, "get_scheduled_workouts", down)
    _push_all(at.run())
    assert not at.exception, [e.value for e in at.exception]
    assert any("Erreur Garmin en rattachant" in e.value for e in at.error)
    assert _uploads(fake_api) == uploads and len(fake_api.scheduled) == scheduled


def test_garmin_side_session_names_are_inert_markdown(logged_in, fake_api, goal, monkeypatch):
    """Revue : le journal garde désormais le nom réel lu dans Garmin (renommable par
    l'utilisateur) — affiché tel quel, il chargeait une image distante."""
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    at = _push_all(_validated(logged_in))
    key, entry = next(iter(goal_store.load(42)["pushed"].items()))
    goal_store.record_push(42, key, {**entry, "name": "![](https://tiers.example/p.png) **x** :red[y]"})
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    shown = [m.value for m in at.markdown if "tiers" in m.value]
    assert shown and all("![](" not in v and "**x**" not in v and ":red[" not in v for v in shown), shown


def test_duplicated_tagged_session_is_reported_not_silently_ignored(logged_in, fake_api, goal, monkeypatch):
    """Revue : « Copie de … [GD-…] » dans Garmin Connect — seule la 1re trouvée était prise,
    la copie restait planifiée hors journal, sans rien dire."""
    import copy
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    at = _push_all(_validated(logged_in))
    wid, payload = next(iter(fake_api.workouts.items()))
    fake_api._next_id += 1
    dup = copy.deepcopy(payload)
    dup["workoutName"] = "Copie de " + dup["workoutName"]
    # la copie est rangée AVANT l'original : l'ancien code la prenait
    fake_api.workouts = {fake_api._next_id: dup, **fake_api.workouts}
    _lose_journal()
    uploads = _uploads(fake_api)
    _push_all(at.run())
    assert not at.exception, [e.value for e in at.exception]
    journal = goal_store.load(42)["pushed"]
    slot = next(e for e in journal.values() if e["workout_id"] in (wid, fake_api._next_id))
    assert slot["workout_id"] == wid and slot["fingerprint"] != "unverified"   # l'original vérifié
    assert any("en double" in w.value for w in at.warning)
    assert _uploads(fake_api) == uploads


_HOSTILE = RuntimeError("API Error 500 ![](https://tiers.example/p.png)")


def _inert(values):
    return values and all("![](" not in v for v in values)


def test_garmin_errors_on_every_write_path_are_shown_escaped(logged_in, fake_api, goal, monkeypatch):
    """Chemins d'erreur de la page (retrait, lecture de la bibliothèque, recherche) :
    message visible, texte Garmin échappé, page debout."""
    monkeypatch.setenv("GARMIN_WRITE_ENABLED", "true")
    at = _push_all(_validated(logged_in))
    # 1. Retrait qui échoue
    monkeypatch.setattr(fake_api, "delete_workout", lambda wid: (_ for _ in ()).throw(_HOSTILE))
    _button(at, "Retirer").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert _inert([e.value for e in at.error if "en retirant" in e.value])
    # 2. Bibliothèque illisible au moment d'envoyer
    _lose_journal()
    monkeypatch.setattr(fake_api, "get_workouts", lambda *a, **k: (_ for _ in ()).throw(_HOSTILE))
    _push_all(at.run())
    assert not at.exception, [e.value for e in at.exception]
    assert _inert([e.value for e in at.error if "impossible de lire tes séances" in e.value])
    # 3. Recherche des séances du dashboard
    _button(at, "Rechercher les séances du dashboard").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert _inert([e.value for e in at.error if "impossible de lire tes séances" in e.value])
