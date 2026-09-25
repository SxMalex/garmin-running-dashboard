"""Modes Light / Pro : bascule sur chaque page, explications, indicateurs Pro, réglages."""

import pytest

from conftest import PAGES
from ui_mode import MODE_KEY, PARAMS_KEY


@pytest.mark.parametrize("mode", ["light", "pro"])
@pytest.mark.parametrize("name", ["main.py", *PAGES])
def test_every_page_in_both_modes(logged_in, name, mode):
    at = logged_in(name, **{MODE_KEY: mode}).run()
    assert not at.exception, [e.value for e in at.exception]
    assert _mode_widget(at) is not None


def _mode_widget(at):
    """La bascule Light/Pro vit dans l'en-tête (contrôle segmenté)."""
    return next((w for w in at.button_group if w.label == "Mode d'affichage"), None)


def _explains(at):
    return [e for e in at.expander if e.label.startswith("💡 ") and "c'est quoi" in e.label]


def test_light_explains_pro_does_not(logged_in):
    light = logged_in("3_Forme.py", **{MODE_KEY: "light"}).run()
    pro = logged_in("3_Forme.py", **{MODE_KEY: "pro"}).run()
    assert len(_explains(light)) >= 2
    assert _explains(pro) == []


def test_pro_shows_load_risk(logged_in):
    pro = logged_in("3_Forme.py", **{MODE_KEY: "pro"}).run()
    labels = {m.label for m in pro.metric}
    assert {"ACWR 7/28 j", "Monotonie 7 j"} <= labels
    light = logged_in("3_Forme.py", **{MODE_KEY: "light"}).run()
    assert "ACWR 7/28 j" not in {m.label for m in light.metric}


def test_toggle_switches_mode(logged_in):
    at = logged_in("3_Forme.py").run()
    assert _explains(at)                               # Light par défaut
    _mode_widget(at).set_value("pro").run()
    assert at.session_state[MODE_KEY] == "pro"
    assert not _explains(at)


def test_pro_settings_stored_outside_widget(logged_in):
    """Revue : la valeur survit à une page qui ne rend pas le curseur."""
    at = logged_in("4_Progression.py", **{MODE_KEY: "pro"}).run()
    slider = next(s for s in at.sidebar.slider if "effort minimal" in s.label)
    slider.set_value(60).run()
    assert at.session_state[PARAMS_KEY]["decoupling_min_moving_min"] == 60
    other = logged_in("3_Forme.py", **{MODE_KEY: "pro",
                                       PARAMS_KEY: {"decoupling_min_moving_min": 60}}).run()
    assert other.session_state[PARAMS_KEY]["decoupling_min_moving_min"] == 60
    back = logged_in("1_Activities.py", **{MODE_KEY: "pro",
                                           PARAMS_KEY: {"decoupling_min_moving_min": 60}}).run()
    assert next(s for s in back.sidebar.slider if "effort minimal" in s.label).value == 60


def test_light_hides_pro_settings(logged_in):
    at = logged_in("4_Progression.py", **{MODE_KEY: "light"}).run()
    assert not any("effort minimal" in s.label for s in at.sidebar.slider)



@pytest.mark.parametrize("name", ["main.py", "3_Forme.py", "4_Progression.py",
                                  "5_Next_Session.py", "8_Comparatif.py", "9_Objectif.py"])
def test_light_explains_on_metric_pages(logged_in, name):
    """Contre-validation F : Light n'expliquait les indicateurs que sur quelques pages."""
    import goal_store
    from datetime import date, timedelta
    goal_store.save_goal(42, {"distance": "10 km", "target_text": "",
                              "race_date": (date.today() + timedelta(weeks=8)).isoformat()},
                         {"runs_per_week": 4})
    at = logged_in(name, **{MODE_KEY: "light"}).run()
    assert not at.exception, [e.value for e in at.exception]
    assert _explains(at), name
