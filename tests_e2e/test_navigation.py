"""
Navigation au clic dans un vrai navigateur. Chaque test clique comme un
utilisateur : Playwright refuse de cliquer un élément recouvert par un autre
(c'est ainsi que la barre d'outils Streamlit, posée sur la 1re ligne de
l'en-tête, rendait les pôles incliquables sans qu'aucun test AppTest ne rougisse).
"""

import pytest
from playwright.sync_api import expect

from conftest import settle

POLES = [("Entraînement", "/activites", "Activités"),
         ("Progrès", "/tendances", "Tendances"),
         ("Objectif", "/objectif", "Objectif de course"),
         ("Aujourd'hui", "/", "Rien à signaler")]
EXPLAIN = "c'est quoi ?"   # encadrés pédagogiques : mode Light uniquement


def _path(page) -> str:
    return "/" + page.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0].rstrip("/")


def _topbar(page):
    return page.locator(".st-key-gd-topbar")


@pytest.mark.parametrize("pole, path, title", POLES)
@pytest.mark.parametrize("device", ["ordinateur", "tablette"])
def test_click_pole_in_header(open_page, device, pole, path, title):
    page = open_page("/forme", device)
    _topbar(page).get_by_role("link", name=pole, exact=True).click()
    settle(page)
    assert _path(page) == path.rstrip("/") or (path == "/" and _path(page) == "/")
    expect(page.locator("h1").first).to_contain_text(title)


def test_click_subpage(open_page):
    page = open_page("/")
    page.locator(".st-key-gd-subnav").get_by_role("link", name="Forme & récup").click()
    settle(page)
    assert _path(page) == "/forme"
    expect(page.locator("h1").first).to_contain_text("Forme & récup")


@pytest.mark.parametrize("device", ["ordinateur", "telephone"])
def test_mode_toggle_survives_navigation(open_page, device):
    page = open_page("/forme", device)
    top = _topbar(page)
    expect(page.get_by_text(EXPLAIN).first).to_be_visible()
    top.get_by_role("radio", name="Pro", exact=True).click()
    settle(page)
    expect(page.get_by_text(EXPLAIN).first).to_be_hidden()
    # Le mode vit hors clé de widget : il doit survivre au changement de page.
    page.locator(".st-key-gd-subnav").get_by_role("link", name="Cockpit").click()
    settle(page)
    expect(page.get_by_text(EXPLAIN).first).to_be_hidden()
    top.get_by_role("radio", name="Light", exact=True).click()
    settle(page)
    expect(page.get_by_text(EXPLAIN).first).to_be_visible()


@pytest.mark.parametrize("device", ["ordinateur", "tablette"])
def test_refresh_button(open_page, device):
    page = open_page("/objectif", device)
    _topbar(page).get_by_role("button", name="Actualiser").click()
    settle(page)
    assert _path(page) == "/objectif"
    expect(page.locator("h1").first).to_contain_text("Objectif de course")
    expect(page.get_by_text("Page not found")).to_be_hidden()


def test_coach_link(open_page):
    page = open_page("/")
    _topbar(page).get_by_role("link", name="Prompt coach IA").click()
    settle(page)
    assert _path(page) == "/coach"
    expect(page.locator("h1").first).to_contain_text("Coach IA")


def test_account_menu_opens_without_logging_out(open_page):
    page = open_page("/")
    _topbar(page).get_by_role("button", name="Compte").click()
    expect(page.get_by_role("button", name="Déconnexion")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("h1").first).to_contain_text("Rien à signaler")


@pytest.mark.parametrize("tab, path, title", [("Progrès", "/tendances", "Tendances"),
                                              ("Objectif", "/objectif", "Objectif"),
                                              ("Entraînement", "/activites", "Activités"),
                                              ("Aujourd'hui", "/", "Rien à signaler")])
def test_phone_bottom_tabs(open_page, tab, path, title):
    page = open_page("/forme", "telephone")
    page.locator(".st-key-gd-bottomnav").get_by_role("link", name=tab).tap()
    settle(page)
    expect(page.locator("h1").first).to_contain_text(title)
    assert _path(page) == path.rstrip("/") or (path == "/" and _path(page) == "/")


def test_phone_bottom_bar_fits_the_screen(open_page):
    """Revue visuelle : la barre du bas débordait à droite."""
    page = open_page("/", "telephone")
    box = page.locator(".st-key-gd-bottomnav").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390


def test_desktop_hides_the_bottom_bar(open_page):
    page = open_page("/")
    expect(page.locator(".st-key-gd-bottomnav")).to_be_hidden()


_OVERFLOWING = """() => [...document.querySelectorAll('[class*="st-key-gd-"]')].filter(e => {
  const cs = getComputedStyle(e);
  const scrollX = e.scrollWidth > e.clientWidth + 1 && !['visible', 'hidden', 'clip'].includes(cs.overflowX);
  const scrollY = e.scrollHeight > e.clientHeight + 1 && !['visible', 'hidden', 'clip'].includes(cs.overflowY);
  return scrollX || scrollY;
}).map(e => [...e.classList].find(c => c.startsWith('st-key-')))"""


@pytest.mark.parametrize("width", [1920, 1440, 1280, 1100, 900, 390])
@pytest.mark.parametrize("path", ["/", "/forme"])   # /forme : barre latérale ouverte
def test_header_never_shows_a_scrollbar(browser, demo_url, width, path):
    """Retour utilisateur : « la barre de navigation montre un ascenseur ».
    Streamlit met overflow:auto sur les conteneurs horizontaux ; barre latérale
    ouverte, l'en-tête débordait de 175 px."""
    page = browser.new_page(viewport={"width": width, "height": 900})
    try:
        page.goto(demo_url + path, wait_until="networkidle")
        settle(page)
        assert page.evaluate(_OVERFLOWING) == []
    finally:
        page.close()
