"""Pages en vrai navigateur : interactions que seuls le JS et le CSS rendent possibles."""


from playwright.sync_api import expect
from conftest import settle


def test_clicking_an_explorer_point_opens_the_activity(open_page):
    page = open_page("/activites")
    point = page.locator(".st-key-card-act-explorer .scatterlayer .point").first
    point.wait_for()
    point.click(force=True)          # un point Plotly est un <path> SVG : pas de rôle ARIA
    settle(page)
    expect(page.get_by_text("Détails —").first).to_be_visible(timeout=15000)


def test_explorer_metric_switch_redraws(open_page):
    page = open_page("/activites")
    page.get_by_role("radio", name="Calories", exact=True).click()
    settle(page)
    expect(page.locator(".st-key-card-act-explorer .ytitle")).to_contain_text("Calories")


def test_progress_projections_are_drawn(open_page):
    page = open_page("/tendances")
    expect(page.get_by_text("dans 90 j").first).to_be_visible()
    page.get_by_role("radio", name="Superposé (% de progression)").click()
    settle(page)
    expect(page.locator('[data-testid="stPlotlyChart"] .ytitle').first).to_contain_text("Progression")


def test_charts_blend_into_the_cards(open_page):
    """Fond Plotly transparent (le frontend Streamlit le forçait au gris)."""
    page = open_page("/activites")
    bg = page.locator('[data-testid="stPlotlyChart"] rect.bg').first
    bg.wait_for(state="attached")
    assert bg.evaluate("e => getComputedStyle(e).fill") in ("transparent", "rgba(0, 0, 0, 0)")


def _click_table_row(page, row_index):
    """Coche la ligne `row_index` (0 = première) du tableau des sorties (grille canvas)."""
    grid = page.locator("[data-testid='stDataFrame']").first
    grid.wait_for()
    grid.scroll_into_view_if_needed()
    box = grid.bounding_box()
    page.mouse.click(box["x"] + 12, box["y"] + 35 * (row_index + 1) + 17)
    settle(page)


def test_filtering_after_a_table_selection_does_not_crash(open_page):
    """Revue #1 : une ligne loin dans la liste, puis un filtre qui n'en garde que 3 → iloc hors bornes."""
    page = open_page("/activites")
    page.get_by_text("Toutes les sorties").first.click()
    settle(page)
    _click_table_row(page, 8)          # 9e ligne (la grille en montre 10)
    expect(page.get_by_text("Détails —").first).to_be_visible(timeout=15000)
    search = page.get_by_role("textbox", name="🔎 Rechercher par nom")
    search.fill("Course 5")          # une seule sortie sur les 90 derniers jours : index 8 hors bornes
    page.keyboard.press("Enter")
    settle(page)
    assert page.locator('[data-testid="stException"]').count() == 0


def test_table_click_after_a_chart_click_changes_the_detail(open_page):
    """Revue #1 : après un clic sur le graphe, la liste ne changeait plus le détail."""
    page = open_page("/activites")
    point = page.locator(".st-key-card-act-explorer .scatterlayer .point").first
    point.wait_for()
    point.click(force=True)
    settle(page)
    first = page.get_by_text("Détails —").first.inner_text()
    page.get_by_text("Toutes les sorties").first.click()
    settle(page)
    _click_table_row(page, 3)
    expect(page.get_by_text("Détails —").first).not_to_have_text(first, timeout=15000)


def _detail_title(page):
    loc = page.get_by_text("Détails —")
    return loc.first.inner_text() if loc.count() else None


def test_unticking_the_row_closes_the_detail(open_page):
    """Contre-revue : une fois une sortie choisie, décocher ne refermait plus le détail."""
    page = open_page("/activites")
    page.get_by_text("Toutes les sorties").first.click()
    settle(page)
    _click_table_row(page, 2)
    expect(page.get_by_text("Détails —").first).to_be_visible(timeout=15000)
    _click_table_row(page, 2)                                  # décoche
    expect(page.get_by_text("Détails —")).to_have_count(0, timeout=15000)


def test_list_then_chart_then_list_needs_one_click(open_page):
    """Contre-revue : liste X, graphe Y, puis X dans la liste le décochait (deux clics nécessaires)."""
    page = open_page("/activites")
    page.get_by_text("Toutes les sorties").first.click()
    settle(page)
    _click_table_row(page, 1)
    x = _detail_title(page)
    point = page.locator(".st-key-card-act-explorer .scatterlayer .point").nth(5)
    point.click(force=True)
    settle(page)
    y = _detail_title(page)
    assert y and y != x
    if not page.locator("[data-testid='stDataFrame']").first.is_visible():
        page.get_by_text("Toutes les sorties").first.click()
        settle(page)
    _click_table_row(page, 1)                                  # un seul clic
    expect(page.get_by_text(x, exact=True).first).to_be_visible(timeout=15000)   # « Course 1 » ≠ « Course 10 »


def test_coming_back_to_the_page_does_not_reopen_an_old_detail(open_page):
    page = open_page("/activites")
    point = page.locator(".st-key-card-act-explorer .scatterlayer .point").first
    point.wait_for()
    point.click(force=True)
    settle(page)
    expect(page.get_by_text("Détails —").first).to_be_visible(timeout=15000)
    page.get_by_role("link", name="Carte").first.click()
    settle(page)
    page.get_by_role("link", name="Activités").first.click()
    settle(page)
    page.locator(".st-key-card-act-explorer .scatterlayer .point").first.wait_for()
    expect(page.get_by_text("Détails —")).to_have_count(0)


