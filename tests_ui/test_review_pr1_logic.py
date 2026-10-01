"""
Revue PR 1 (lot L) — pages et helpers Streamlit : Run Coach inconnu sur
l'Accueil, chiffres « du mois » de la carte semaine, tendance de dérive qui
saute une sortie refusée (4xx) au lieu de s'arrêter.
"""

from datetime import date, datetime, timedelta

import pytest

import physio_ui
import ui_helpers
from fake_garmin import _run
from ui_mode import MODE_KEY


def _goal_run_tomorrow():
    day = (date.today() + timedelta(days=1)).isoformat()
    return [{"date": day, "kind": "tempo", "title": "Seuil du plan", "distance_km": 9.0,
             "duration_min": 50, "target": "3 × 8′", "pace_sec": 300.0, "steps": [],
             "why": "Séance clé"}]


def _html(at) -> str:
    return "\n".join(m.value for m in at.markdown)


def test_accueil_run_coach_inconnu_n_annonce_pas_le_plan_objectif(logged_in, fake_api, monkeypatch):
    """
    Revue : un échec passager de get_training_plans donnait « pas de plan »
    (mis en cache 1 h) et l'Accueil annonçait le plan Objectif à la place de
    la séance Run Coach.
    """
    monkeypatch.setattr(ui_helpers, "validated_plan_sessions", lambda: _goal_run_tomorrow())
    fake_api.plans_error = RuntimeError("API Error 503")
    at = logged_in("0_Accueil.py").run()
    assert not at.exception
    html = _html(at)
    assert "plan Objectif" not in html and "Seuil du plan" not in html
    assert any("Garmin n'a pas répondu" in i.value for i in at.info)
    # Garmin répond de nouveau : l'échec n'était pas en cache, le plan Objectif revient.
    fake_api.plans_error = None
    at.run()
    html = _html(at)
    assert "Séance du jour · plan Objectif" in html and "Seuil du plan" in html
    assert not any("Garmin n'a pas répondu" in i.value for i in at.info)


def test_accueil_allure_et_fc_du_mois(logged_in, fake_api):
    """
    Revue : « Allure moyenne » et « FC moyenne » portaient sur tout l'historique.
    Anciennes sorties lentes (7:00/km, 170 bpm), 3 sorties ce mois à 5:00/km et
    150 bpm : la carte doit dire 5:00/km et 150 bpm.
    """
    today = datetime.combine(date.today(), datetime.min.time())
    old = [_run(i, today - timedelta(days=45 + 3 * i)) for i in range(30)]
    for a in old:
        a.update(averageSpeed=1000 / 420, distance=a["duration"] * 1000 / 420, averageHR=170.0)
    recent = [_run(100 + h, today + timedelta(hours=h)) for h in (6, 7, 8)]
    for a in recent:
        a.update(averageSpeed=1000 / 300, distance=a["duration"] * 1000 / 300, averageHR=150.0)
    fake_api.activities = sorted(recent + old, key=lambda a: a["startTimeLocal"], reverse=True)
    at = logged_in("0_Accueil.py", **{MODE_KEY: "pro"}).run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Allure du mois"] == "5:00/km"
    assert metrics["FC moy. du mois"] == "150 bpm"
    assert "Allure moyenne" not in metrics and "FC moyenne" not in metrics


class _HttpError(Exception):
    def __init__(self, status):
        super().__init__(f"API Error {status}")
        self.status_code = status


@pytest.mark.parametrize("error,loaded,cause", [
    (_HttpError(404), [2, 3], None),
    (_HttpError(410), [2, 3], None),
    (_HttpError(401), [], "Garmin n'a pas répondu"),       # jetons révoqués : pas 12 refus d'affilée
    (_HttpError(403), [], "Garmin n'a pas répondu"),
    (_HttpError(429), [], "Garmin limite le nombre d'appels"),
    (_HttpError(500), [], "Garmin n'a pas répondu"),
    (RuntimeError("API Error 503"), [], "Garmin n'a pas répondu"),
    (ConnectionError("réseau coupé"), [], "Garmin n'a pas répondu"),
], ids=["404", "410", "401", "403", "429", "500", "503-message", "reseau"])
def test_tendance_saute_un_4xx_et_s_arrete_sur_429_5xx(monkeypatch, error, loaded, cause):
    """
    Revue : la sortie la plus récente répondait 404 (supprimée dans Garmin,
    encore dans la liste en cache) → aucune sortie plus ancienne analysée.
    """
    def load(athlete_id, activity_id):
        if activity_id == 1:
            raise error
        return {"time": [0, 1]}
    monkeypatch.setattr(physio_ui, "_load_streams_strict", load)
    streams, got_cause = physio_ui._load_candidate_streams(42, (1, 2, 3))
    assert sorted(streams) == loaded and got_cause == cause


def test_tendance_refus_au_milieu_garde_ce_qui_precede(monkeypatch):
    def load(athlete_id, activity_id):
        if activity_id == 2:
            raise _HttpError(502)
        return {"time": [0, 1]}
    monkeypatch.setattr(physio_ui, "_load_streams_strict", load)
    streams, cause = physio_ui._load_candidate_streams(42, (1, 2, 3))
    assert sorted(streams) == [1] and cause == "Garmin n'a pas répondu"


def test_tendance_s_arrete_apres_deux_refus_sautables_d_affilee(monkeypatch):
    """Contre-revue : un 404 GÉNÉRAL faisait 12 refus d'affilée à chaque rendu."""
    calls = []

    def load(athlete_id, activity_id):
        calls.append(activity_id)
        raise _HttpError(404)
    monkeypatch.setattr(physio_ui, "_load_streams_strict", load)
    streams, cause = physio_ui._load_candidate_streams(42, tuple(range(1, 13)))
    assert calls == [1, 2] and streams == {} and cause == "Garmin n'a pas répondu"


def test_tendance_un_404_isole_reste_saute(monkeypatch):
    def load(athlete_id, activity_id):
        if activity_id in (1, 3):
            raise _HttpError(404)
        return {"time": [0, 1]}
    monkeypatch.setattr(physio_ui, "_load_streams_strict", load)
    streams, cause = physio_ui._load_candidate_streams(42, (1, 2, 3, 4))
    assert sorted(streams) == [2, 4] and cause is None
