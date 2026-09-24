"""
Suite UI : rend les vraies pages Streamlit en headless (`streamlit.testing`)
contre un faux client Garmin. Séparée de `tests/` parce que `tests/conftest.py`
remplace streamlit par un MagicMock pour la logique pure.

Lancement : `.venv/bin/python -m pytest tests_ui/ -q`
"""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"

# Avant tout import de garmin_client : CACHE_DIR / tokenstore sont lus à
# l'import. Dossiers jetables → jamais le cache ni les tokens réels.
_TMP = Path(tempfile.mkdtemp(prefix="gdash-ui-"))
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
os.environ["CACHE_DIR"] = str(_TMP / "cache")
os.environ["DATA_DIR"] = str(_TMP / "data")
os.environ["GARMIN_TOKENSTORE"] = str(_TMP / "tokens")
os.environ.pop("ORS_API_KEY", None)
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
import streamlit as st  # noqa: E402

import garmin_client  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from fake_garmin import FakeGarmin  # noqa: E402

PAGES = sorted(p.name for p in (APP_DIR / "pages").glob("*.py"))


def page_path(name: str) -> str:
    return str(APP_DIR / name) if name == "main.py" else str(APP_DIR / "pages" / name)


@pytest.fixture(autouse=True)
def _isolated_caches(monkeypatch):
    # st.cache_data est global au process et le cache disque est partagé par
    # la session : sans purge des deux, un test lirait les données du précédent
    # (et ne solliciterait jamais son propre FakeGarmin).
    st.cache_data.clear()
    shutil.rmtree(garmin_client.CACHE_DIR, ignore_errors=True)
    shutil.rmtree(os.environ["DATA_DIR"], ignore_errors=True)
    monkeypatch.setattr(garmin_client, "API_COOLDOWN_S", 0)
    yield
    st.cache_data.clear()


@pytest.fixture
def fake_api():
    return FakeGarmin()


@pytest.fixture
def logged_in(fake_api):
    """Fabrique un AppTest connecté (session_state pré-rempli)."""
    def _make(name: str, **state) -> AppTest:
        at = AppTest.from_file(page_path(name), default_timeout=60)
        at.session_state["garmin_api"] = fake_api
        at.session_state["garmin_athlete_id"] = 42
        for key, value in state.items():
            at.session_state[key] = value
        return at
    return _make