def test_detail_survives_a_filter_or_metric_change(open_page):
    """Contre-revue 2 : changer un filtre (sortie toujours listée) ou l'indicateur refermait le détail."""
    page = open_page("/activites")
    page.get_by_text("Toutes les sorties").first.click()
    settle(page)
    _click_table_row(page, 0)
    title = _detail_title(page)
    assert title
    page.get_by_role("radio", name="Calories", exact=True).click()       # indicateur du graphe
    settle(page)
    expect(page.get_by_text(title, exact=True).first).to_be_visible(timeout=15000)
    search = page.get_by_role("textbox", name="🔎 Rechercher par nom")
    search.fill("0")                  # Course 0, 10, 20 : la liste change, la sortie choisie y reste
    page.keyboard.press("Enter")
    settle(page)
    expect(page.get_by_text(title, exact=True).first).to_be_visible(timeout=15000)


def test_chart_pick_survives_a_metric_change(open_page):
    page = open_page("/activites")
    point = page.locator(".st-key-card-act-explorer .scatterlayer .point").nth(3)
    point.wait_for()
    point.click(force=True)
    settle(page)
    title = _detail_title(page)
    assert title
    page.get_by_role("radio", name="Calories", exact=True).click()
    settle(page)
    expect(page.get_by_text(title, exact=True).first).to_be_visible(timeout=15000)


def test_chart_pick_survives_a_filter_change(open_page):
    """Contre-revue 3 : toute nouvelle figure (filtre) vidait la sélection du graphe → détail fermé."""
    page = open_page("/activites")
    point = page.locator(".st-key-card-act-explorer .scatterlayer .point").first
    point.wait_for()
    point.click(force=True)
    settle(page)
    title = _detail_title(page)
    assert title and "Course " in title, title
    digit = title.rsplit("Course ", 1)[1].strip()[-1]              # « Course 27 » → « 7 » : 7, 17, 27 restent
    page.get_by_role("textbox", name="🔎 Rechercher par nom").fill(digit)
    page.keyboard.press("Enter")
    settle(page)
    expect(page.get_by_text(title, exact=True).first).to_be_visible(timeout=15000)


def test_race_day_gpx_upload_builds_the_pace_band(open_page, tmp_path):
    pts = "".join(f'<trkpt lat="43.6" lon="{1.44 + k * 25 / 80600:.6f}"><ele>{150 + (60 if 120 < k < 160 else 0)}</ele></trkpt>'
                  for k in range(400))                                   # ~10 km, une côte vers le km 3-4
    gpx = tmp_path / "parcours.gpx"
    gpx.write_text(f'<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>{pts}'
                   '</trkseg></trk></gpx>')
    page = open_page("/jour-de-course")
    page.locator('input[type="file"]').set_input_files(str(gpx))
    settle(page)
    page.get_by_role("textbox", name="Temps visé").fill("50:00")
    page.keyboard.press("Enter")
    settle(page)
    expect(page.get_by_text("Bracelet d'allure")).to_be_visible()
    expect(page.get_by_text("Dénivelé").first).to_be_visible()


def test_calendar_two_clicks_compare_the_runs(open_page):
    page = open_page("/calendrier")
    # Mois précédent : le mois en cours n'a qu'une sortie les premiers jours
    # (FakeGarmin, une tous les 3 jours) ; le précédent en a toujours ~10.
    page.locator(".st-key-cal_prev button").first.click()
    settle(page)
    points = page.locator(".st-key-card-cal-grid .scatterlayer .trace:not(:first-child) .point")
    points.first.wait_for()
    assert points.count() >= 2
    points.nth(0).click(force=True)
    settle(page)
    page.locator(".st-key-card-cal-grid .scatterlayer .trace:not(:first-child) .point").nth(1).click(force=True)
    settle(page)
    expect(page.get_by_text("De A à B")).to_be_visible(timeout=15000)
    expect(page.get_by_text("Kilomètre par kilomètre")).to_be_visible()


import re  # noqa: E402
import pytest  # noqa: E402
from pathlib import Path  # noqa: E402


CARDS = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "gpx"


@pytest.mark.parametrize("card, target, expected", [
    ("organizer_route_and_track.gpx", "50:00", re.compile(r"(9\.9\d|10\.0\d) km en")),  # pas 29 km
    ("two_variants_10k_and_semi.gpx", "1:45", "parcours distincts"),     # variantes : la plus longue, dit
    ("utf16_entity_bomb.gpx", None, "DOCTYPE/ENTITY"),                   # hostile : refus propre
    ("degenerate_same_point.gpx", None, "trop court"),
])
def test_race_day_gpx_cards_in_the_browser(open_page, card, target, expected):
    page = open_page("/jour-de-course")
    page.locator('input[type="file"]').set_input_files(str(CARDS / card))
    settle(page)
    if target:
        box = page.get_by_role("textbox", name="Temps visé")
        box.fill(target)
        page.keyboard.press("Enter")
        settle(page)
    expect(page.get_by_text(expected).first).to_be_visible(timeout=15000)
    assert page.locator('[data-testid="stException"]').count() == 0
