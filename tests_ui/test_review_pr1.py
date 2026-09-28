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
