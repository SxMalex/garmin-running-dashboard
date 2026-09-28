"""Identité « Piste claire » : contrastes, carte séance (échappement), jauge, navigation."""

import re
from datetime import date
from pathlib import Path

import pytest

import chart_theme as ct
from nav import POLES
from ui_theme import _CSS, contrast, freshness_bar, ink_on, session_card, tsb_status


_SERIES = [*ct.CAT, *ct.ZONE_RAMP, *ct.ZONE_HEAT, *ct.WORKOUT_COLORS.values(),
           ct.SLEEP_DEEP, ct.SLEEP_LIGHT, ct.SLEEP_REM, ct.SLEEP_AWAKE,
           ct.GOOD, ct.WARNING, ct.SERIOUS, ct.CRITICAL]


@pytest.mark.parametrize("color", _SERIES)
def test_series_colors_hold_3_to_1_on_paper(color):
    """Marques graphiques (WCAG 1.4.11) sur le fond papier et sur les cartes blanches."""
    assert contrast(color, ct.SURFACE) >= 3.0, color
    assert contrast(color, ct.SURFACE_2) >= 3.0, color


@pytest.mark.parametrize("status", sorted(ct.STATUS_BG))
def test_status_chips_are_readable(status):
    assert contrast(ct.STATUS_TEXT[status], ct.STATUS_BG[status]) >= 4.5, status


def test_accent_is_a_fill_with_ink_on_top():
    """L'accent volt ne se lit pas sur clair : il ne sert qu'en aplat, encre dessus."""
    assert contrast(ct.ACCENT, ct.SURFACE) < 3.0
    assert ink_on(ct.ACCENT) == ct.INK and contrast(ct.ACCENT, ct.INK) >= 4.5
    assert contrast(ct.INK_MUTED, ct.SURFACE) >= 4.5


# Blocs sur fond encre, où l'accent peut servir de couleur de texte.
_INK_BLOCKS = (".gd-day-done", ".st-key-gd-bottomnav")


def test_accent_is_text_only_on_ink_blocks():
    """Revue : `color: var(--gd-accent)` n'est permis que sur fond encre."""
    for rule in _CSS.split("}"):
        if re.search(r"(?<!-)color:\s*var\(--gd-accent\)", rule):
            selector = rule.rsplit("{", 1)[0]
            assert any(b in selector for b in _INK_BLOCKS), selector.strip()


@pytest.mark.parametrize("tsb", [-55.0, -20.0, 0.0, 5.0, 42.0])
def test_gauge_needle_stays_on_the_track(tsb):
    left = float(re.search(r"left:([\d.]+)%", freshness_bar(tsb)).group(1))
    assert 0 < left < 100


def test_gauge_without_value():
    html = freshness_bar(None)
    assert "gd-needle" not in html and "inconnue" in html


def test_tsb_status_matches_thresholds():
    assert tsb_status(-21)[0] == "Fatigué"
    assert tsb_status(0)[0] == "Neutre"
    assert tsb_status(6)[0] == "Frais"


def test_session_card_escapes_and_stays_one_block():
    html = session_card(kicker="Séance", number="10", unit="km",
                        title="<script>alert(1)</script>",
                        target="15 min\n\n![x](https://tiers.example/pixel.png) **gras**",
                        why="ligne 1\n\nligne 2")
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert "\n\n" not in html   # aucune rupture de bloc HTML → pas de Markdown injecté


def _nav_links(at):
    return [(el.proto.label, el.proto.page) for el in at.get("page_link")]


def test_header_lists_the_four_poles(logged_in):
    at = logged_in("main.py").run()
    assert not at.exception, [e.value for e in at.exception]
    labels = [label for label, _ in _nav_links(at)]
    for pole in POLES:
        # En-tête (desktop) + barre du bas (téléphone)
        assert labels.count(pole.label) == 2, pole.label


