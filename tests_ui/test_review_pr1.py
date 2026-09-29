"""Retours de revue de la PR #1 : un test par défaut signalé, qui échouait avant la correction."""

import garmin_client


def _unreliable(at):
    garmin_client._SESSION.update(athlete_id=123456, reliable=False, checked_at=10**12)
    at.session_state["garmin_athlete_id_reliable"] = False


def test_pages_do_not_pretend_to_follow_a_plan_under_a_fallback_id(logged_in, monkeypatch):
    """Sous un id de repli, le plan Objectif n'est pas lu : un avis le dit, au lieu d'un silence."""
    import goal_store
    calls = []
    monkeypatch.setattr(goal_store, "validated_sessions", lambda *a, **k: calls.append(a) or None)
    # (Coach IA : le plan n'est lu qu'en mode « Idées de repas »)
    for page, state in (("main.py", {}), ("5_Next_Session.py", {}),
                        ("7_AI_Coach.py", {"ai_coach_prompt_kind": "🍽️ Idées de repas"})):
        at = logged_in(page, **state)                          # (pré-run de l'Accueil, id fiable)
        _unreliable(at)
        calls.clear()
        at.run()
        assert not at.exception, (page, [e.value for e in at.exception])
        assert any("Plan Objectif non lu" in c.value for c in at.caption), page
        assert calls == [], page                              # jamais lu dans le mauvais dossier


def test_reliable_id_reads_the_plan_without_notice(logged_in, monkeypatch):
    import goal_store
    calls = []
    monkeypatch.setattr(goal_store, "validated_sessions", lambda *a, **k: calls.append(a) or None)
    at = logged_in("main.py").run()
    assert not at.exception
    assert calls and not any("Plan Objectif non lu" in c.value for c in at.caption)


def test_activities_without_chart_data_still_renders(logged_in, fake_api):
    """Chemin « aucune sortie ne porte cette donnée » : pas de graphe, pas d'empreinte — la page tient."""
    for act in fake_api.activities:
        act["elevationGain"] = 0.0                              # aucun D+ : l'indicateur est vide
    at = logged_in("1_Activities.py", act_metric="denivele").run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("ne porte cette donnée" in i.value for i in at.info)


def test_session_type_colours_are_current_theme_tokens():
    """Lot U : l'ancien violet (2,84:1) et l'ancien aqua traçaient encore le parcours de Prochaine sortie."""
    import chart_theme as ct
    from next_session_logic import SESSION_TYPES
    for key, spec in SESSION_TYPES.items():
        assert spec["color"].lower() in [c.lower() for c in ct.CAT], (key, spec["color"])


def test_next_session_says_when_run_coach_state_is_unknown(logged_in, monkeypatch):
    """Lot L : pendant une panne, l'Accueil prévenait, pas Prochaine sortie."""
    import ui_helpers
    from coach_logic import COACH_UNKNOWN
    monkeypatch.setattr(ui_helpers, "cached_coach_context", lambda athlete_id, cdate=None: COACH_UNKNOWN)
    at = logged_in("5_Next_Session.py").run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("Garmin n'a pas répondu sur ton plan Run Coach" in i.value for i in at.info)


def test_meal_prompt_does_not_fall_back_to_the_objectif_plan_during_an_outage(logged_in, monkeypatch):
    """Lot L : les idées de repas retombaient sur le plan Objectif quand Run Coach était inconnu."""
    from datetime import date, timedelta
    import goal_store
    import ui_helpers
    from coach_logic import COACH_UNKNOWN
    monkeypatch.setattr(ui_helpers, "cached_coach_context", lambda athlete_id, cdate=None: COACH_UNKNOWN)
    monkeypatch.setattr(goal_store, "validated_sessions", lambda *a, **k: [
        {"date": (date.today() + timedelta(days=1)).isoformat(), "kind": "tempo", "title": "Tempo",
         "distance_km": 10}])
    at = logged_in("7_AI_Coach.py", ai_coach_prompt_kind="🍽️ Idées de repas").run()
    assert not at.exception, [e.value for e in at.exception]
    prompt = at.code[0].value
    assert "État du plan Garmin Run Coach inconnu" in prompt and "plan Objectif validé" not in prompt


def test_next_session_page_with_an_undated_run_does_not_crash(logged_in, fake_api):
    """Revue : 3 courses dont une sans heure de départ → todays_session rend rec=None, la page plantait."""
    runs = [a for a in fake_api.activities if a["activityType"]["typeKey"] == "running"][:3]
    runs[0]["startTimeLocal"] = None
    fake_api.activities = runs
    at = logged_in("5_Next_Session.py").run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("courses datées" in w.value for w in at.warning)


def test_a_bug_reading_the_coach_plan_is_not_disguised_as_an_outage(logged_in, monkeypatch):
    """Revue : un KeyError de lecture devenait « Garmin n'a pas répondu » pour toujours."""
    import pytest
    import ui_helpers

    def broken(*a, **k):
        raise KeyError("taskList")
    monkeypatch.setattr(ui_helpers, "_cached_coach_context_impl", broken)
    with pytest.raises(KeyError):
        ui_helpers.cached_coach_context(42, "2026-09-29")

    def down(*a, **k):
        raise RuntimeError("API Error 503")
    monkeypatch.setattr(ui_helpers, "_cached_coach_context_impl", down)
    from coach_logic import coach_unknown
    assert coach_unknown(ui_helpers.cached_coach_context(42, "2026-09-29"))


def test_next_session_with_too_few_runs_says_so(logged_in, fake_api):
    fake_api.activities = [a for a in fake_api.activities if a["activityType"]["typeKey"] == "running"][:2]
    at = logged_in("5_Next_Session.py").run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("au moins 3 courses" in w.value for w in at.warning)


def test_ai_coach_slot_during_a_run_coach_outage(logged_in, monkeypatch):
    """Intégration : le créneau du Coach IA appelait goal_store supprimé (NameError), et un
    état Run Coach inconnu aurait annoncé le plan Objectif à sa place."""
    from datetime import date, time, timedelta
    import goal_store
    import ui_helpers
    from coach_logic import COACH_UNKNOWN
    slot = date.today() + timedelta(days=1)
    monkeypatch.setattr(ui_helpers, "cached_coach_context", lambda athlete_id, cdate=None: COACH_UNKNOWN)
    monkeypatch.setattr(goal_store, "validated_sessions", lambda *a, **k: [
        {"date": slot.isoformat(), "kind": "tempo", "title": "Tempo", "distance_km": 10}])
    at = logged_in("7_AI_Coach.py", ai_slot_on=True, ai_slot_date=slot, ai_slot_time=time(7, 0)).run()
    assert not at.exception, [e.value for e in at.exception]
    prompt = at.code[0].value
    assert "inconnu" in prompt and "plan Objectif validé" not in prompt and "ne la remplace pas" not in prompt
