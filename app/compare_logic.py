"""
Calendrier et comparaison de deux sorties (page Calendrier). Logique pure, testée.

Comparer deux chronos bruts ne dit presque rien : un 10 km vallonné en plein été
et un 10 km plat en novembre ne se comparent qu'une fois ramenés au même terrain
et à la même météo. On compare donc :

- l'**allure corrigée** : pente (coût Minetti, `raceday_logic.effort_factor`) puis
  chaleur (règle de Hadley) ; entre deux distances différentes, projetée par
  Riegel (exposant 1,06) sur la distance de la première sortie ;
- la **gestion de course** : allure de la 2e moitié contre la 1re (à pente égale) ;
- le **bloc d'avant** : volume, sortie la plus longue, part en facile, charge
  CTL/TSB la veille (le TSB unique de `compute_pmc_series`), HRV, FC de repos et
  sommeil de la semaine précédente.

Deux sorties ne prouvent rien : le verdict dit « a coïncidé avec », jamais
« grâce à ».
"""

from __future__ import annotations

import calendar
import html
from datetime import date, timedelta
from typing import Mapping

import numpy as np
import pandas as pd

from activities_logic import enrich, polarization
from raceday_logic import effort_factor, heat_slowdown, split_pct

KINDS = {"race": "Courses", "training": "Entraînements", "both": "Les deux"}
KIND_LABELS = {"race": "Course", "training": "Entraînement", "other": "Autre sport"}
RIEGEL_EXPONENT = 1.06
BLOCK_WEEKS = 6
MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
             "septembre", "octobre", "novembre", "décembre"]
WEEKDAYS_FR = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]
HEALTH_NIGHTS = 7


def activity_kind(activity_type, workout_type) -> str:
    """`race` / `training` pour la course à pied, `other` pour le reste."""
    if activity_type != "running":
        return "other"
    return "race" if workout_type == "race" else "training"


def with_kind(df: pd.DataFrame) -> pd.DataFrame:
    """Ajoute `kind` et `day` (date locale) au DataFrame d'activités."""
    out = df.copy()
    if out.empty:
        out["kind"] = pd.Series(dtype=object)
        out["day"] = pd.Series(dtype=object)
        return out
    out["kind"] = [activity_kind(t, w) for t, w in zip(out["activityType"], out.get("workoutType"))]
    out["day"] = pd.to_datetime(out["startTimeLocal"]).dt.date
    return out


def filter_kind(df: pd.DataFrame, choice: str) -> pd.DataFrame:
    """Courses, entraînements ou les deux (toujours de la course à pied)."""
    kinded = df if "kind" in df else with_kind(df)
    wanted = {"race", "training"} if choice == "both" else {choice}
    return kinded[kinded["kind"].isin(wanted)]


