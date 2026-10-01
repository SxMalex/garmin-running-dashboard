"""
Chaleur et jour de course. Logique pure, testée.

- **Chaleur** : règle « température + point de rosée » (°F) de Mark Hadley,
  répandue chez les coureurs : au-delà de 100, l'allure à effort égal se dégrade
  par paliers (0,5 % … 10 %). Elle sert à relire une sortie passée (« 5:40 par
  30 °C valent ~5:28 au frais ») et à régler l'objectif du jour J.
- **Allure par kilomètre** sur le GPX du parcours, à effort égal : coût
  énergétique de la course selon la pente (Minetti et al. 2002, J Appl Physiol).
  Les descentes aident moins qu'elles ne coûtent en montée : le gain en descente
  est plafonné, on ne « rattrape » pas tout.
- **Stratégie** : à effort égal seul, un parcours plat donnait la même allure à
  chaque kilomètre — mauvais conseil, on part alors trop vite dans l'euphorie du
  départ. La stratégie « progressive » part 1,5 à 2,5 % plus lentement (le temps
  que le cardio s'installe), tient l'allure au milieu et accélère sur le dernier
  cinquième : léger *negative split*, la répartition des records du 5 km au
  marathon (Abbiss & Laursen 2008 ; Díaz et al. 2018). Le temps visé est conservé.
- **Ravitaillement** : repères de Jeukendrup (2014) selon la durée — rien sous
  ~75 min, 30-60 g de glucides/h jusqu'à 2 h 30, 60-90 g/h au-delà ; des prises
  régulières placées au kilomètre, avant les montées plutôt qu'en plein effort.

Sécurité : un GPX est un XML fourni par l'utilisateur. On refuse les fichiers
de plus de 5 Mo (et Streamlit plafonne l'upload à 5 Mo, `server.maxUploadSize`),
puis toute déclaration DOCTYPE / ENTITY, détectée par une pré-passe expat — donc
quel que soit l'encodage (UTF-16…) ou sa position dans le fichier, là où un
filtre sur les octets se contournait. expat (≥ 2.4.1) bloque de toute façon
l'explosion d'entités et ne résout pas les entités externes : la pré-passe est
une défense de plus, et un message clair.

Tracé : la trace (`trk`) prime sur la route (`rte`) — beaucoup de GPX
d'organisateurs ou de BaseCamp portent les deux, et les mettre bout à bout
triplait la distance. Plusieurs traces qui se suivent (≤ 200 m) sont enchaînées ;
sinon (variantes 10 km / semi dans un même fichier) la plus longue est retenue.
Un point illisible est écarté, un tracé inexploitable lève `GpxError`.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
import xml.parsers.expat

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Chaleur
# ---------------------------------------------------------------------------
# (seuil T+Td en °F, ralentissement bas, haut) — Hadley
_HEAT_TABLE = [(100, 0.0, 0.0), (110, 0.0, 0.5), (120, 0.5, 1.0), (130, 1.0, 2.0),
               (140, 2.0, 3.0), (150, 3.0, 4.5), (160, 4.5, 6.0), (170, 6.0, 8.0),
               (180, 8.0, 10.0)]


def c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def f_to_c(f: float) -> float:
    return (f - 32) * 5 / 9


def heat_slowdown(temp_c: float | None, dewpoint_c: float | None) -> dict | None:
    """
    Ralentissement attendu (en %) à effort égal. `hard` = au-delà du barème :
    on déconseille de courir dur, pas un pourcentage.
    """
    if temp_c is None or dewpoint_c is None:
        return None
    score = c_to_f(temp_c) + c_to_f(dewpoint_c)
    for limit, lo, hi in _HEAT_TABLE:
        if score <= limit:
            return {"score": score, "low": lo, "high": hi, "mid": (lo + hi) / 2, "hard": False}
    return {"score": score, "low": 10.0, "high": 12.0, "mid": 11.0, "hard": True}


def cool_equivalent_pace(pace_sec: float, heat: dict | None) -> float | None:
    """Allure équivalente au frais d'une sortie courue au chaud."""
    if not pace_sec or heat is None:
        return None
    return pace_sec / (1 + heat["mid"] / 100)


