"""
Analyses physiologiques sur les streams et les résumés d'activité —
qualité du signal cardio, dérive cardiaque, facteur d'efficacité.
Logique pure, testable sans Streamlit.

Les streams Garmin (`build_streams`) sont échantillonnés de façon irrégulière
(2 à 7 s selon la durée, `maxchart=2000`) : toutes les durées sont donc
pondérées par l'écart de temps réel entre points, jamais par le nombre de
points.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

RUNNING_TYPE = "running"

# Un écart entre deux points au-delà de ce seuil est une pause (montre en
# auto-pause, arrêt au feu) : il ne compte pas comme du temps d'effort.
MAX_SAMPLE_GAP_S = 30.0

# --- FC optique « calée » sur la cadence (cadence lock) ----------------------
# Le capteur au poignet confond parfois le pouls avec le rythme des bras : la
# FC affichée colle alors à la cadence. FC et cadence se croisent aussi
# naturellement vers 170-180 lors d'un effort dur : d'où l'exigence d'un
# plateau soutenu (calibré sur données réelles : croisements naturels
# ≤ 72 s contigus sur 16 sorties).
LOCK_TOL_BPM = 3.0
LOCK_MIN_DURATION_S = 120.0
LOCK_MIN_SHARE = 0.8
LOCK_MIN_CADENCE = 140.0  # en dessous : marche, le phénomène ne s'applique pas
# Une vraie FC monte avec l'effort ; un lock la fait SAUTER au niveau de la
# cadence. Sans marche d'au moins ce seuil à l'entrée ou à la sortie de la
# plage, c'est un finish accéléré où FC et cadence montent ensemble (faux
# positif observé sur données réelles : 174 → 188 bpm en 4 min à 4:37/km).
LOCK_MIN_JUMP_BPM = 12.0

# --- Dérive cardiaque (Pa:HR decoupling, Friel / TrainingPeaks) --------------
DECOUPLING_WARMUP_S = 600.0       # l'échauffement fausse la 1re moitié
DECOUPLING_MIN_MOVING_S = 2400.0  # < 40 min : la dérive n'a pas le temps d'apparaître
DECOUPLING_MAX_SPEED_CV = 0.15    # au-delà : fractionné, la comparaison n'a plus de sens
DECOUPLING_MIN_SPEED_MS = 1.5     # en dessous : marche / arrêt
DECOUPLING_MAX_EXCLUDED_SHARE = 0.10
HILLY_M_PER_KM = 20.0             # dénivelé qui biaise la comparaison des moitiés
# Accélérer en 2de moitié gonfle mécaniquement vitesse/FC (la relation FC-vitesse
# a une ordonnée à l'origine) et masque la dérive : sortie progressive = non
# comparable. Même logique pour un dénivelé concentré sur une moitié.
DECOUPLING_MAX_HALF_SPEED_DIFF = 0.05
# Ralentir à FC tenue EST la dérive (séance guidée à la FC) : on ne rejette un
# ralentissement que si la FC baisse aussi, signe d'un effort volontairement
# relâché (fin de sortie en récupération).
DECOUPLING_EASED_HR_RATIO = 0.97
DECOUPLING_MAX_HALF_CLIMB_DIFF_M_PER_KM = 10.0
DECOUPLING_MAX_MISSING_HR_SHARE = 0.30
# Lissage de l'altitude avant de sommer le dénivelé : le bruit du capteur
# (±1 m à chaque point) multiplie sinon le D+ par ~10 (constaté sur données
# réelles : 23 m/km calculés pour 1,7 m/km réels).
ALTITUDE_SMOOTH_S = 60.0
ALTITUDE_HYSTERESIS_M = 2.0

DECOUPLING_LEVELS = {
    "solide": {"label": "Endurance solide", "max": 5.0},
    "a_consolider": {"label": "À consolider", "max": 10.0},
    "marquee": {"label": "Dérive marquée", "max": float("inf")},
}


def _series(streams: dict, key: str, n: int) -> np.ndarray:
    values = (streams.get(key) or [])[:n]
    arr = pd.to_numeric(pd.Series(values, dtype=object), errors="coerce").to_numpy(float)
    if len(arr) < n:
        arr = np.concatenate([arr, np.full(n - len(arr), np.nan)])
    return arr


def _time_weights(t: np.ndarray, max_gap_s: float = MAX_SAMPLE_GAP_S) -> np.ndarray:
    """Durée représentée par chaque point : écart jusqu'au suivant, pauses exclues."""
    dt = np.diff(t, append=np.nan)
    finite = dt[np.isfinite(dt) & (dt > 0)]
    fallback = float(np.median(finite)) if finite.size else 0.0
    dt = np.where(np.isfinite(dt) & (dt >= 0), dt, fallback)
    return np.where(dt > max_gap_s, 0.0, dt)


