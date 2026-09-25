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