def weather_from_garmin(raw: dict | None) -> dict | None:
    """Réponse `get_activity_weather` (températures en °F) → °C lisibles."""
    if not isinstance(raw, dict) or raw.get("temp") is None:
        return None
    temp_c = f_to_c(float(raw["temp"]))
    dew = raw.get("dewPoint")
    desc = raw.get("weatherTypeDTO") or {}
    return {"temp_c": temp_c, "dewpoint_c": f_to_c(float(dew)) if dew is not None else None,
            "humidity": raw.get("relativeHumidity"),
            "wind_kmh": float(raw["windSpeed"]) * 1.609 if raw.get("windSpeed") is not None else None,
            "desc": desc.get("desc") if isinstance(desc, dict) else None}


# ---------------------------------------------------------------------------
# GPX → profil au kilomètre
# ---------------------------------------------------------------------------
MAX_GPX_BYTES = 5 * 1024 * 1024


class GpxError(ValueError):
    pass


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


_DTD_REFUSED = "GPX refusé : les déclarations DOCTYPE/ENTITY ne sont pas acceptées."
JOIN_M = 200.0          # boucle bouclée / tour enchaîné : départ et arrivée à moins de 200 m
SEGMENT_GAP_M = 1000.0  # au-delà, deux segments ou deux traces ne sont plus une coupure GPS
MIN_TRACK_M = 100.0
ELE_RANGE_M = (-500.0, 9000.0)   # altitude plausible sur Terre ; hors plage : illisible


def _reject_dtd(data: bytes) -> None:
    """Pré-passe expat : DOCTYPE ou ENTITY → GpxError, dans tout encodage."""
    def refuse(*_args):
        raise GpxError(_DTD_REFUSED)

    parser = xml.parsers.expat.ParserCreate()
    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    try:
        parser.Parse(data, True)
    except GpxError:
        raise
    except (xml.parsers.expat.ExpatError, ValueError, LookupError) as e:
        # ValueError : encodage multi-octets déclaré sans BOM (« utf-16-le ») ;
        # LookupError : encodage déclaré inconnu de Python (« EBCDIC-XYZ »).
        raise GpxError(f"GPX illisible : {e}") from None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _number(text) -> float:
    """Nombre GPX ; la virgule décimale de certains exports est tolérée. NaN si illisible."""
    try:
        v = float(str(text).strip().replace(",", "."))
    except (TypeError, ValueError):
        return math.nan
    return v if math.isfinite(v) else math.nan


def _points(parent, tag: str) -> tuple[list[tuple[float, float, float]], int]:
    """(points lat/lon/ele valides, nombre de points écartés) des enfants `tag` de `parent`."""
    pts, bad = [], 0
    for el in parent:
        if _local(el.tag) != tag:
            continue
        lat, lon = _number(el.attrib.get("lat")), _number(el.attrib.get("lon"))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):              # NaN compris
            bad += 1
            continue
        ele = next((_number(c.text) for c in el if _local(c.tag) == "ele"), math.nan)
        if not ELE_RANGE_M[0] <= ele <= ELE_RANGE_M[1]:
            ele = math.nan             # 1e308 : pente NaN, équivalent plat faux ; interpolée
        pts.append((lat, lon, ele))
    return pts, bad


LAP_TOLERANCE = 0.05     # deux tours d'une même boucle : longueurs à ±5 %
LAP_MATCH_M = 60.0       # …et chaque point du tour suivant à < 60 m du précédent


