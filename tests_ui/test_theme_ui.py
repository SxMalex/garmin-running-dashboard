"""Identité « Piste claire » : contrastes, carte séance (échappement), jauge, navigation."""

import ast
import re
from datetime import date
from pathlib import Path

import pytest

import chart_theme as ct
from nav import POLES
from ui_theme import _CSS, freshness_bar, session_card, tsb_status


# Contraste WCAG : seuls les tests en ont besoin (plus aucun appelant dans app/).
def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    rgb = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def contrast(a: str, b: str) -> float:
    """Rapport de contraste WCAG entre deux couleurs hex."""
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


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
    assert contrast(ct.ACCENT, ct.INK) >= 4.5
    assert contrast(ct.ACCENT, ct.INK) > contrast(ct.ACCENT, "#ffffff")   # encre, pas blanc
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


_DRAWN = [*(_APP / "pages").glob("*.py"), *(_APP / "stats_tabs").glob("*.py"),
          _APP / "main.py", _APP / "ui_helpers.py", _APP / "physio_ui.py"]

# Couleur littérale : #rgb, #rgba, #rrggbb, #rrggbbaa (pas une entité &#123;),
# ou une fonction CSS rgb()/rgba()/hsl()/hsla(), quelle que soit la casse.
_HEX = re.compile(r"(?<![\w&#])#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])")
_FUNC = re.compile(r"\b(rgba?|hsla?)\s*\(([^()]*)\)", re.IGNORECASE)
# Déclarations CSS de surface (fond de carte, filet) : pas des marques graphiques.
_SURFACE_DECL = re.compile(r"\b(?:background|border|outline|box-shadow)[\w-]*\s*:[^;{}]*",
                           re.IGNORECASE)


def _theme_hexes(value) -> set[str]:
    """Toutes les teintes #rrggbb déclarées par chart_theme (listes, dicts compris)."""
    if isinstance(value, str):
        return {value.lower()} if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else set()
    if isinstance(value, dict):
        value = [v for k, v in value.items() if not str(k).startswith("__")]
    if isinstance(value, (list, tuple)):
        return set().union(*map(_theme_hexes, value)) if value else set()
    return set()


_THEME = _theme_hexes(vars(ct))


def _judge(hexa: str, found: str, weak: list[str]) -> None:
    ratio = contrast(hexa, ct.SURFACE)
    if hexa.lower() not in _THEME or ratio < 3.0:
        where = "hors thème, " if hexa.lower() not in _THEME else ""
        weak.append(f"{found} ({where}{ratio:.2f}:1 sur le papier)")


def _num(token: str, scale: float) -> float:
    return float(token[:-1]) * scale / 100 if token.endswith("%") else float(token)


def weak_colours(source: str) -> list[str]:
    """Couleurs écrites en dur dans `source` hors du thème ou sous 3:1 sur le papier.

    On juge la teinte de base (sans l'alpha) : elle doit être un token de
    chart_theme ET tenir 3:1. Un aplat translucide d'une teinte hors thème (ex.
    rgba(144,133,233,.16), l'ancien #9085e9 à 2,84:1) reste un reste de l'ancien
    thème, et une teinte voisine d'un token (#0ca30c pour GOOD) une dérive. Une
    couleur calculée (f-string, .format, %) ne peut pas être vérifiée : elle est
    signalée, il faut passer par ct.rgba(token, a).
    Ignorés : le transparent (alpha nul) et les fonds/filets CSS."""
    tree = ast.parse(source)
    inner = {id(v) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)
             for v in node.values}
    texts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in inner:
            texts.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            texts.append("".join(v.value if isinstance(v, ast.Constant) else "{}"
                                 for v in node.values))
    weak = []
    for text in texts:
        skipped = [m.span() for m in _SURFACE_DECL.finditer(text)]
        def surface(pos):
            return any(a <= pos < b for a, b in skipped)
        for m in _HEX.finditer(text):
            h = m.group(1)
            if surface(m.start()):
                continue
            if len(h) in (3, 4):
                h = "".join(c * 2 for c in h)
            if len(h) == 8 and h[6:] == "00":
                continue                                   # transparent
            _judge("#" + h[:6], m.group(0), weak)
        for m in _FUNC.finditer(text):
            if surface(m.start()):
                continue
            parts = [t for t in re.split(r"[\s,/]+", m.group(2).strip()) if t]
            try:
                if m.group(1).lower().startswith("hsl") or len(parts) not in (3, 4):
                    raise ValueError
                r, g, b = (round(_num(t, 255)) for t in parts[:3])
                alpha = _num(parts[3], 1) if len(parts) == 4 else 1.0
            except ValueError:
                weak.append(f"{m.group(0)} (couleur calculée : passer par ct.rgba)")
                continue
            if alpha == 0:
                continue                                   # transparent
            _judge(f"#{r:02x}{g:02x}{b:02x}", m.group(0), weak)
    return weak