def _segments(t: np.ndarray, w: np.ndarray, mask: np.ndarray) -> list[tuple[float, float]]:
    """Plages [début, fin] (s) des points contigus où `mask` est vrai."""
    out = []
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return out
    breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate([[idx[0]], idx[breaks + 1]])
    ends = np.concatenate([idx[breaks], [idx[-1]]])
    for s, e in zip(starts, ends):
        out.append((float(t[s]), float(t[e] + w[e])))
    return out


LOCK_JUMP_WINDOW_S = 20.0  # la marche doit se produire en quelques secondes


def _abrupt_boundary(t, h, s: int, e: int, min_jump_bpm) -> bool:
    """
    La FC entre-t-elle (ou sort-elle) de la plage [s, e] (indices) par une
    marche RAPIDE ? On compare les ~20 s de part et d'autre de chaque bord :
    un lock fait sauter la FC d'un point à l'autre, alors qu'une vraie FC met
    30 s ou plus à monter au début d'une répétition — au moment où elle
    rejoint la cadence, elle a déjà presque fini de monter (faux positif
    « fractionné au seuil » de la revue).
    Au tout début de l'activité, faute d'« avant », on compare au reste de la
    sortie : une FC de départ plus haute que tout l'effort qui suit n'est pas
    physiologique.
    """
    win = LOCK_JUMP_WINDOW_S

    def med(lo, hi):
        vals = h[(t >= lo) & (t < hi) & np.isfinite(h)]
        return float(np.median(vals)) if vals.size else None

    t0, t1 = t[s], t[e]
    inside_in, before = med(t0, t0 + win), med(t0 - win, t0)
    inside_out, after = med(t1 - win, t1 + 1e-9), med(t1 + 1e-9, t1 + win + 1e-9)
    if inside_in is not None and before is not None and inside_in - before >= min_jump_bpm:
        return True
    if inside_out is not None and after is not None and inside_out - after >= min_jump_bpm:
        return True
    if before is None:
        seg = h[s:e + 1][np.isfinite(h[s:e + 1])]
        rest = h[(t > t1 + 60) & np.isfinite(h)]
        return (seg.size > 0 and rest.size >= 10
                and float(np.median(seg)) - float(np.median(rest)) >= min_jump_bpm)
    return False