def _same_path(a: np.ndarray, b: np.ndarray) -> bool:
    """`b` repasse-t-il sur `a` (un tour de plus de la même boucle) ? Échantillonné : quelques ms."""
    a_s = a[:: max(1, len(a) // 1500)]
    b_s = b[:: max(1, len(b) // 25)]
    d = _haversine_m(b_s[:, None, 0], b_s[:, None, 1], a_s[None, :, 0], a_s[None, :, 1])
    return bool((d.min(axis=1) <= LAP_MATCH_M).mean() >= 0.9)


def _best_course(pieces: list[np.ndarray]) -> tuple[pd.DataFrame | None, int]:
    """
    Pièces (une par trace, ou par route) → (le parcours retenu, nombre de
    parcours distincts). Deux pièces s'enchaînent si la suivante part à moins
    de `SEGMENT_GAP_M` de la fin de la chaîne (le même seuil qu'entre deux
    segments d'une trace : une pause GPS ne dépend pas de l'encodage) ET que la
    chaîne n'est pas déjà bouclée (sinon deux variantes partant de la même
    arche s'additionnaient) — sauf si la suivante est un TOUR de plus de la
    même boucle (boucle fermée, même longueur, même tracé : marathon en deux
    tours de semi, une trace par tour ; un aller-retour n'est pas un tour). Longueurs
    calculées en un seul passage vectoriel : un fichier de milliers de traces
    ne construit qu'un DataFrame, celui du parcours retenu.
    """
    pieces = [p for p in pieces if len(p)]
    if not pieces:
        return None, 0
    allp = np.concatenate(pieces)
    ids = np.repeat(np.arange(len(pieces)), [len(p) for p in pieces])
    steps = _haversine_m(allp[:-1, 0], allp[:-1, 1], allp[1:, 0], allp[1:, 1])
    inside = ids[1:] == ids[:-1]
    length = np.bincount(ids[1:][inside], weights=steps[inside], minlength=len(pieces))
    first = np.r_[0, np.flatnonzero(~inside) + 1]
    last = np.r_[np.flatnonzero(~inside), len(allp) - 1]

    def gap(a, b) -> float:
        return float(_haversine_m(allp[a, 0], allp[a, 1], allp[b, 0], allp[b, 1]))

    chains = [{"pieces": [0], "len": float(length[0]), "start": first[0], "end": last[0]}]
    for j in range(1, len(pieces)):
        c = chains[-1]
        joined = gap(c["end"], first[j])
        prev = c["pieces"][-1]
        lap = (joined <= JOIN_M and length[prev] > 0
               and gap(first[prev], last[prev]) <= JOIN_M
               and abs(length[j] / length[prev] - 1) <= LAP_TOLERANCE
               and _same_path(pieces[prev], pieces[j]))
        if joined <= SEGMENT_GAP_M and (gap(c["start"], c["end"]) > JOIN_M or lap):
            c["laps"] = c.get("laps", 1) + (1 if lap else 0)
            c["pieces"].append(j)
            c["len"] += joined + float(length[j])
            c["end"] = last[j]
        else:
            chains.append({"pieces": [j], "len": float(length[j]), "start": first[j], "end": last[j]})
    best = max(chains, key=lambda c: c["len"])
    if best["len"] < MIN_TRACK_M:
        return None, len(chains)
    pts = np.concatenate([pieces[i] for i in best["pieces"]])
    df = pd.DataFrame(pts, columns=["lat", "lon", "ele"])
    step = _haversine_m(df["lat"].shift(), df["lon"].shift(), df["lat"], df["lon"]).fillna(0)
    df["dist"] = step.cumsum()
    df.attrs["laps"] = best.get("laps", 1)
    return df, len(chains)


def parse_gpx(data: bytes) -> pd.DataFrame:
    """
    Points du tracé : lat, lon, ele (m), dist (m, cumulée). `attrs["note"]`
    signale un choix fait à la place de l'utilisateur (parcours retenu, route
    faute de trace, points écartés) ; la page l'affiche.
    """
    if len(data) > MAX_GPX_BYTES:
        raise GpxError("Fichier trop gros (5 Mo maximum).")
    _reject_dtd(data)
    try:
        root = ET.fromstring(data)
    except (ET.ParseError, ValueError) as e:
        raise GpxError(f"GPX illisible : {e}") from None

    bad = 0
    tracks, routes = [], []
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "trk":
            # Les segments d'une trace se suivent (GPS coupé en courant) : écart
            # compté — jusqu'à SEGMENT_GAP_M. Au-delà, ce n'est plus une coupure
            # mais un autre parcours collé dans la même trace : pièce à part
            # (enchaînée ou non par les règles des traces), jamais le saut compté.
            pts = []
            for seg in (c for c in el if _local(c.tag) == "trkseg"):
                seg_pts, n_bad = _points(seg, "trkpt")
                bad += n_bad
                if pts and seg_pts and float(_haversine_m(pts[-1][0], pts[-1][1], seg_pts[0][0],
                                                          seg_pts[0][1])) > SEGMENT_GAP_M:
                    tracks.append(np.array(pts, dtype=float).reshape(-1, 3))
                    pts = []
                pts += seg_pts
            tracks.append(np.array(pts, dtype=float).reshape(-1, 3))
        elif tag == "rte":
            pts, n_bad = _points(el, "rtept")
            bad += n_bad
            routes.append(np.array(pts, dtype=float).reshape(-1, 3))

    notes = []
    df, n_courses = _best_course(tracks)
    route, n_routes = _best_course(routes)
    track_km = df["dist"].iloc[-1] / 1000 if df is not None else 0.0
    if route is not None and (df is None or route["dist"].iloc[-1] > 2 * df["dist"].iloc[-1]):
        # Pas de trace, trace inexploitable, ou trace bien plus courte que la route
        # (un bout de trace de 150 m à côté du parcours complet) : la route.
        had_track = any(len(t) for t in tracks)
        notes.append((f"La trace enregistrée ({track_km:.2f} km) est bien plus courte que la route : "
                      if df is not None else
                      "La trace enregistrée est inexploitable (trop courte) : " if had_track else
                      "Pas de trace enregistrée : ")
                     + "calcul sur la route (points de passage), moins précise en distance.")
        df, n_courses = route, n_routes
    if df is None:
        if not any(len(p) for p in tracks + routes):
            raise GpxError("Aucun tracé dans ce GPX (il faut des points trkpt ou rtept valides).")
        raise GpxError("Tracé trop court ou dégénéré (moins de 100 m) : ce n'est pas un parcours.")
    if df["ele"].isna().all():
        df["ele"] = 0.0
    df["ele"] = df["ele"].interpolate(limit_direction="both")
    if df.attrs.get("laps", 1) > 1:
        notes.insert(0, f"{df.attrs['laps']} tours de la même boucle enchaînés "
                        f"({df['dist'].iloc[-1] / 1000:.1f} km).")
    if n_courses > 1:
        notes.insert(0, f"{n_courses} parcours distincts dans le fichier : le plus long "
                        f"({df['dist'].iloc[-1] / 1000:.1f} km) est retenu.")
    if bad:
        notes.append(f"{bad} point(s) illisible(s) écarté(s).")
    df.attrs["note"] = " ".join(notes)
    return df


def km_profile(track: pd.DataFrame, smooth_m: float = 150.0) -> pd.DataFrame:
    """
    Un segment par kilomètre (le dernier partiel) : km, length_m, gain, loss,
    grade (pente nette). L'altitude est lissée sur ~150 m : le bruit GPS/baro
    gonflerait le dénivelé.
    """
    dist = track["dist"].to_numpy()
    grid = np.arange(0, dist[-1] + 1, 10.0)                        # rééchantillonnage 10 m
    ele = np.interp(grid, dist, track["ele"].to_numpy())
    win = max(1, int(smooth_m / 10))
    ele = pd.Series(ele).rolling(win, center=True, min_periods=1).mean().to_numpy()
    rows = []
    edges = list(np.arange(0, dist[-1], 1000.0)) + [dist[-1]]
    for k in range(len(edges) - 1):
        a, b = edges[k], edges[k + 1]
        if b - a < 50:                                              # reliquat négligeable
            continue
        seg = ele[(grid >= a) & (grid <= b)]
        diff = np.diff(seg) if len(seg) > 1 else np.array([0.0])
        rows.append({"km": k + 1, "length_m": b - a, "gain": float(diff[diff > 0].sum()),
                     "loss": float(-diff[diff < 0].sum()),
                     "grade": float((seg[-1] - seg[0]) / (b - a)) if len(seg) > 1 else 0.0,
                     "ele_end": float(seg[-1])})
    if not rows:
        raise GpxError("Tracé inexploitable : aucun segment de distance mesurable.")
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Temps visé : lecture et garde-fous
# ---------------------------------------------------------------------------
PLAUSIBLE_PACE_S = (150, 1200)    # 2:30 à 20:00 /km À PLAT : au-delà, c'est une faute de frappe


def reading_distance(total_km: float) -> str:
    """
    Distance à passer à `parse_race_time` pour lire « 1:45 » : en heures dès
    18 km (semi, trail), en minutes en dessous. Sans ça, un GPX de semi lisait
    « 1:45 » comme 105 secondes.
    """
    return "Semi-marathon" if total_km >= 18 else "10 km"


def flat_equivalent_km(profile: pd.DataFrame) -> float:
    """Distance « à plat » équivalente en effort (un km de côte en vaut plusieurs)."""
    return float(sum(l / 1000 * effort_factor(g) for l, g in zip(profile["length_m"], profile["grade"])))


def implausible_target(target_s: float | None, total_km: float,
                       flat_km: float | None = None) -> str | None:
    """
    Message si le temps visé donne une allure absurde (format mal lu, champ
    périmé). Jugée à l'allure À PLAT équivalente (`flat_km`) : un kilomètre
    vertical à 17 min/km est une allure de coureur, pas une faute de frappe.
    """
    if not target_s or total_km <= 0:
        return None
    pace = target_s / total_km
    flat_pace = target_s / flat_km if flat_km else pace
    lo, hi = PLAUSIBLE_PACE_S
    if lo <= flat_pace <= hi:
        return None
    return (f"Ce temps donne {fmt_pace(pace)}/km sur {total_km:.1f} km : vérifie le format "
            "(h:mm:ss pour un semi ou plus, mm:ss en dessous).")


def goal_distance_for(total_km: float, goal_distance: str | None,
                      distances: dict[str, float]) -> str | None:
    """
    La distance d'objectif que ce GPX représente (±5 %), sinon None : le GPX
    officiel de la course visée reprend alors le temps visé enregistré.
    """
    if goal_distance in distances and abs(total_km / distances[goal_distance] - 1) <= 0.05:
        return goal_distance
    return None


def course_prediction(predictions: dict[float, float], total_km: float) -> float | None:
    """Temps prédit sur `total_km` : la prédiction de la distance la plus proche, projetée par Riegel."""
    if not predictions or total_km <= 0:
        return None
    km = min(predictions, key=lambda d: abs(math.log(d / total_km)))
    return predictions[km] * (total_km / km) ** 1.06


# ---------------------------------------------------------------------------
# Allure à effort égal
# ---------------------------------------------------------------------------
DOWNHILL_FLOOR = 0.88   # on ne va pas plus de ~12 % plus vite qu'à plat, même en descente


def minetti_cost(grade: float) -> float:
    """Coût énergétique de la course (J/kg/m) en fonction de la pente (fraction)."""
    i = max(-0.45, min(0.45, grade))
    return 155.4 * i**5 - 30.4 * i**4 - 43.3 * i**3 + 46.3 * i**2 + 19.5 * i + 3.6


def effort_factor(grade: float) -> float:
    """Allure à effort égal ÷ allure à plat pour une pente (fraction), gain en descente plafonné."""
    return max(minetti_cost(grade) / minetti_cost(0.0), DOWNHILL_FLOOR)


PACING_STRATEGIES = {"progressive": "Progressive", "even": "Régulière"}


def progression_shape(total_km: float) -> dict:
    """
    Paramètres de la stratégie progressive selon la distance : départ plus lent
    de `start_pct` % qui s'efface sur `warm_frac` de la course, accélération
    jusqu'à `kick_pct` % à partir de `kick_from`. Plus la course est longue, plus
    le départ est retenu et l'accélération finale modeste (le marathon se perd
    dans la première moitié, il ne se gagne pas au sprint).
    """
    if total_km <= 12:
        return {"start_pct": 1.5, "warm_frac": 0.15, "kick_pct": 2.0, "kick_from": 0.8}
    if total_km <= 25:
        return {"start_pct": 2.0, "warm_frac": 0.15, "kick_pct": 2.0, "kick_from": 0.8}
    return {"start_pct": 2.5, "warm_frac": 0.12, "kick_pct": 1.5, "kick_from": 0.85}


def strategy_multipliers(profile: pd.DataFrame, strategy: str = "even") -> np.ndarray:
    """Multiplicateur d'allure par segment (1 = allure de base ; > 1 = plus lent)."""
    km = profile["length_m"].to_numpy() / 1000
    if strategy != "progressive" or km.sum() <= 0:
        return np.ones(len(km))
    total = float(km.sum())
    x = (np.cumsum(km) - km / 2) / total                            # milieu du segment, 0 → 1
    s = progression_shape(total)
    slow = s["start_pct"] / 100 * np.clip(1 - x / s["warm_frac"], 0, 1)
    fast = s["kick_pct"] / 100 * np.clip((x - s["kick_from"]) / (1 - s["kick_from"]), 0, 1)
    return 1 + slow - fast


def pacing_plan(profile: pd.DataFrame, target_time_s: float, strategy: str = "even") -> pd.DataFrame:
    """
    Allure par kilomètre pour finir en `target_time_s` : effort égal selon la
    pente, modulé par la stratégie (`even` ou `progressive`). Ajoute pace_s
    (s/km), split_s (temps du segment) et elapsed_s (cumul). Le temps final vaut
    `target_time_s` quelle que soit la stratégie.
    """
    if profile.empty or not target_time_s or target_time_s <= 0:
        raise ValueError("Profil vide ou temps visé invalide.")
    if strategy not in PACING_STRATEGIES:
        raise ValueError(f"Stratégie inconnue : {strategy}")
    factor = np.array([effort_factor(g) for g in profile["grade"]])
    mult = strategy_multipliers(profile, strategy)
    km = profile["length_m"].to_numpy() / 1000
    base = target_time_s / float((factor * mult * km).sum())      # allure à plat (s/km)
    out = profile.copy()
    out["pace_s"] = base * factor * mult
    out["split_s"] = out["pace_s"] * km
    out["elapsed_s"] = out["split_s"].cumsum()
    out.attrs["flat_pace_s"] = base
    return out


def half_split_pct(plan: pd.DataFrame) -> float:
    """Écart d'allure 2e moitié / 1re moitié du plan, en % (< 0 = negative split)."""
    return split_pct(plan["length_m"].to_numpy(), plan["split_s"].to_numpy())


def split_pct(lengths_m, times_s) -> float:
    """
    Allure de la 2e moitié de la distance comparée à la 1re, en % (positif = on
    a ralenti). Le segment qui chevauche la mi-course est réparti au prorata.
    """
    lengths = np.asarray(lengths_m, dtype=float)
    times = np.asarray(times_s, dtype=float)
    half = lengths.sum() / 2
    first_d = first_t = 0.0
    for d, t in zip(lengths, times):
        take = min(d, max(half - first_d, 0.0))
        if take <= 0:
            break
        first_d += take
        first_t += t * take / d
    second_t = times.sum() - first_t
    second_d = lengths.sum() - first_d
    if first_d <= 0 or second_d <= 0 or first_t <= 0:
        return 0.0
    return float((second_t / second_d) / (first_t / first_d) - 1) * 100


# ---------------------------------------------------------------------------
# Ravitaillement
# ---------------------------------------------------------------------------
def carbs_per_hour(total_s: float) -> tuple[int, int]:
    h = total_s / 3600
    if h < 1.25:
        return 0, 0
    if h <= 2.5:
        return 30, 60
    return 60, 90


def fueling_plan(plan: pd.DataFrame, every_min: int = 25, gel_g: int = 25) -> dict:
    """
    Prises de glucides placées au kilomètre. La première après ~30 min, puis
    toutes les `every_min` minutes ; décalée d'un km plus tôt si le suivant monte
    fort (on mange avant la côte, pas dedans).
    """
    total = float(plan["elapsed_s"].iloc[-1])
    lo, hi = carbs_per_hour(total)
    events = []
    if hi:
        elapsed = plan["elapsed_s"].to_numpy()
        t = 30 * 60
        while t < total - 10 * 60:                                  # rien dans les 10 dernières minutes
            # Repère kilométrique déjà franchi à l'instant t (jamais après) : la
            # prise se fait « au km N », N = kilomètres bouclés.
            done = int(np.searchsorted(elapsed, t, side="right"))
            # Le kilomètre suivant monte fort : on mange au repère d'avant.
            if done < len(plan) and plan["grade"].iloc[done] > 0.03 and done > 1:
                done -= 1
            if done >= 1 and (not events or events[-1]["km"] != done):
                events.append({"km": done, "at_s": float(elapsed[done - 1]),
                               "what": f"1 gel ou équivalent (~{gel_g} g de glucides) + quelques gorgées"})
            t += every_min * 60
    return {"total_s": total, "carbs_g_per_h": (lo, hi), "water_ml_per_h": (400, 800) if hi else (0, 0),
            "events": events,
            "note": ("Moins de 75 min : l'eau suffit, un petit-déjeuner riche en glucides 2-3 h avant."
                     if not hi else "À tester à l'entraînement avant : on ne change rien le jour J.")}


def fmt_pace(sec: float) -> str:
    s = int(round(sec))
    return f"{s // 60}:{s % 60:02d}"


def fmt_clock(sec: float) -> str:
    s = int(round(sec))
    h, m = divmod(s // 60, 60)
    return f"{h}h{m:02d}" if h else f"{m} min"
