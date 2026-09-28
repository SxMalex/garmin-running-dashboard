"""
Génère les cartes de test GPX (`python tests/fixtures/gpx/make_cards.py`).

Chaque carte reproduit une forme de fichier rencontrée en vrai : export Garmin
Connect (extensions FC/cadence), export sans altitude, fichier d'organisateur
BaseCamp (route + trace), route seule (plotaroute), trace coupée en segments,
deux variantes dans un fichier, marathon en deux traces, UTF-16, virgule
décimale, points illisibles, et les fichiers hostiles (entités, DOCTYPE caché).
Tracés synthétiques (cercles autour de Toulouse) : aucune donnée réelle.
Les distances attendues vivent dans `CARDS` (lu par tests/test_gpx_cards.py).
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAT0, LON0 = 43.6045, 1.4440
M_PER_DEG_LAT = 111_320.0


def loop(km: float, step_m: float = 50.0, lat0=LAT0, lon0=LON0, climb_m: float = 30.0,
         start_frac: float = 0.0, end_frac: float = 1.0):
    """Boucle circulaire de `km` km (portion [start_frac, end_frac]), altitude sinusoïdale."""
    r = km * 1000 / (2 * math.pi)
    n = max(int(km * 1000 / step_m), 8)
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    pts = []
    for k in range(int(n * start_frac), int(n * end_frac) + 1):
        a = 2 * math.pi * k / n
        lat = lat0 + (r * math.sin(a)) / M_PER_DEG_LAT
        lon = lon0 + (r * (1 - math.cos(a))) / m_per_deg_lon
        ele = 150 + climb_m * (1 - math.cos(a)) / 2
        pts.append((lat, lon, ele))
    return pts


def trkpts(pts, ele=True, ext=False, t0=datetime(2026, 9, 20, 8, 0, 0), comma=False):
    out = []
    for i, (lat, lon, e) in enumerate(pts):
        value = f"{e:.1f}".replace(".", ",") if comma else f"{e:.1f}"
        inner = f"<ele>{value}</ele>" if ele else ""
        if ext:
            inner += (f"<time>{(t0 + timedelta(seconds=15 * i)).isoformat()}Z</time><extensions>"
                      f"<ns3:TrackPointExtension><ns3:hr>{140 + i % 20}</ns3:hr>"
                      f"<ns3:cad>88</ns3:cad></ns3:TrackPointExtension></extensions>")
        out.append(f'<trkpt lat="{lat:.7f}" lon="{lon:.7f}">{inner}</trkpt>')
    return "".join(out)


def gpx(body: str, creator="Garmin Connect") -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<gpx creator="{creator}" version="1.1" xmlns="http://www.topografix.com/GPX/1/1" '
            'xmlns:ns3="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">'
            f"<metadata><name>carte de test</name></metadata>{body}</gpx>")


def trk(*segments: str, name="Course") -> str:
    return f"<trk><name>{name}</name>" + "".join(f"<trkseg>{s}</trkseg>" for s in segments) + "</trk>"


def rte(pts, name="Parcours") -> str:
    return f"<rte><name>{name}</name>" + "".join(
        f'<rtept lat="{lat:.7f}" lon="{lon:.7f}"><ele>{e:.1f}</ele></rtept>' for lat, lon, e in pts) + "</rte>"


# nom → (contenu, km attendus ou None, erreur attendue ou None, extrait de note attendu)
CARDS: dict[str, tuple] = {}


def card(name, content, km=None, error=None, note=None, encoding="utf-8"):
    CARDS[name] = (km, error, note)
    (_TARGET / name).write_bytes(content.encode(encoding) if isinstance(content, str) else content)


_TARGET = HERE


def build(target: Path = HERE) -> None:
    """Écrit les cartes dans `target` (le dépôt par défaut ; un dossier jetable pour les tests)."""
    global _TARGET
    _TARGET = Path(target)
    CARDS.clear()
    card("garmin_connect_10k.gpx", gpx(trk(trkpts(loop(10), ext=True))), km=10.0)
    card("no_elevation_semi.gpx", gpx(trk(trkpts(loop(21.0975), ele=False)), creator="StravaGPX"),
         km=21.0975)
    # Organisateur (BaseCamp) : 24 points de passage + la trace fine du même 10 km.
    card("organizer_route_and_track.gpx",
         gpx(rte(loop(10, step_m=420)) + trk(trkpts(loop(10))), creator="BaseCamp"), km=10.0)
    card("route_only.gpx", gpx(rte(loop(10, step_m=25)), creator="plotaroute.com"), km=10.0,
         note="route")
    # GPS coupé deux fois en courant : 3 segments, trous de ~100 m comptés.
    whole = loop(10)
    card("segments_with_gps_gaps.gpx",
         gpx(trk(trkpts(whole[:60]), trkpts(whole[62:130]), trkpts(whole[132:]))), km=10.0)
    far = loop(21.0975, lat0=LAT0 + 0.03)
    card("two_variants_10k_and_semi.gpx", gpx(trk(trkpts(loop(10)), name="10 km") +
                                              trk(trkpts(far), name="Semi")),
         km=21.0975, note="parcours distincts")
    card("marathon_in_two_tracks.gpx",
         gpx(trk(trkpts(loop(42.195, end_frac=0.5)), name="Aller") +
             trk(trkpts(loop(42.195, start_frac=0.5)), name="Retour")), km=42.195)
    card("utf16_5k.gpx", gpx(trk(trkpts(loop(5)))).replace('encoding="UTF-8"', 'encoding="UTF-16"'),
         km=5.0, encoding="utf-16")
    card("utf16_entity_bomb.gpx",
         ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
          '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">]><gpx>&lol2;</gpx>'), error="DOCTYPE",
         encoding="utf-16")
    card("doctype_after_long_comment.gpx",
         '<?xml version="1.0"?><!--' + "x" * 5000 + '--><!DOCTYPE gpx SYSTEM "file:///etc/passwd">'
         + gpx(trk(trkpts(loop(5)))).split("?>", 1)[1], error="DOCTYPE")
    # Export « à la française » : virgule décimale dans l'altitude (40 m de D+ attendus).
    card("comma_decimal_elevation.gpx", gpx(trk(trkpts(loop(5, climb_m=40), comma=True))), km=5.0)
    # Deux points illisibles (latitude « nan », longitude « abc ») au milieu de la trace.
    pts = trkpts(loop(5)).split("</trkpt>")
    pts[10] = re.sub(r'lat="[^"]*"', 'lat="nan"', pts[10])
    pts[20] = re.sub(r'lon="[^"]*"', 'lon="abc"', pts[20])
    card("unreadable_points.gpx", gpx(trk("</trkpt>".join(pts))), km=5.0, note="illisible")
    card("degenerate_same_point.gpx", gpx(trk(trkpts([(LAT0, LON0, 150.0)] * 50))), error="trop court")
    card("mountain_trail_20k.gpx", gpx(trk(trkpts(loop(20, climb_m=800)))), km=20.0)
    # Organisateur : 10 km et semi partant de la MÊME arche (deux boucles) — ne pas les additionner.
    card("variants_same_start.gpx", gpx(trk(trkpts(loop(10)), name="10 km") +
                                        trk(trkpts(loop(21.0975)), name="Semi")),
         km=21.0975, note="parcours distincts")
    # Trace réduite à un marqueur de départ + route complète : la route sert.
    card("single_point_track_with_route.gpx",
         gpx(trk(trkpts(loop(10)[:1])) + rte(loop(10, step_m=25))), km=10.0, note="inexploitable")
    # Encodage multi-octets déclaré SANS BOM : refus propre, pas une trace Python.
    card("utf16le_without_bom.gpx",
         gpx(trk(trkpts(loop(5)))).replace('encoding="UTF-8"', 'encoding="utf-16-le"'),
         error="illisible", encoding="utf-16-le")
    # Marathon en deux tours d'un semi, une trace par tour : enchaînés (et dit).
    card("two_laps_marathon.gpx", gpx(trk(trkpts(loop(21.0975)), name="Tour 1") +
                                      trk(trkpts(loop(21.0975)), name="Tour 2")), km=42.195, note="tours")
    # Un bout de trace (150 m) à côté du parcours complet en route : la route, et dit.
    card("short_track_long_route.gpx", gpx(trk(trkpts(loop(10)[:4])) + rte(loop(10, step_m=25))),
         km=10.0, note="bien plus courte")
    card("empty_track_with_route.gpx", gpx("<trk><name>vide</name><trkseg></trkseg></trk>" +
                                           rte(loop(10, step_m=25))), km=10.0, note="route")


if __name__ == "__main__":
    build()
    print("\n".join(sorted(CARDS)))
