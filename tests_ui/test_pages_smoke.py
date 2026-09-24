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
