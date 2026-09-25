"""
Suite de bout en bout : le vrai dashboard dans un vrai navigateur (Playwright),
contre le faux compte Garmin. Elle attrape ce qu'AppTest ne voit pas, faute de
CSS : un élément qui en recouvre un autre, une barre mobile hors écran, un
lien qui ne mène nulle part.

Lancement : `.venv/bin/python -m pytest tests_e2e/ -q`
Prérequis : `playwright` (pip) et un Chromium — `playwright install chromium`,
ou à défaut le Chrome/Chromium du système (détecté automatiquement, ou
`PLAYWRIGHT_CHROMIUM_EXECUTABLE=/chemin/vers/chrome`).
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright non installé")

HERE = Path(__file__).resolve().parent
SYSTEM_BROWSERS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_healthy(url: str, proc: subprocess.Popen, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"le serveur de démo s'est arrêté (code {proc.returncode})")
        try:
            with urllib.request.urlopen(f"{url}/_stcore/health", timeout=2) as r:
                if r.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.5)
    raise RuntimeError("le serveur de démo n'a pas démarré à temps")


@pytest.fixture(scope="session")
def demo_url():
    """Dashboard de démo sur un port libre, caches et tokens jetables."""
    tmp = Path(tempfile.mkdtemp(prefix="gdash-e2e-"))
    port = _free_port()
    env = {**os.environ, "CACHE_DIR": str(tmp / "cache"), "DATA_DIR": str(tmp / "data"),
           "GARMIN_TOKENSTORE": str(tmp / "tokens"), "GARMIN_WRITE_ENABLED": ""}
    env.pop("ORS_API_KEY", None)
    log = open(tmp / "streamlit.log", "w")
    proc = subprocess.Popen([sys.executable, str(HERE / "demo_server.py"), str(port)],
                            env=env, stdout=log, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    try:
        _wait_healthy(url, proc)
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        shutil.rmtree(tmp, ignore_errors=True)


def _launch(p):
    exe = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if exe:
        return p.chromium.launch(executable_path=exe)
    try:
        return p.chromium.launch()
    except sync_api.Error:
        # Navigateur de Playwright absent : repli sur celui du système.
        for name in SYSTEM_BROWSERS:
            path = shutil.which(name)
            if path:
                return p.chromium.launch(executable_path=path, args=["--headless=new"])
        pytest.skip("aucun Chromium : lancer `playwright install chromium`")


@pytest.fixture(scope="session")
def browser(demo_url):
    with sync_api.sync_playwright() as p:
        b = _launch(p)
        # Premier chargement à froid : Streamlit peut afficher « Page not found »
        # sur la toute première session du processus ; on le consomme ici.
        warm = b.new_page()
        warm.goto(demo_url + "/", wait_until="networkidle")
        warm.close()
        yield b
        b.close()


VIEWPORTS = {
    "ordinateur": {"viewport": {"width": 1440, "height": 900}},
    "tablette": {"viewport": {"width": 900, "height": 900}},
    "telephone": {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True},
}


@pytest.fixture
def open_page(browser, demo_url):
    """Ouvre `path` dans un nouvel onglet au format voulu, une fois le rendu fini."""
    pages = []

    def _open(path: str = "/", device: str = "ordinateur"):
        page = browser.new_page(**VIEWPORTS[device])
        page.set_default_timeout(15000)
        pages.append(page)
        page.goto(demo_url + path, wait_until="networkidle")
        settle(page)
        return page

    yield _open
    for page in pages:
        page.close()


def settle(page) -> None:
    """Attend la fin du run Streamlit (plus d'indicateur « Running »)."""
    page.wait_for_timeout(500)
    for _ in range(80):
        if not page.query_selector('[data-testid="stStatusWidget"]'):
            break
        page.wait_for_timeout(250)
    page.locator("h1").first.wait_for()