def _fmt_duration(minutes) -> str:
    total = int(round(float(minutes or 0) * 60))
    h, rem = divmod(total, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def day_hover(acts: pd.DataFrame, day: date) -> str:
    """
    Infobulle d'un jour : date, puis chaque sortie (nom, distance, durée,
    allure, FC), la sortie retenue au clic en premier. Les noms Garmin sont
    saisis par l'utilisateur : échappés (Plotly interprète le HTML du survol).
    """
    lines = [f"<b>{WEEKDAYS_FR[day.weekday()].capitalize()} {day:%d/%m/%Y}</b>"]
    for _, a in acts.iterrows():
        pace = _num(a.get("avgPace_sec"))
        hr = _num(a.get("avgHR"))
        detail = [f"{float(a['distance_km']):.1f} km", _fmt_duration(a.get("duration_min"))]
        if pace:
            detail.append(f"{int(pace) // 60}:{int(pace) % 60:02d}/km")
        if hr:
            detail.append(f"{hr:.0f} bpm")
        name = html.escape(str(a.get("activityName") or "Course"))[:40]
        lines.append(f"{KIND_LABELS.get(a['kind'], '')} · {name}<br>   " + " · ".join(detail))
    return "<br>".join(lines)


def month_cells(df: pd.DataFrame, year: int, month: int) -> pd.DataFrame:
    """
    Une ligne par jour du mois : day, weekday (0 = lundi), week (ligne de la
    grille), km, n, kind (`race` > `training` > `none`), main_id (la sortie
    retenue au clic : la course du jour, sinon la plus longue), hover (texte
    de l'infobulle, vide sans sortie).
    """
    kinded = df if "kind" in df else with_kind(df)
    first = date(year, month, 1)
    n_days = calendar.monthrange(year, month)[1]
    offset = first.weekday()
    rows = []
    for d in range(1, n_days + 1):
        day = date(year, month, d)
        acts = kinded[kinded["day"] == day] if not kinded.empty else kinded
        if acts.empty:
            kind, main_id, km, n, hover = "none", None, 0.0, 0, ""
        else:
            ranked = acts.assign(_race=(acts["kind"] == "race").astype(int)).sort_values(
                ["_race", "distance_km"], ascending=False)
            main = ranked.iloc[0]
            kind = main["kind"]
            main_id = int(main["activityId"])
            km = float(acts["distance_km"].sum())
            n = len(acts)
            hover = day_hover(ranked, day)
        rows.append({"day": day, "weekday": day.weekday(), "week": (d - 1 + offset) // 7,
                     "km": km, "n": n, "kind": kind, "main_id": main_id, "hover": hover})
    cells = pd.DataFrame(rows)
    cells["main_id"] = pd.array(cells["main_id"].tolist(), dtype="Int64")   # NA, pas NaN flottant
    return cells


def months_with_activities(df: pd.DataFrame) -> list[tuple[int, int]]:
    """(année, mois) du plus récent au plus ancien, trous compris (on navigue mois par mois)."""
    if df.empty:
        return []
    days = pd.to_datetime(df["startTimeLocal"])
    lo, hi = days.min(), days.max()
    out, y, m = [], hi.year, hi.month
    while (y, m) >= (lo.year, lo.month):
        out.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


# ---------------------------------------------------------------------------
# Une sortie
# ---------------------------------------------------------------------------
def km_splits_frame(splits: list[dict]) -> pd.DataFrame:
    """
    Splits de `compute_km_splits` → km (distance cumulée), length_m, time_s,
    pace_s, gap_s (allure à plat équivalente), hr.
    """
    cols = ["km", "length_m", "time_s", "pace_s", "gap_s", "hr"]
    rows = [s for s in splits or [] if (s.get("distance_m") or 0) > 0 and (s.get("moving_s") or 0) > 0]
    if not rows:
        return pd.DataFrame(columns=cols)
    length = np.array([float(s["distance_m"]) for s in rows])
    time_s = np.array([float(s["moving_s"]) for s in rows])
    grade = np.array([float(s.get("elev_diff") or 0) for s in rows]) / length
    factor = np.array([effort_factor(g) for g in grade])
    pace = time_s / (length / 1000)
    return pd.DataFrame({"km": np.cumsum(length) / 1000, "length_m": length, "time_s": time_s,
                         "pace_s": pace, "gap_s": pace / factor,
                         "hr": [s.get("avg_hr") for s in rows]})


def _num(value) -> float | None:
    v = pd.to_numeric(value, errors="coerce")
    return float(v) if v is not None and np.isfinite(v) else None


def run_summary(row: Mapping, splits: list[dict] | None = None, weather: dict | None = None) -> dict:
    """
    Résumé comparable d'une sortie. `gap_s` = allure à plat équivalente (splits
    requis, sinon l'allure brute), `adjusted_s` = `gap_s` ramenée au frais,
    `split_pct` = 2e moitié contre 1re à pente égale (None sans splits).
    """
    frame = km_splits_frame(splits or [])
    pace = _num(row.get("avgPace_sec"))
    if not frame.empty:
        flat_time = frame["gap_s"].to_numpy() * frame["length_m"].to_numpy() / 1000
        gap = float(flat_time.sum() / (frame["length_m"].sum() / 1000))
        halves = split_pct(frame["length_m"], flat_time) if len(frame) >= 4 else None
    else:
        gap, halves = pace, None
    heat = heat_slowdown(weather.get("temp_c"), weather.get("dewpoint_c")) if weather else None
    adjusted = gap / (1 + heat["mid"] / 100) if gap and heat else gap
    return {
        "id": int(row["activityId"]),
        "date": pd.Timestamp(row["startTimeLocal"]).date(),
        "name": row.get("activityName") or "Course",
        "kind": activity_kind(row.get("activityType"), row.get("workoutType")),
        "distance_km": _num(row.get("distance_km")) or 0.0,
        "duration_min": _num(row.get("duration_min")) or 0.0,
        "pace_s": pace, "gap_s": gap, "adjusted_s": adjusted,
        "hr": _num(row.get("avgHR")), "elevation": _num(row.get("elevationGain")),
        "cadence": _num(row.get("avgCadence")),
        "heat": heat, "weather": weather, "split_pct": halves, "splits": frame,
    }


def equivalent_pace(pace_s: float, from_km: float, to_km: float) -> float:
    """Allure projetée d'une distance à l'autre (Riegel : t ∝ d^1,06)."""
    if not pace_s or from_km <= 0 or to_km <= 0:
        return pace_s
    return pace_s * (to_km / from_km) ** (RIEGEL_EXPONENT - 1)


# ---------------------------------------------------------------------------
# Le bloc d'avant
# ---------------------------------------------------------------------------
def training_block(activities: pd.DataFrame, pmc: pd.DataFrame, day: date,
                   threshold_sec: float, weeks: int = BLOCK_WEEKS) -> dict:
    """
    Les `weeks` semaines avant `day` (exclu) : km/semaine, nombre de sorties,
    sortie la plus longue, part du temps de course en facile, heures de sport
    croisé, et CTL / ATL / TSB en fin de veille.
    """
    start = pd.Timestamp(day - timedelta(days=weeks * 7))
    end = pd.Timestamp(day)
    when = pd.to_datetime(activities["startTimeLocal"])
    window = activities[(when >= start) & (when < end)]
    runs = window[window["activityType"] == "running"]
    polar = polarization(enrich(window, threshold_sec)) if not window.empty else None
    out = {
        "weeks": weeks,
        "km_week": float(runs["distance_km"].sum()) / weeks,
        "runs": len(runs),
        "longest_km": float(runs["distance_km"].max()) if not runs.empty else 0.0,
        "easy_share": polar["easy"] if polar else None,
        "cross_hours": float(window.loc[window["activityType"] != "running", "duration_min"].sum()) / 60,
        "ctl": None, "atl": None, "tsb": None,
    }
    if pmc is not None and not pmc.empty:
        eve = pd.Timestamp(day - timedelta(days=1))
        before = pmc[pd.to_datetime(pmc["date"]) <= eve]
        if not before.empty:
            last = before.iloc[-1]
            ctl, atl = round(float(last["ctl"]), 1), round(float(last["atl"]), 1)
            out.update(ctl=ctl, atl=atl, tsb=round(ctl - atl, 1))
    return out


def health_before(health: pd.DataFrame | None, sleep_rows: list[dict] | None, day: date,
                  nights: int = HEALTH_NIGHTS) -> dict:
    """
    Moyennes des `nights` nuits qui précèdent `day` (la nuit d'avant la sortie
    comprise : Garmin date une nuit du jour du réveil). `health` = frame de
    `illness_logic.daily_health_frame` ; `sleep_rows` = `get_sleep_range`.
    """
    lo, hi = pd.Timestamp(day - timedelta(days=nights - 1)), pd.Timestamp(day)
    out = {"hrv": None, "rhr": None, "sleep_h": None}
    if health is not None and not health.empty:
        part = health[(health.index >= lo) & (health.index <= hi)]
        for key in ("hrv", "rhr"):
            if key in part and part[key].notna().any():
                out[key] = float(part[key].mean())
    hours = [r["sleepTimeSeconds"] / 3600 for r in sleep_rows or []
             if isinstance(r, dict) and r.get("calendarDate") and r.get("sleepTimeSeconds")
             and lo <= pd.Timestamp(r["calendarDate"]) <= hi]
    if hours:
        out["sleep_h"] = float(np.mean(hours))
    return out


# ---------------------------------------------------------------------------
# Comparaison
# ---------------------------------------------------------------------------
def _fmt_pace(sec: float | None) -> str:
    if not sec:
        return "—"
    s = int(round(sec))
    return f"{s // 60}:{s % 60:02d}"


# (clé, section, libellé, format, seuil « notable », unité de l'écart)
_FACTORS = [
    ("km_week", "block", "Volume", "{:.0f} km/sem.", 0.15, "rel"),
    ("longest_km", "block", "Sortie la plus longue", "{:.1f} km", 3.0, "abs"),
    ("runs", "block", "Sorties", "{:.0f}", 3, "abs"),
    ("easy_share", "block", "Part en facile", "{:.0%}", 0.10, "abs"),
    ("cross_hours", "block", "Sport croisé", "{:.1f} h", 3.0, "abs"),
    ("ctl", "block", "Forme de fond (CTL) la veille", "{:.0f}", 5.0, "abs"),
    ("tsb", "block", "Fraîcheur (TSB) la veille", "{:+.0f}", 5.0, "abs"),
    ("sleep_h", "health", "Sommeil moyen (7 nuits)", "{:.1f} h", 0.5, "abs"),
    ("hrv", "health", "HRV moyenne (7 nuits)", "{:.0f} ms", 0.05, "rel"),
    ("rhr", "health", "FC de repos (7 nuits)", "{:.0f} bpm", 2.0, "abs"),
]


def factor_rows(a: dict, b: dict) -> list[dict]:
    """
    Tableau du bloc d'avant : label, a, b, delta (texte), notable. `a` et `b`
    sont des dicts {"block": …, "health": …}. Une valeur absente d'un côté ne
    fait pas de ligne notable (pas de conclusion sur un trou de données).
    """
    rows = []
    for key, section, label, fmt, threshold, mode in _FACTORS:
        va, vb = (a.get(section) or {}).get(key), (b.get(section) or {}).get(key)
        notable, delta = False, ""
        if va is not None and vb is not None:
            diff = vb - va
            if mode == "rel":
                notable = va > 0 and abs(diff) / va >= threshold
                delta = f"{diff / va:+.0%}" if va > 0 else ""
            else:
                notable = abs(diff) >= threshold
                signed = fmt.replace("{:+", "{:").replace("{:", "{:+")    # écart toujours signé
                delta = f"{diff * 100:+.0f} pts" if key == "easy_share" else signed.format(diff)
        rows.append({"key": key, "label": label,
                     "a": fmt.format(va) if va is not None else "—",
                     "b": fmt.format(vb) if vb is not None else "—",
                     "delta": delta, "notable": notable})
    return rows


def compare_runs(a: dict, b: dict) -> dict:
    """
    Verdict de A (la plus ancienne) vers B. `a` / `b` = {"run": run_summary,
    "block": training_block, "health": health_before}. Retourne headline,
    status (`good` / `warning` / `neutral`), delta_pct (< 0 = B plus rapide),
    factors (phrases sur ce qui a changé), notes (précautions de lecture).
    """
    if a["run"]["date"] > b["run"]["date"]:
        a, b = b, a
    ra, rb = a["run"], b["run"]
    notes = []
    pa, pb = ra["adjusted_s"], rb["adjusted_s"]
    if pa and pb and ra["distance_km"] and rb["distance_km"]:
        ratio = rb["distance_km"] / ra["distance_km"]
        if not 0.85 <= ratio <= 1.15:
            pb = equivalent_pace(pb, rb["distance_km"], ra["distance_km"])
            notes.append(f"Distances différentes : l'allure de B est projetée sur "
                         f"{ra['distance_km']:.1f} km (Riegel), une approximation.")
        delta = (pb / pa - 1) * 100
    else:
        delta = None

    if delta is None:
        headline, status = "Allures incomparables (données manquantes).", "neutral"
    elif abs(delta) < 1:
        headline, status = (f"Même niveau à conditions égales ({_fmt_pace(pa)} contre "
                            f"{_fmt_pace(pb)}/km corrigés)."), "neutral"
    elif delta < 0:
        headline, status = (f"B plus rapide de {-delta:.1f} % à conditions égales "
                            f"({_fmt_pace(pb)} contre {_fmt_pace(pa)}/km corrigés)."), "good"
    else:
        headline, status = (f"B plus lente de {delta:.1f} % à conditions égales "
                            f"({_fmt_pace(pb)} contre {_fmt_pace(pa)}/km corrigés)."), "warning"

    factors = [f"{r['label']} : {r['a']} → {r['b']} ({r['delta']})"
               for r in factor_rows(a, b) if r["notable"]]
    sa, sb = ra["split_pct"], rb["split_pct"]
    if sa is not None and sb is not None and abs(sb - sa) >= 2:
        factors.append(f"Gestion de course : 2e moitié {sa:+.1f} % → {sb:+.1f} % par rapport à la 1re "
                       "(positif = ralentissement).")
    ha, hb = (ra["heat"] or {}).get("mid"), (rb["heat"] or {}).get("mid")
    if ha is not None and hb is not None and abs(hb - ha) >= 1:
        notes.append(f"Chaleur : pénalité estimée {ha:.1f} % → {hb:.1f} %, déjà retirée des allures corrigées.")
    if ra["kind"] != rb["kind"]:
        notes.append("Une course et un entraînement : l'engagement n'est pas le même, "
                     "l'écart d'allure en dit moins qu'entre deux courses.")
    notes.append("Deux sorties ne prouvent rien : ces écarts ont coïncidé avec le changement, "
                 "ils ne l'expliquent pas à coup sûr.")
    return {"headline": headline, "status": status, "delta_pct": delta,
            "factors": factors, "notes": notes, "a": a, "b": b}


def pacing_tendency(split_pcts: list[float | None]) -> dict | None:
    """
    Habitude de gestion sur les dernières courses (split 2e / 1re moitié, à
    pente égale) → conseil pour la stratégie du jour J. None sous 2 courses.
    """
    values = [v for v in split_pcts if v is not None]
    if len(values) < 2:
        return None
    mean = float(np.mean(values))
    if mean > 2:
        status, advice = "warning", ("tu ralentis nettement en 2e moitié : départ trop rapide. "
                                     "La stratégie progressive est faite pour toi.")
    elif mean < -1:
        status, advice = "good", "tu finis plus vite que tu ne pars : ta gestion est déjà progressive."
    else:
        status, advice = "good", "ta gestion est régulière ; un départ un peu retenu peut encore aider."
    return {"mean": mean, "n": len(values), "status": status, "advice": advice}



def month_label(year: int, month: int) -> str:
    return f"{MONTHS_FR[month - 1]} {year}"