def test_active_pole_and_subnav(logged_in):
    at = logged_in("3_Forme.py").run()
    assert not at.exception, [e.value for e in at.exception]
    labels = [label for label, _ in _nav_links(at)]
    assert {"Cockpit", "Forme & récup", "Prochaine sortie"} <= set(labels)
    assert "Statistiques" not in labels          # sous-pages d'un autre pôle


def test_home_coach_branch_shows_plan_details(logged_in, monkeypatch):
    """La branche « coach » (source primaire) de la carte séance."""
    import ui_helpers
    task = {"date": date.today(), "name": "Semi — seuil 3×10 min", "duration_min": 55,
            "target_text": "3x10:00@4:50/km", "session_key": "tempo"}
    ctx = {"plan": {"name": "Semi Run Coach"}, "phase": {"label": "Build"},
           "days_to_event": 40, "next_run": task, "tasks": [task]}
    monkeypatch.setattr(ui_helpers, "cached_coach_context", lambda *a, **k: ctx)
    at = logged_in("main.py").run()
    assert not at.exception, [e.value for e in at.exception]
    html = " ".join(m.value for m in at.markdown)
    assert "Semi — seuil 3×10 min" in html
    assert "Semi Run Coach" in html and "phase Build" in html and "J−40" in html


def test_refresh_bumps_the_nonce_and_reloads(logged_in, fake_api):
    """Revue : Actualiser et Déconnexion, regroupés dans l'en-tête, sans test."""
    at = logged_in("main.py").run()
    fake_api.calls.clear()
    at.button(key="gd-refresh").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["_cache_nonce"] == 1
    assert "get_activities" in fake_api.calls


def test_logout_clears_tokens_and_shows_login(logged_in, monkeypatch):
    import garmin_client
    import ui_helpers
    cleared = []
    # main.py importe clear_tokens depuis garmin_client à chaque run.
    monkeypatch.setattr(garmin_client, "clear_tokens", lambda: cleared.append(True))
    at = logged_in("main.py").run()
    at.button(key="gd-logout").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert cleared == [True]
    assert "garmin_api" not in at.session_state
    assert any(w.label == "Email Garmin" for w in at.text_input)


@pytest.mark.parametrize("name", ["main.py", "3_Forme.py", "5_Next_Session.py"])
def test_refresh_reloads_recovery_on_every_page(logged_in, fake_api, name):
    """Audit : Actualiser ne relisait la HRV que sur l'Accueil — Accueil et
    Prochaine sortie annonçaient alors deux séances différentes pendant 1 h."""
    at = logged_in(name).run()
    before = fake_api.calls.count("get_hrv_data")
    assert before >= 1
    fake_api.hrv_status = "LOW"          # la montre a synchronisé entre-temps
    at.button(key="gd-refresh").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert fake_api.calls.count("get_hrv_data") > before


_APP = Path(__file__).resolve().parent.parent / "app"


def test_no_dark_theme_colour_left_in_drawn_code():
    """Audit : les onglets traçaient encore les teintes de l'ancien thème sombre
    (#e66767, #6da7ec…) alors que seul ct.CAT était testé. Toute couleur écrite en
    dur dans une page ou un onglet doit tenir 3:1 sur le papier."""
    files = [*(_APP / "pages").glob("*.py"), *(_APP / "stats_tabs").glob("*.py"),
             _APP / "ui_helpers.py", _APP / "physio_ui.py"]
    weak = []
    for f in files:
        for hexa in re.findall(r'"(#[0-9a-fA-F]{6})"', f.read_text()):
            if contrast(hexa, ct.SURFACE) < 3.0:
                weak.append(f"{f.name}: {hexa}")
    assert not weak, weak



def test_charts_background_is_transparent():
    """Le frontend Streamlit impose aux graphiques Plotly son fond gris
    (secondaryBackgroundColor), même avec theme=None : visible dans les cartes
    blanches. La feuille de style le rend transparent pour tous les graphiques."""
    assert '[data-testid="stPlotlyChart"] .main-svg' in _CSS and "rect.bg" in _CSS
