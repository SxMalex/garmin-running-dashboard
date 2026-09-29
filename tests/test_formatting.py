"""
Tests des helpers de présentation `map_zoom` et `decimate` (formatting.py).
Fonctions pures sans dépendance Streamlit/Plotly.
"""

from datetime import date

import pandas as pd
import pytest

from formatting import decimate, map_zoom, weekday_fr


# ===========================================================================
# map_zoom — choix du niveau de zoom selon l'amplitude des coordonnées
# ===========================================================================

class TestMapZoom:
    """Couvre les 6 branches d'altitude et le calcul du centre."""

    @pytest.mark.parametrize("max_range,expected_zoom", [
        # (max_range_target, expected_zoom)
        # Branche 1 : < 0.01 → zoom 15 (très local, parc de quartier)
        (0.005, 15),
        # Branche 2 : < 0.05 → zoom 13 (un quartier)
        (0.02,  13),
        # Branche 3 : < 0.15 → zoom 12 (une ville)
        (0.10,  12),
        # Branche 4 : < 0.4 → zoom 11 (agglomération)
        (0.30,  11),
        # Branche 5 : < 1.0 → zoom 10 (une région)
        (0.80,  10),
        # Branche 6 : ≥ 1.0 → zoom 9 (plusieurs régions)
        (2.0,    9),
    ])
    def test_zoom_branches(self, max_range, expected_zoom):
        # On encode l'amplitude `max_range` via une variation en latitude
        lats = [48.85, 48.85 + max_range]
        lons = [2.35, 2.35]
        _, _, zoom = map_zoom(lats, lons)
        assert zoom == expected_zoom

    def test_uses_max_of_lat_lon_range(self):
        # Variation longitude plus grande que latitude → c'est elle qui pilote
        lats = [48.85, 48.86]      # 0.01
        lons = [2.35, 2.85]        # 0.50 → branche 5 (< 1.0)
        _, _, zoom = map_zoom(lats, lons)
        assert zoom == 10

    def test_center_is_midpoint(self):
        lats = [48.0, 49.0]
        lons = [2.0, 4.0]
        center_lat, center_lon, _ = map_zoom(lats, lons)
        assert center_lat == pytest.approx(48.5)
        assert center_lon == pytest.approx(3.0)

    def test_single_point_max_range_zero(self):
        # Un seul point répété → max_range = 0 → branche la plus serrée (zoom 15)
        lats = [48.85, 48.85]
        lons = [2.35, 2.35]
        center_lat, center_lon, zoom = map_zoom(lats, lons)
        assert center_lat == pytest.approx(48.85)
        assert center_lon == pytest.approx(2.35)
        assert zoom == 15

    def test_negative_coords_supported(self):
        # Hémisphère sud / ouest → calcul du centre reste valide
        lats = [-33.86, -33.85]
        lons = [-70.65, -70.64]
        center_lat, center_lon, zoom = map_zoom(lats, lons)
        assert center_lat == pytest.approx(-33.855)
        assert center_lon == pytest.approx(-70.645)
        # Variation 0.01 → branche 2 (< 0.05) → zoom 13
        assert zoom == 13


# ===========================================================================
# decimate — sous-échantillonnage des streams pour Plotly
# ===========================================================================

class TestDecimate:
    def test_shorter_than_target_unchanged(self):
        # Liste plus courte que target → renvoyée telle quelle
        values = [1, 2, 3, 4, 5]
        result = decimate(values, target=100)
        assert result == values
        # Et c'est bien une copie (le contrat utilise list(values))
        assert result is not values

    def test_exactly_target_unchanged(self):
        values = list(range(10))
        result = decimate(values, target=10)
        assert result == values

    def test_longer_than_target_decimated(self):
        values = list(range(1000))
        result = decimate(values, target=100)
        assert len(result) == 100

    def test_target_zero_returns_identity(self):
        # target ≤ 0 → garde-fou : retourne la liste inchangée
        values = list(range(20))
        result = decimate(values, target=0)
        assert result == values

    def test_default_target_1000(self):
        # 5000 points → 1000 par défaut
        values = list(range(5000))
        result = decimate(values)
        assert len(result) == 1000

    def test_first_value_preserved(self):
        # Le 1er échantillon (i=0 → values[0]) doit être conservé
        values = list(range(1, 5001))  # 1..5000
        result = decimate(values, target=10)
        assert result[0] == 1

    def test_handles_tuples_and_strings(self):
        # decimate utilise list(values) → accepte tout itérable indexable
        values = tuple(range(500))
        result = decimate(values, target=50)
        assert len(result) == 50
        assert isinstance(result, list)

    def test_negative_target_returns_identity(self):
        values = list(range(20))
        # target < 0 → garde-fou activé via `target <= 0`
        result = decimate(values, target=-5)
        assert result == values


class TestWeekdayFr:
    @pytest.mark.parametrize("iso,expected", [
        ("2026-08-13", "jeudi"),
        ("2025-08-13", "mercredi"),
        ("2026-08-16", "dimanche"),
    ])
    def test_jours(self, iso, expected):
        assert weekday_fr(date.fromisoformat(iso)) == expected

    def test_accepte_un_timestamp_pandas(self):
        assert weekday_fr(pd.Timestamp("2026-08-13 07:53")) == "jeudi"

    @pytest.mark.parametrize("value", [None, "2026-08-13", 42])
    def test_valeurs_invalides(self, value):
        assert weekday_fr(value) == ""


def test_md_escape_neutralises_images_links_and_emphasis():
    """Audit : un nom d'activité Garmin rendu en Markdown pouvait charger une image distante."""
    from formatting import md_escape
    out = md_escape("![x](https://tiers.example/p.png) **gras** _it_\n\nsuite")
    assert "![" not in out and "](" not in out and "**" not in out.replace("\\*", "")
    assert "\n" not in out
    assert md_escape("Sortie longue 18 km") == "Sortie longue 18 km"


def test_pace_is_rounded_like_the_plan():
    """Audit : 299,6 s/km donnait « 4:59 » dans le plan et « 4:58 » ailleurs."""
    from formatting import seconds_to_pace_str
    assert seconds_to_pace_str(298.6) == "4:59/km"
    assert seconds_to_pace_str(299.6) == "5:00/km"
    assert seconds_to_pace_str(300.4) == "5:00/km"



@pytest.mark.parametrize("name, forbidden", [
    (":material/warning: danger", ":material"),     # icône Streamlit (le backslash n'y suffit pas)
    (":smile: bonne sortie", ":smile:"),               # emoji
    (":red[attention]", ":red["),                      # couleur
    ("&#58;material/warning&#58;", "&#58;material"),   # entité fournie par le nom lui-même
])
def test_md_escape_neutralises_streamlit_directives(name, forbidden):
    """Rendu vérifié dans le navigateur : « &#58; » s'affiche « : » sans rien activer."""
    from formatting import md_escape
    out = md_escape(name)
    assert forbidden not in out and ":" not in out


def test_md_escape_keeps_ordinary_text_readable():
    from formatting import md_escape
    assert md_escape("Tom & Jerry 10:30") == "Tom &amp; Jerry 10&#58;30"
