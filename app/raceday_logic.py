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

Sécurité : un GPX est un XML fourni par l'utilisateur. On refuse les DOCTYPE /
ENTITY (explosion d'entités) et les fichiers de plus de 5 Mo.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

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


def parse_gpx(data: bytes) -> pd.DataFrame:
    """Points du tracé : lat, lon, ele (m), dist (m, cumulée)."""
    if len(data) > MAX_GPX_BYTES:
        raise GpxError("Fichier trop gros (5 Mo maximum).")
    head = data[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in data.upper():
        raise GpxError("GPX refusé : les déclarations DOCTYPE/ENTITY ne sont pas acceptées.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise GpxError(f"GPX illisible : {e}") from None
    pts = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("trkpt", "rtept") and "lat" in el.attrib and "lon" in el.attrib:
            ele = next((c.text for c in el if c.tag.rsplit("}", 1)[-1] == "ele"), None)
            pts.append((float(el.attrib["lat"]), float(el.attrib["lon"]),
                        float(ele) if ele not in (None, "") else np.nan))
    if len(pts) < 2:
        raise GpxError("Aucun tracé dans ce GPX (il faut des points trkpt ou rtept).")
    df = pd.DataFrame(pts, columns=["lat", "lon", "ele"])
    step = _haversine_m(df["lat"].shift(), df["lon"].shift(), df["lat"], df["lon"]).fillna(0)
    df["dist"] = step.cumsum()
    if df["ele"].isna().all():
        df["ele"] = 0.0
    df["ele"] = df["ele"].interpolate(limit_direction="both")
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
    return pd.DataFrame(rows)


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
