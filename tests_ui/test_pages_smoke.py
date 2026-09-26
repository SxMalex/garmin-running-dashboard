"""Chaque page se rend sans exception avec un compte Garmin factice."""

import pytest

from conftest import PAGES


@pytest.mark.parametrize("name", ["main.py", *PAGES])
def test_page_renders(logged_in, fake_api, name):
    at = logged_in(name).run()
    assert not at.exception, [e.value for e in at.exception]
    # safe_load_activities transforme les exceptions en message : sans ces
    # assertions, un chargement cassé laisserait la suite verte.
    assert "get_activities" in fake_api.calls
    # (st.error sert aussi de tuile de légende, ex. « TSB < −20 » sur Forme.)
    errors = [e.value for e in at.error if "Erreur" in e.value or "Garmin" in e.value]
    assert not errors, errors



def test_activities_explorer_every_metric(logged_in):
    """L'explorateur doit tracer chaque indicateur sans planter (colonnes absentes, NaN)."""
    from activities_logic import METRICS
    at = logged_in("1_Activities.py").run()
    for key in METRICS:
        next(w for w in at.button_group if w.label == "Indicateur").set_value(key).run()
        assert not at.exception, (key, [e.value for e in at.exception])
    assert any("en facile" in m.value for m in at.markdown)          # carte 80/20


def test_progress_page_shows_projections(logged_in, fake_api):
    fake_api.with_predictions = True
    at = logged_in("4_Progression.py").run()
    assert not at.exception, [e.value for e in at.exception]
    labels = {m.label: m for m in at.metric}
    assert "dans 90 j" in (labels["10 km"].delta or "")
    assert any(m.label.startswith("Dans 3 mois") for m in at.metric)
    next(w for w in at.button_group if w.label == "Vue").set_value("ensemble").run()
    assert not at.exception, [e.value for e in at.exception]


def test_home_shows_the_health_alert_first(logged_in, monkeypatch):
    """Veille santé : 2 signaux concordants → carte « Alerte santé » en tête des signaux."""
    import numpy as np
    import pandas as pd
    import illness_logic
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=35, freq="D")
    rng = np.random.default_rng(0)
    frame = pd.DataFrame({"rhr": 48 + rng.normal(0, 1, 35), "hrv": 60 + rng.normal(0, 4, 35),
                          "resp": 14.5 + rng.normal(0, 0.3, 35), "spo2": np.nan}, index=idx)
    frame.iloc[-1, 0], frame.iloc[-1, 2] = 57, 16.5
    monkeypatch.setattr(illness_logic, "load_health_frame", lambda gc, today, days=34: frame)
    at = logged_in("main.py").run()
    assert not at.exception, [e.value for e in at.exception]
    cards = [m.value for m in at.markdown if "gd-signal-title" in m.value]
    assert cards and "Alerte santé" in cards[0] and "FC de repos" in cards[0]


def test_progress_shows_running_form(logged_in):
    at = logged_in("4_Progression.py").run()
    assert not at.exception, [e.value for e in at.exception]
    kickers = " ".join(m.value for m in at.markdown)
    assert "Contact au sol" in kickers and "Puissance" in kickers
    next(w for w in at.button_group if w.label == "Métrique").set_value("avgStride_cm").run()
    assert not at.exception, [e.value for e in at.exception]




def test_race_day_page_flat_course_by_default(logged_in):
    """Jour de course sans GPX (AppTest ne sait pas téléverser ; l'import est couvert
    par tests_e2e) : plan sur parcours plat, bracelet et ravitaillement ≥ 1 h 15."""
    at = logged_in("10_Jour_de_course.py", rd_distance="Semi-marathon", rd_target="1:50:00").run()
    assert not at.exception, [e.value for e in at.exception]
    labels = {m.label: m.value for m in at.metric}
    assert labels["Allure moyenne"].startswith("5:13")
    assert any("gel" in m.value for m in at.markdown)
