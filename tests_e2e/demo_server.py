"""
Lance le vrai dashboard Streamlit contre le faux compte Garmin des tests UI
(`tests_ui/fake_garmin.py`) : aucune donnée réelle, aucun réseau Garmin.

Usage : python tests_e2e/demo_server.py <port>

Le correctif de `resume_session` doit précéder l'import de `ui_helpers`, qui
en copie la référence : il est donc posé AVANT de démarrer Streamlit, dans ce
même processus. `CACHE_DIR`, `DATA_DIR` et `GARMIN_TOKENSTORE` doivent pointer
vers des dossiers jetables (le conftest s'en charge) : ils sont lus à l'import
de `garmin_client`.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
sys.path[:0] = [str(APP), str(ROOT / "tests_ui")]

import garmin_client  # noqa: E402
from fake_garmin import FakeGarmin  # noqa: E402

_API = FakeGarmin(n_runs=60)
_API.with_predictions = True   # page Progrès : historique + projections
garmin_client.resume_session = lambda: _API

if __name__ == "__main__":
    port = sys.argv[1] if len(sys.argv) > 1 else "8599"
    # `.streamlit/config.toml` est lu depuis le répertoire courant.
    os.chdir(APP)
    from streamlit.web import cli as stcli

    sys.argv = [
        "streamlit", "run", str(APP / "main.py"),
        "--server.port", port, "--browser.serverPort", port,
        "--server.address", "127.0.0.1", "--server.headless", "true",
        # Pas de rechargement de modules en cours de route : il annulerait le correctif.
        "--server.fileWatcherType", "none",
    ]
    sys.exit(stcli.main())