def _index_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Plages [début, fin] (indices inclus) des points contigus vrais."""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate([[idx[0]], idx[breaks + 1]])
    ends = np.concatenate([idx[breaks], [idx[-1]]])
    return list(zip(starts.tolist(), ends.tolist()))


def hr_cadence_lock(
    streams: dict,
    tol_bpm: float = LOCK_TOL_BPM,
    min_duration_s: float = LOCK_MIN_DURATION_S,
    min_share: float = LOCK_MIN_SHARE,
    min_cadence: float = LOCK_MIN_CADENCE,
    min_jump_bpm: float = LOCK_MIN_JUMP_BPM,
) -> dict:
    """
    Détecte les plages où la FC optique recopie la cadence.

    Une fenêtre glissante de `min_duration_s` est suspecte si, sur au moins
    `min_share` de ses points, |FC − cadence| ≤ `tol_bpm` (cadence de course).
    La plage n'est retenue que si la FC y saute d'au moins `min_jump_bpm` en
    quelques secondes (cf. `_abrupt_boundary`) : sinon FC et cadence montent
    simplement ensemble (finish accéléré, répétition au seuil).
    Retourne `detected`, `segments` [(début_s, fin_s)], `locked_s`, `share`
    (part du temps d'effort) et `mask` (un booléen par point du stream, pour
    exclure ces points des autres calculs).
    """
    t_all = _series(streams, "time", len(streams.get("time") or []))
    n = len(t_all)
    empty = {"detected": False, "segments": [], "locked_s": 0.0, "share": 0.0,
             "mask": [False] * n}
    if n < 2 or not streams.get("heartrate") or not streams.get("cadence"):
        return empty

    hr = _series(streams, "heartrate", n)
    cad = _series(streams, "cadence", n)
    keep = np.isfinite(t_all)
    order = np.flatnonzero(keep)
    t = t_all[keep]
    if t.size < 2 or np.any(np.diff(t) < 0):
        return empty
    h, c = hr[keep], cad[keep]
    w = _time_weights(t)

    near = (np.isfinite(h) & np.isfinite(c) & (c >= min_cadence)
            & (np.abs(h - c) <= tol_bpm))
    cs = np.concatenate([[0], np.cumsum(near)])
    end = np.searchsorted(t, t + min_duration_s, side="left")
    complete = end < t.size  # la fenêtre doit tenir dans l'activité
    starts = np.flatnonzero(complete)
    counts = end[starts] - starts
    shares = (cs[end[starts]] - cs[starts]) / np.maximum(counts, 1)
    hits = starts[(counts > 0) & (shares >= min_share)]

    cover = np.zeros(t.size + 1, dtype=int)
    np.add.at(cover, hits, 1)
    np.add.at(cover, end[hits], -1)
    locked = np.cumsum(cover)[:-1] > 0
    for s, e in _index_runs(locked):
        # La tolérance de la fenêtre (1 − min_share) déborde sur de la vraie
        # FC : on recale la plage sur ses premier et dernier points suspects.
        hits_in = s + np.flatnonzero(near[s:e + 1])
        locked[s:e + 1] = False
        if hits_in.size and _abrupt_boundary(t, h, int(hits_in[0]), int(hits_in[-1]), min_jump_bpm):
            locked[hits_in[0]:hits_in[-1] + 1] = True

    effort_s = float(w.sum())
    locked_s = float(w[locked].sum())
    mask = np.zeros(n, dtype=bool)
    mask[order[locked]] = True
    return {
        "detected": bool(locked.any()),
        "segments": _segments(t, w, locked),
        "locked_s": locked_s,
        "share": locked_s / effort_s if effort_s > 0 else 0.0,
        "mask": mask.tolist(),
    }


def _wmean(x: np.ndarray, w: np.ndarray) -> float:
    return float(np.sum(x * w) / np.sum(w))


def aerobic_decoupling(
    streams: dict,
    warmup_s: float = DECOUPLING_WARMUP_S,
    min_moving_s: float = DECOUPLING_MIN_MOVING_S,
    max_speed_cv: float = DECOUPLING_MAX_SPEED_CV,
    min_speed_ms: float = DECOUPLING_MIN_SPEED_MS,
    exclude_mask: list[bool] | None = None,
) -> dict:
    """
    Dérive cardiaque Pa:HR : perte d'efficacité (vitesse ÷ FC) entre la 1re et
    la 2de moitié du temps d'effort, échauffement exclu.

    `decoupling_pct` > 0 = il a fallu plus de battements pour la même vitesse en
    fin de sortie. Le chiffre n'a de sens que sur une sortie longue et régulière :
    `valid` vaut False (avec `reason`) si la sortie est trop courte, trop
    irrégulière (fractionné) ou si la FC est suspecte ; le pourcentage reste
    fourni quand il est calculable, pour le mode expert.
    """
    result = {"valid": False, "reason": None, "decoupling_pct": None,
              "ef_first": None, "ef_second": None, "moving_min": 0.0,
              "speed_cv": None, "climb_m_per_km": None, "hilly": False,
              "half_speed_diff": None}
    n = len(streams.get("time") or [])
    if n < 2 or not streams.get("heartrate") or not streams.get("velocity_smooth"):
        result["reason"] = "Pas de FC ou de vitesse enregistrée."
        return result

    t = _series(streams, "time", n)
    hr = _series(streams, "heartrate", n)
    v = _series(streams, "velocity_smooth", n)
    alt = _series(streams, "altitude", n)
    dist = _series(streams, "distance", n)
    if not np.all(np.isfinite(t)) or np.any(np.diff(t) < 0):
        result["reason"] = "Horodatage du stream inexploitable."
        return result
    w = _time_weights(t)

    moving = np.isfinite(hr) & (hr > 0) & np.isfinite(v) & (v >= min_speed_ms)
    excluded = np.zeros(n, dtype=bool)
    if exclude_mask is not None:
        excluded = np.asarray(list(exclude_mask)[:n] + [False] * max(0, n - len(exclude_mask)))
    moving_s = float(w[moving].sum())
    result["moving_min"] = round(moving_s / 60, 1)
    running_s = float(w[np.isfinite(v) & (v >= min_speed_ms)].sum())
    if running_s >= min_moving_s and (running_s - moving_s) / running_s > DECOUPLING_MAX_MISSING_HR_SHARE:
        result["reason"] = (
            f"FC absente sur {(running_s - moving_s) / running_s:.0%} de la sortie "
            "(capteur décroché ?)."
        )
        return result
    if moving_s < min_moving_s:
        result["reason"] = (
            f"Sortie trop courte ({moving_s / 60:.0f} min d'effort, "
            f"{min_moving_s / 60:.0f} min minimum)."
        )
        return result

    excluded_s = float(w[moving & excluded].sum())
    moving_time = np.cumsum(w * moving) - w * moving  # temps d'effort écoulé avant chaque point
    kept = moving & ~excluded & (moving_time >= warmup_s)
    kw = w * kept
    kept_s = float(kw.sum())
    if kept_s <= 0:
        result["reason"] = "Aucun point exploitable après l'échauffement."
        return result

    mean_v = _wmean(v[kept], kw[kept])
    cv = float(np.sqrt(_wmean((v[kept] - mean_v) ** 2, kw[kept])) / mean_v)
    result["speed_cv"] = round(cv, 3)

    elapsed = np.cumsum(kw)
    first = kept & (elapsed <= kept_s / 2)
    second = kept & ~first
    if not first.any() or not second.any():
        result["reason"] = "Pas assez de points pour comparer deux moitiés."
        return result
    v1 = _wmean(v[first], w[first])
    v2 = _wmean(v[second], w[second])
    hr1 = _wmean(hr[first], w[first])
    hr2 = _wmean(hr[second], w[second])
    ef1 = v1 * 60 / hr1
    ef2 = v2 * 60 / hr2
    half_speed_diff = (v2 - v1) / v1
    result.update(
        ef_first=round(ef1, 3), ef_second=round(ef2, 3),
        decoupling_pct=round((ef1 - ef2) / ef1 * 100, 1),
        half_speed_diff=round(half_speed_diff, 3),
    )

    climbs = {}
    for name, part in (("all", kept), ("first", first), ("second", second)):
        climbs[name] = _climb_per_km(t, alt, dist, np.flatnonzero(part))
    if climbs["all"] is not None:
        result["climb_m_per_km"] = round(climbs["all"], 1)
        result["hilly"] = climbs["all"] > HILLY_M_PER_KM
    climb_gap = (abs(climbs["first"] - climbs["second"])
                 if climbs["first"] is not None and climbs["second"] is not None else 0.0)

    if moving_s > 0 and excluded_s / moving_s > DECOUPLING_MAX_EXCLUDED_SHARE:
        result["reason"] = "FC suspecte (calée sur la cadence) sur une partie de la sortie."
    elif half_speed_diff > DECOUPLING_MAX_HALF_SPEED_DIFF:
        result["reason"] = (
            f"Sortie progressive : tu as accéléré de {half_speed_diff:.0%} en 2de "
            "moitié, ce qui masque la dérive."
        )
    elif (half_speed_diff < -DECOUPLING_MAX_HALF_SPEED_DIFF
          and hr2 / hr1 < DECOUPLING_EASED_HR_RATIO):
        result["reason"] = (
            f"Effort relâché en 2de moitié (−{-half_speed_diff:.0%} de vitesse et FC en "
            "baisse) : la dérive n'est pas comparable."
        )
    elif climb_gap > DECOUPLING_MAX_HALF_CLIMB_DIFF_M_PER_KM:
        result["reason"] = (
            f"Dénivelé inégal entre les deux moitiés ({climbs['first']:.0f} vs "
            f"{climbs['second']:.0f} m/km)."
        )
    elif cv > max_speed_cv:
        result["reason"] = (
            f"Allure trop irrégulière (variation {cv:.0%}, max {max_speed_cv:.0%}) : "
            "fractionné ou terrain très varié."
        )
    else:
        result["valid"] = True
    return result


def _climb_per_km(t: np.ndarray, alt: np.ndarray, dist: np.ndarray,
                  idx: np.ndarray) -> float | None:
    """
    Dénivelé positif (m) par km sur les points `idx`, après lissage de
    l'altitude (médiane glissante `ALTITUDE_SMOOTH_S`) et avec une hystérésis
    de `ALTITUDE_HYSTERESIS_M` : ni le bruit du capteur ni un pic GPS isolé
    ne comptent comme de la montée.
    """
    if idx.size < 2 or np.isfinite(alt[idx]).sum() < 2 or np.isfinite(dist[idx]).sum() < 2:
        return None
    km = (np.nanmax(dist[idx]) - np.nanmin(dist[idx])) / 1000
    if km <= 0:
        return None
    a = pd.Series(alt[idx]).interpolate(limit_direction="both")
    dts = np.diff(t[idx])
    step = float(np.median(dts[dts > 0])) if (dts > 0).any() else 1.0
    window = max(1, int(round(ALTITUDE_SMOOTH_S / step)))
    smooth = a.rolling(window, center=True, min_periods=1).median().to_numpy()
    gain, ref = 0.0, smooth[0]
    for value in smooth[1:]:
        if value > ref + ALTITUDE_HYSTERESIS_M:
            gain += value - ref
            ref = value
        elif value < ref:
            ref = value
    return gain / km


def decoupling_level(pct: float | None) -> str | None:
    """Clé de `DECOUPLING_LEVELS` : < 5 % solide, < 10 % à consolider, sinon marquée."""
    if pct is None or not np.isfinite(pct):
        return None
    for key, level in DECOUPLING_LEVELS.items():
        if pct < level["max"]:
            return key
    return "marquee"


def summary_lock_suspect(avg_hr, avg_cadence, tol_bpm: float = 2.0) -> bool:
    """FC moyenne ≈ cadence moyenne sur toute une course : lock probable."""
    try:
        hr, cad = float(avg_hr), float(avg_cadence)
    except (TypeError, ValueError):
        return False
    if not (np.isfinite(hr) and np.isfinite(cad)):
        return False
    return cad >= LOCK_MIN_CADENCE and abs(hr - cad) <= tol_bpm


def efficiency_trend(
    activities_df: pd.DataFrame,
    window_days: int = 42,
    min_duration_min: float = 20.0,
    max_hr_quantile: float = 0.8,
) -> pd.DataFrame:
    """
    Facteur d'efficacité (m/min par battement) des courses, et sa médiane
    glissante sur `window_days`. Calculé sur les résumés : aucun appel API.

    On garde les sorties comparables : pas de compétition, pas de FC suspecte,
    et on écarte les sorties les plus intenses (quantile `max_hr_quantile` de
    la FC moyenne) dont l'efficacité ne se compare pas à celle de l'endurance.
    Colonnes : startTimeLocal, activityId, ef, ef_smooth, avgHR, avgPace_sec.
    """
    cols = ["startTimeLocal", "activityId", "ef", "ef_smooth", "avgHR", "avgPace_sec"]
    if activities_df is None or activities_df.empty:
        return pd.DataFrame(columns=cols)
    df = activities_df[activities_df["activityType"] == RUNNING_TYPE].copy()
    for col in ("avgSpeed_ms", "avgHR", "duration_min", "avgCadence"):
        df[col] = pd.to_numeric(df.get(col), errors="coerce")
    df = df[(df["avgSpeed_ms"] > 0) & (df["avgHR"] > 0)
            & (df["duration_min"] >= min_duration_min)]
    if "workoutType" in df.columns:
        df = df[df["workoutType"] != "race"]
    suspect = [summary_lock_suspect(h, c) for h, c in zip(df["avgHR"], df["avgCadence"])]
    df = df[~np.asarray(suspect, dtype=bool)] if len(df) else df
    if df.empty:
        return pd.DataFrame(columns=cols)
    df = df[df["avgHR"] <= df["avgHR"].quantile(max_hr_quantile)]

    df["startTimeLocal"] = pd.to_datetime(df["startTimeLocal"])
    df = df.sort_values("startTimeLocal")
    df["ef"] = df["avgSpeed_ms"] * 60 / df["avgHR"]
    df["ef_smooth"] = (
        df.set_index("startTimeLocal")["ef"]
        .rolling(f"{window_days}D", min_periods=1).median().to_numpy()
    )
    return df[cols].reset_index(drop=True)


def efficiency_change(trend: pd.DataFrame, days: int = 90) -> float | None:
    """
    Variation (%) de l'efficacité lissée entre il y a `days` jours et la
    dernière sortie. None si l'historique ne couvre pas la période.
    """
    if trend is None or len(trend) < 2:
        return None
    last = trend.iloc[-1]
    ref_date = last["startTimeLocal"] - pd.Timedelta(days=days)
    # La référence doit être proche de J−days : après un long arrêt, comparer
    # à une sortie de 200 jours serait étiqueté « sur 90 j » à tort.
    before = trend[(trend["startTimeLocal"] <= ref_date)
                   & (trend["startTimeLocal"] >= ref_date - pd.Timedelta(days=max(days // 3, 14)))]
    if before.empty:
        return None
    ref = float(before.iloc[-1]["ef_smooth"])
    if ref <= 0:
        return None
    return round((float(last["ef_smooth"]) - ref) / ref * 100, 1)


def decoupling_history(items: list[tuple[dict, dict]], lock_params: dict | None = None,
                       **params) -> pd.DataFrame:
    """
    Dérive cardiaque de plusieurs sorties : `items` = [(ligne d'activité, streams)].
    Ne garde que les mesures valides ; la FC calée sur la cadence est exclue
    du calcul avant de juger la sortie.
    Colonnes : startTimeLocal, activityId, activityName, moving_min,
    decoupling_pct, level, hilly.
    """
    cols = ["startTimeLocal", "activityId", "activityName", "moving_min",
            "decoupling_pct", "level", "hilly"]
    rows = []
    for activity, streams in items:
        if not streams:
            continue
        lock = hr_cadence_lock(streams, **(lock_params or {}))
        res = aerobic_decoupling(streams, exclude_mask=lock["mask"], **params)
        if not res["valid"]:
            continue
        rows.append({
            "startTimeLocal": pd.to_datetime(activity.get("startTimeLocal")),
            "activityId": activity.get("activityId"),
            "activityName": activity.get("activityName", ""),
            "moving_min": res["moving_min"],
            "decoupling_pct": res["decoupling_pct"],
            "level": decoupling_level(res["decoupling_pct"]),
            "hilly": res["hilly"],
        })
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows, columns=cols).sort_values("startTimeLocal").reset_index(drop=True)


# Budget d'appels API de la tendance multi-sorties : chaque sortie non encore
# en cache coûte un `get_activity_details` (streams gardés 30 jours ensuite).
DECOUPLING_TREND_WEEKS = 12
DECOUPLING_TREND_MAX_RUNS = 12


def decoupling_candidates(
    activities_df: pd.DataFrame,
    today=None,
    weeks: int = DECOUPLING_TREND_WEEKS,
    max_runs: int = DECOUPLING_TREND_MAX_RUNS,
    min_duration_min: float = DECOUPLING_MIN_MOVING_S / 60 + 5,
) -> list[dict]:
    """
    Courses assez longues pour mesurer une dérive, les plus récentes d'abord,
    bornées en nombre et en ancienneté (budget d'appels API). Compétitions
    exclues : on y cherche la performance, pas l'endurance régulière.
    """
    if activities_df is None or activities_df.empty:
        return []
    df = activities_df[activities_df["activityType"] == RUNNING_TYPE].copy()
    df["startTimeLocal"] = pd.to_datetime(df["startTimeLocal"])
    df["duration_min"] = pd.to_numeric(df["duration_min"], errors="coerce")
    ref = pd.Timestamp(today) if today is not None else pd.Timestamp.now()
    df = df[(df["startTimeLocal"] >= ref - pd.Timedelta(weeks=weeks))
            & (df["duration_min"] >= min_duration_min)]
    if "workoutType" in df.columns:
        df = df[df["workoutType"] != "race"]
    df = df.sort_values("startTimeLocal", ascending=False).head(max_runs)
    return df.to_dict("records")
