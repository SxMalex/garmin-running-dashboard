"""
Cartes de test GPX (tests/fixtures/gpx) : formes de fichiers rencontrées en vrai.
Chaque carte doit donner la bonne distance à ±1 %, ou être refusée proprement
(GpxError, jamais une exception Python brute), et ses choix doivent être dits.
"""

import atexit
import sys
import tempfile
from pathlib import Path

import pytest

CARDS_DIR = Path(__file__).resolve().parent / "fixtures" / "gpx"
sys.path.insert(0, str(CARDS_DIR))

import make_cards  # noqa: E402
from raceday_logic import GpxError, fueling_plan, km_profile, pacing_plan, parse_gpx  # noqa: E402

# Décrit les cartes (CARDS) en les générant dans un dossier jetable, nettoyé en
# fin de session : le dépôt n'est jamais réécrit (checkout en lecture seule).
_GENERATED = tempfile.TemporaryDirectory(prefix="gpx-cards-")
atexit.register(_GENERATED.cleanup)
make_cards.build(Path(_GENERATED.name))


def test_versioned_cards_match_the_generator(tmp_path):
    """Les cartes du dépôt sont celles du générateur (sans jamais réécrire le dépôt)."""
    make_cards.build(tmp_path)
    fresh = {p.name: p.read_bytes() for p in tmp_path.glob("*.gpx")}
    on_disk = {p.name: p.read_bytes() for p in CARDS_DIR.glob("*.gpx")}
    assert fresh.keys() == on_disk.keys(), set(fresh) ^ set(on_disk)
    assert [n for n in fresh if fresh[n] != on_disk[n]] == []


def test_every_card_on_disk_is_described():
    on_disk = {p.name for p in CARDS_DIR.glob("*.gpx")}
    assert on_disk == set(make_cards.CARDS)


def test_thousands_of_tiny_tracks_parse_fast():
    """Revue : 35 000 traces de 2 points coûtaient 30 s par rerun (un DataFrame par chaîne)."""
    import time
    pts = make_cards.loop(10)
    body = "".join(make_cards.trk(make_cards.trkpts([pts[i % len(pts)], pts[(i + 7) % len(pts)]]), name="t")
                   for i in range(24000))
    data = make_cards.gpx(body).encode()
    assert len(data) < 5 * 1024 * 1024                  # sous le plafond : il faut vraiment le parser
    t0 = time.perf_counter()
    track = parse_gpx(data)
    # 30 s avant la correction ; ~3 s ici, jusqu'à 6 s sur une machine chargée.
    assert time.perf_counter() - t0 < 10.0
    assert "parcours distincts" in track.attrs["note"]


@pytest.mark.parametrize("name", sorted(make_cards.CARDS))
def test_card(name):
    km, error, note = make_cards.CARDS[name]
    data = (CARDS_DIR / name).read_bytes()
    if error:
        with pytest.raises(GpxError, match=error):
            parse_gpx(data)
        return
    track = parse_gpx(data)
    got = track["dist"].iloc[-1] / 1000
    assert got == pytest.approx(km, rel=0.01), (name, got)
    if note:
        assert note in track.attrs["note"], track.attrs["note"]
    else:
        assert track.attrs["note"] == "", track.attrs["note"]
    # Toute la chaîne de la page doit tenir : profil, plan (deux stratégies), ravitaillement.
    prof = km_profile(track)
    assert prof["length_m"].sum() / 1000 == pytest.approx(km, rel=0.01)
    for strategy in ("even", "progressive"):
        plan = pacing_plan(prof, km * 300, strategy)
        assert plan["elapsed_s"].iloc[-1] == pytest.approx(km * 300, abs=1)
    fueling_plan(plan)


def test_trail_elevation_gain_is_not_inflated():
    prof = km_profile(parse_gpx((CARDS_DIR / "mountain_trail_20k.gpx").read_bytes()))
    assert prof["gain"].sum() == pytest.approx(800, rel=0.1)


def test_comma_decimal_elevation_is_read():
    prof = km_profile(parse_gpx((CARDS_DIR / "comma_decimal_elevation.gpx").read_bytes()))
    assert prof["gain"].sum() == pytest.approx(40, rel=0.15)        # pas 0 : l'altitude est lue


def test_absurd_elevation_keeps_a_finite_profile():
    """Revue #2 : `<ele>1e308</ele>` donnait une pente NaN et 26,9 km d'équivalent plat pour 5."""
    prof = km_profile(parse_gpx((CARDS_DIR / "absurd_elevation.gpx").read_bytes()))
    assert prof[["grade", "gain", "ele_end"]].notna().all().all()
    assert prof["gain"].sum() == pytest.approx(30, rel=0.15)


def test_route_and_track_uses_the_track_not_both():
    """Le bug de revue : 29 km pour un 10 km quand route et trace étaient enchaînées."""
    track = parse_gpx((CARDS_DIR / "organizer_route_and_track.gpx").read_bytes())
    assert track["dist"].iloc[-1] / 1000 < 10.2
