"""GARMIN_PASSWORD (.env) ne doit ni atteindre le navigateur ni servir de repli."""

import ui_helpers
import pytest
from streamlit.testing.v1 import AppTest

import garmin_client
from conftest import page_path

SECRET = "s3cret-value"


@pytest.fixture
def login_page(monkeypatch):
    monkeypatch.setenv("GARMIN_EMAIL", "runner@example.com")
    monkeypatch.setenv("GARMIN_PASSWORD", SECRET)
    monkeypatch.setattr(ui_helpers, "resume_session", lambda: None)
    calls = []
    # main.py importe login_with_credentials depuis garmin_client à chaque run.
    monkeypatch.setattr(garmin_client, "login_with_credentials",
                        lambda email, pw: calls.append((email, pw)) or ("ok", None))
    at = AppTest.from_file(page_path("main.py"), default_timeout=60).run()
    return at, calls


def test_password_from_env_is_not_sent_to_browser(login_page):
    at, _ = login_page
    assert not at.exception
    by_label = {w.label: w for w in at.text_input}
    assert by_label["Email Garmin"].value == "runner@example.com"
    assert by_label["Mot de passe"].value == ""
    # Tout l'arbre rendu (widgets, markdown, caption, code, erreurs…).
    assert SECRET not in repr(at)
    assert all(SECRET not in str(getattr(w, "placeholder", "")) for w in at.text_input)


def test_empty_form_does_not_login_with_env_password(login_page):
    """Un visiteur qui soumet le formulaire vide ne déclenche aucun login."""
    at, calls = login_page
    at.button[0].click().run()
    assert calls == []
    assert any("mot de passe" in e.value.lower() for e in at.error)


def test_typed_password_is_used(login_page):
    """Contre-épreuve : le stub observe bien le login quand on tape le mot de passe."""
    at, calls = login_page
    [w for w in at.text_input if w.label == "Mot de passe"][0].input("typed-pw")
    at.button[0].click().run()
    assert calls == [("runner@example.com", "typed-pw")]
