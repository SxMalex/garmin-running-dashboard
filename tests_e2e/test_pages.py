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