def test_no_dark_theme_colour_left_in_drawn_code():
    """Audit : les onglets traçaient encore les teintes de l'ancien thème sombre
    (#e66767, #6da7ec…) alors que seul ct.CAT était testé ; revue PR 1 : le test
    ne voyait que "#rrggbb" et laissait passer les rgba() en dur (2,84:1). Toute
    couleur écrite en dur dans une page ou un onglet doit être un token du thème
    tenant 3:1 sur le papier."""
    weak = [f"{f.name}: {c}" for f in _DRAWN for c in weak_colours(f.read_text())]
    assert not weak, weak


_WEAK_SNIPPETS = [
    'c = "rgba(144,133,233,0.9)"',                 # l'ancien violet du profil altimétrique
    'c = "rgba( 144 , 133 , 233 , 0.9 )"',         # espaces
    'c = "RGBA(144,133,233,.9)"',                  # majuscules, alpha sans zéro
    'c = "rgba(144,133,233,0.16)"',                # aplat translucide d'une teinte hors thème
    'c = "rgb(144,133,233)"',
    'c = "rgb(144 133 233 / 90%)"',                # syntaxe CSS 4
    'c = "rgba(56%, 52%, 91%, 1)"',                # composantes en pourcentage
    'c = "#9085e9"',
    'c = "#9085E9"',                               # majuscules
    'c = "#ccc"',                                  # hex court
    'c = "#ccc8"',                                 # hex court + alpha
    'c = "#9085e9cc"',                             # hex + alpha
    'c = f"rgba({i}, 156, 252, 0.8)"',             # couleur calculée (ancien tab_volume)
    'c = "rgba({}, 156, 252, 0.8)".format(i)',
    'c = "rgba(%d, 156, 252, 0.8)" % i',
    'c = "hsl(250, 70%, 72%)"',                    # non vérifiable
    'c = f"color: #9085e9; {x}"',                  # dans une f-string CSS
    'st.markdown(\'<span style="color: rgba(144,133,233,0.9)">x</span>\')',
    'z = [(0, 1, "rgba(201,133,0,0.10)")]',         # tuple de bandes de zones
    'f(line=dict(color="rgba(230,103,103,0.9)"))',  # ancien rouge FC (2,93:1)
    'c = "rgba(12,163,12,0.85)"',                  # ancien GOOD (#0ca30c) : 3:1 mais hors thème
    'c = "rgba(25,158,112,0.5)"',                  # ancien aqua (#199e70)
    'c = "#ECE9E1"',                               # token GRID, mais 1,1:1 : pas une marque
]

_OK_SNIPPETS = [
    'c = ct.rgba(ct.VIOLET, 0.16)',
    'c = "rgba(0,0,0,0)"',
    'c = "rgba(0, 0, 0, 0.0)"',
    'c = "#00000000"',
    'c = "#3987e5"',
    'c = "rgba(57,135,229,0.15)"',                 # teinte du thème, même très translucide
    'c = "RGBA(208, 59, 59, .1)"',                 # CRITICAL, casse et espaces libres
    'c = "#62666F"',                               # INK_MUTED
    'c = f"{x:.1f} km"',
    'c = f"color: {ct.INK}"',
    'css = "background: #F5F4EF; border: 1px solid #E4E1D8; color: #3D4048"',
    'md = "#### Titre"',
    'md = "Lap #1 — &#123;"',
    'md = "Voir #readme-install"',
]


@pytest.mark.parametrize("snippet", _WEAK_SNIPPETS)
def test_colour_scan_flags_weak_or_computed_colours(snippet):
    assert weak_colours(snippet), snippet


@pytest.mark.parametrize("snippet", _OK_SNIPPETS)
def test_colour_scan_accepts_theme_tokens(snippet):
    assert weak_colours(snippet) == [], snippet



def test_charts_background_is_transparent():
    """Le frontend Streamlit impose aux graphiques Plotly son fond gris
    (secondaryBackgroundColor), même avec theme=None : visible dans les cartes
    blanches. La feuille de style le rend transparent pour tous les graphiques."""
    assert '[data-testid="stPlotlyChart"] .main-svg' in _CSS and "rect.bg" in _CSS
