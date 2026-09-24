"""
Générateur de plan d'entraînement vers un objectif de course daté, course à
pied + renforcement musculaire. Logique pure et déterministe (même historique,
même objectif → même plan), testable sans Streamlit.

Chaque séance porte son « pourquoi » (`why`) et, pour le mode pédagogique,
une explication plus longue (`explain`) avec ses sources (`sources`, clés de
`SOURCES`). Les règles de renforcement viennent de la littérature citée ;
les niveaux de preuve sont dans `SOURCES`.

Garde-fous issus de la revue de conception :
- les allures de prescription viennent de la forme RÉCENTE (8 semaines) ou
  des prédictions, jamais de `reference_threshold_sec` (seuil figé du TSS) ;
- le volume de départ a un plancher (historique vide, reprise) et le volume
  de pointe un plafond (objectif lointain : pas de +10 %/sem pendant 30 sem) ;
- les phases ont une durée minimale et un objectif trop proche est signalé.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

RUNNING_TYPE = "running"
STRENGTH_TYPES = {"strength_training", "strength", "fitness_equipment", "hiit"}

DISTANCES = {
    "5 km": 5.0,
    "10 km": 10.0,
    "Semi-marathon": 21.0975,
    "Marathon": 42.195,
}

PHASE_LABELS = {
    "BASE": "Base",
    "BUILD": "Développement",
    "PEAK": "Spécifique",
    "TAPER": "Affûtage",
    "MAINTENANCE": "Entretien",
}

# ---------------------------------------------------------------------------
# Paramètres par distance
# ---------------------------------------------------------------------------
# taper_weeks : affûtage ; peak_km : volume hebdo visé en fin de préparation
# pour un coureur amateur ; long_cap_km : plafond de la sortie longue ;
# start_floor_km : volume de départ minimal quand l'historique est vide.
DISTANCE_PROFILE = {
    "5 km": {"taper_weeks": 1, "peak_km": 30, "long_cap_km": 12, "start_floor_km": 12},
    "10 km": {"taper_weeks": 1, "peak_km": 38, "long_cap_km": 15, "start_floor_km": 14},
    "Semi-marathon": {"taper_weeks": 2, "peak_km": 48, "long_cap_km": 20, "start_floor_km": 15},
    "Marathon": {"taper_weeks": 3, "peak_km": 65, "long_cap_km": 32, "start_floor_km": 20},
}
TAPER_FACTORS = {1: [0.6], 2: [0.75, 0.5], 3: [0.8, 0.6, 0.4]}

MAX_WEEKLY_GROWTH = 0.10     # règle des 10 %
CUTBACK_EVERY = 4            # 1 semaine allégée toutes les 4
CUTBACK_FACTOR = 0.75        # … à 75 % du volume de la semaine précédente
MAX_PEAK_OVER_START = 1.6    # le pic ne dépasse pas 1,6× le volume de départ
MAX_BUILD_WEEKS = 20         # au-delà : semaines d'entretien avant le plan
MIN_PLAN_WEEKS = 3
EASY_RUN_MIN_KM = 3.0
LONG_RUN_SHARE = 0.4         # la sortie longue suit le volume de la semaine
LONG_RUN_MIN_KM = 5.0
# Plus petite semaine d'entraînement qui tienne 3 sorties (longue, séance,
# footing) : une semaine allégée ne descend pas en dessous.
MIN_WEEK_KM = 12.0

# Allures de prescription, en multiple de l'allure 10 km estimée (sec/km).
PACE_ZONES = {
    "easy": (1.17, 1.28),
    "long": (1.15, 1.25),
    # Seuil : entre l'allure 10 km et l'allure semi (≈ 1,046 × par Riegel) —
    # au-delà de 1,04 la « séance seuil » serait plus lente que l'allure semi.
    "tempo": (1.01, 1.04),
    "interval": (0.95, 0.97),
    "strides": (0.88, 0.92),
}
DEFAULT_10K_PACE = 360.0     # 6:00/km, faute de toute donnée
PREDICTION_MARGIN = 1.03     # prédictions Garmin réputées optimistes (~6 % d'erreur)
PLAUSIBLE_RACE_PACE = (150.0, 720.0)  # 2:30 à 12:00/km : au-delà, saisie erronée
RIEGEL_EXPONENT = 1.06

# Renforcement : séances/semaine par phase (Balsalobre-Fernández 2016 ;
# Bickel 2011) ; arrêt du lourd 9 jours avant la course (Doma 2017).
STRENGTH_PER_WEEK = {"MAINTENANCE": 2, "BASE": 2, "BUILD": 2, "PEAK": 1, "TAPER": 1}
STRENGTH_STOP_DAYS_BEFORE_RACE = 9
STRENGTH_INTRO_WEEKS = 3     # débutant en renfo : poids du corps d'abord

SOURCES = {
    "balsalobre2016": ("Balsalobre-Fernández et al., J Strength Cond Res 2016 — "
                       "le renfo améliore l'économie de course (méta-analyse)", "modéré"),
    "eihara2022": ("Eihara et al., Sports Med Open 2022 — charges lourdes > pliométrie, "
                   "effet après ≥ 10 semaines", "modéré"),
    "llanos2024": ("Llanos-Lagos et al., Sports Med 2024 — le renfo lourd améliore le chrono",
                   "modéré"),
    "bickel2011": ("Bickel et al., Med Sci Sports Exerc 2011 — 1 séance/sem suffit à "
                   "maintenir la force", "modéré"),
    "doma2013": ("Doma & Deakin, Appl Physiol Nutr Metab 2013 — courir avant le renfo, "
                 "≥ 6 h d'écart", "modéré"),
    "doma2017": ("Doma et al., Sports Med 2017 — fatigue du renfo jusqu'à 72 h", "modéré"),
    "wu2024": ("Wu et al., Sports Med 2024 — chez le coureur, l'effet anti-blessure du renfo "
               "n'est net que si le programme est supervisé", "modéré"),
    "friel": ("Friel / TrainingPeaks — dérive cardiaque < 5 % = endurance aérobie solide",
              "avis d'expert"),
    "tenpercent": ("Règle des 10 % de progression hebdomadaire", "avis d'expert"),
    "smyth2021": ("Smyth & Lawlor, Front Sports Act Living 2021 — un affûtage de 3 semaines "
                  "est associé aux meilleurs chronos marathon", "modéré"),
}


# ---------------------------------------------------------------------------
# Situation de départ de l'athlète
# ---------------------------------------------------------------------------

def _pace_str(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def _pace_range(p10: float, zone: str) -> tuple[int, int]:
    lo, hi = PACE_ZONES[zone]
    return int(round(p10 * lo)), int(round(p10 * hi))


def _riegel(time_s: float, from_km: float, to_km: float) -> float:
    return time_s * (to_km / from_km) ** RIEGEL_EXPONENT


def athlete_baseline(
    activities_df: pd.DataFrame,
    today: date,
    predictions: dict | None = None,
) -> dict:
    """
    Ce que l'historique récent dit de l'athlète : volume, sortie longue,
    fréquence, renfo, et allure 10 km estimée (pour les zones d'allure).

    `predictions` : {distance_km: temps_s} (ex. prédictions Garmin).
    L'allure 10 km vient, par ordre de fiabilité (`pace_source`) :
    1. d'une compétition des 26 dernières semaines (Riegel) — un vrai effort ;
    2. des prédictions Garmin, avec une marge de prudence de 3 % (réputées
       optimistes) ;
    3. à défaut, des entraînements récents — des footings, donc une estimation
       trop lente, signalée comme telle.
    Projeter les footings quand une course ou une prédiction existe ferait
    prescrire un seuil plus lent que les footings habituels (revue P2).
    `assumptions` liste ce qui a été supposé faute de données.
    """
    assumptions = []
    since_4w = pd.Timestamp(today - timedelta(weeks=4))
    since_8w = pd.Timestamp(today - timedelta(weeks=8))
    df = activities_df if activities_df is not None else pd.DataFrame()
    if not df.empty:
        df = df.copy()
        df["startTimeLocal"] = pd.to_datetime(df["startTimeLocal"])
        df = df[df["startTimeLocal"] < pd.Timestamp(today + timedelta(days=1))]
    runs = df[df["activityType"] == RUNNING_TYPE] if not df.empty else df
    recent4 = runs[runs["startTimeLocal"] >= since_4w] if not runs.empty else runs
    recent8 = runs[runs["startTimeLocal"] >= since_8w] if not runs.empty else runs

    weekly_km = float(recent4["distance_km"].sum()) / 4 if not recent4.empty else 0.0
    long_km = float(recent8["distance_km"].max()) if not recent8.empty else 0.0
    runs_per_week = len(recent4) / 4 if not recent4.empty else 0.0
    strength_8w = 0
    if not df.empty:
        strength_8w = int(((df["startTimeLocal"] >= since_8w)
                           & df["activityType"].isin(STRENGTH_TYPES)).sum())

    p10, source = None, None
    if not runs.empty and "workoutType" in runs.columns:
        races = runs[(runs["workoutType"] == "race")
                     & (runs["startTimeLocal"] >= pd.Timestamp(today - timedelta(weeks=26)))
                     & (runs["distance_km"] >= 3) & (runs["avgPace_sec"] > 0)]
        if not races.empty:
            last = races.sort_values("startTimeLocal").iloc[-1]
            p10 = _riegel(float(last["avgPace_sec"] * last["distance_km"]),
                          float(last["distance_km"]), 10.0) / 10.0
            source = "race"
    if p10 is None and predictions:
        km, time_s = min(predictions.items(), key=lambda kv: abs(kv[0] - 10.0))
        if time_s and km:
            p10 = _riegel(float(time_s), float(km), 10.0) / 10.0 * PREDICTION_MARGIN
            source = "prediction"
    if p10 is None and not recent8.empty:
        valid = recent8[(recent8["avgPace_sec"] > 0) & (recent8["distance_km"] >= 3)]
        if not valid.empty:
            t = valid["avgPace_sec"] * valid["distance_km"]
            p10 = float((t * (10.0 / valid["distance_km"]) ** RIEGEL_EXPONENT).min()) / 10.0
            source = "training"
            assumptions.append("Allures estimées sur tes entraînements (sans course ni "
                               "prédiction) : probablement trop prudentes. Renseigne un temps "
                               "visé ou fais un test pour les affiner.")
    if p10 is None:
        p10, source = DEFAULT_10K_PACE, "default"
        assumptions.append("Aucune course récente : allures par défaut (10 km en 60 min), "
                           "à ajuster après quelques sorties.")
    if weekly_km == 0:
        assumptions.append("Aucun volume de course sur 4 semaines : départ prudent au "
                           "volume plancher de la distance.")
    if strength_8w == 0:
        assumptions.append("Pas de renforcement récent : 3 semaines d'initiation au poids "
                           "du corps avant les charges.")
    return {
        "weekly_km": round(weekly_km, 1),
        "long_run_km": round(long_km, 1),
        "runs_per_week": round(runs_per_week, 1),
        "strength_sessions_8w": strength_8w,
        "pace_10k_sec": round(p10, 1),
        "pace_source": source,
        "assumptions": assumptions,
    }


# ---------------------------------------------------------------------------
# Structure du plan
# ---------------------------------------------------------------------------

def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def plan_phases(n_weeks: int, distance: str) -> list[str]:
    """
    Phase de chaque semaine (la dernière contient la course). Au-delà de
    `MAX_BUILD_WEEKS` semaines hors affûtage, les premières sont de
    l'entretien : on ne construit pas du volume pendant 30 semaines.
    """
    if n_weeks <= 0:
        return []
    taper = min(DISTANCE_PROFILE[distance]["taper_weeks"], max(n_weeks - 1, 1))
    if n_weeks == 1:
        return ["TAPER"]
    rest = n_weeks - taper
    maintenance = max(0, rest - MAX_BUILD_WEEKS)
    rest -= maintenance
    if rest < 4:
        body = ["BUILD"] * rest
    else:
        peak = max(1, round(rest * 0.2))
        build = max(1, round(rest * 0.35))
        base = rest - peak - build
        body = ["BASE"] * base + ["BUILD"] * build + ["PEAK"] * peak
    return ["MAINTENANCE"] * maintenance + body + ["TAPER"] * taper


def weekly_volumes(phases: list[str], start_km: float, peak_km: float) -> list[float]:
    """
    Volume hebdomadaire (km) : +10 % max par semaine vers le pic, une semaine
    allégée toutes les `CUTBACK_EVERY`, pic tenu en phase spécifique, puis
    affûtage dégressif.
    """
    volumes: list[float] = []
    current = start_km
    growth_weeks = 0
    taper_idx = 0
    n_taper = phases.count("TAPER")
    last_full = start_km
    for phase in phases:
        if phase == "MAINTENANCE":
            volumes.append(round(start_km, 1))
            continue
        if phase == "TAPER":
            factor = TAPER_FACTORS[min(n_taper, 3)][min(taper_idx, 2)]
            volumes.append(round(last_full * factor, 1))
            taper_idx += 1
            continue
        growth_weeks += 1
        if growth_weeks % CUTBACK_EVERY == 0:
            volumes.append(round(max(current * CUTBACK_FACTOR, min(MIN_WEEK_KM, current)), 1))
            continue
        if volumes and phases[len(volumes) - 1] not in ("MAINTENANCE",):
            current = min(current * (1 + MAX_WEEKLY_GROWTH), peak_km)
        volumes.append(round(current, 1))
        last_full = current
    return volumes


# Jours des séances par nombre de sorties, en décalage (jours) AVANT la sortie
# longue : les séances de qualité sont espacées d'au moins 48 h entre elles
# et de la sortie longue.
_RUN_LAYOUT = {
    3: {5: "quality", 3: "easy"},
    4: {5: "quality", 4: "easy", 2: "quality2"},
    5: {6: "easy", 5: "quality", 3: "quality2", 2: "easy"},
    6: {6: "easy", 5: "quality", 4: "easy", 3: "quality2", 1: "easy"},
}


def _quality_sessions(phase: str, runs_per_week: int) -> tuple[str | None, str | None]:
    """(séance clé 1, séance clé 2) de la semaine selon la phase."""
    second = runs_per_week >= 4
    return {
        "MAINTENANCE": ("strides", None),
        "BASE": ("strides", "tempo" if second else None),
        "BUILD": ("tempo", "interval" if second else None),
        "PEAK": ("race_pace", "interval" if second else None),
        "TAPER": ("race_pace", None),
    }[phase]


# ---------------------------------------------------------------------------
# Construction des séances
# ---------------------------------------------------------------------------

def _run_session(kind: str, day: date, km: float, p10: float, race_pace: float,
                 phase: str, week_index: int, context: dict, short: bool = False) -> dict:
    """Une séance de course avec ses allures, ses étapes et son pourquoi."""
    easy_lo, easy_hi = _pace_range(p10, "easy")
    easy_mid = (easy_lo + easy_hi) / 2
    session = {"date": day.isoformat(), "kind": kind, "phase": phase,
               "week": week_index, "sources": []}

    if kind in ("easy", "long", "shakeout"):
        zone = "long" if kind == "long" else "easy"
        lo, hi = _pace_range(p10, zone)
        km = round(max(km, 3.0 if kind == "shakeout" else EASY_RUN_MIN_KM), 1)
        dur = km * (lo + hi) / 2 / 60
        session.update(
            title={"easy": "Footing", "long": "Sortie longue", "shakeout": "Déblocage"}[kind],
            distance_km=km, duration_min=round(dur),
            target=f"{_pace_str(lo)}–{_pace_str(hi)}/km",
            steps=[{"type": "interval", "duration_s": int(dur * 60),
                    "pace_fast": lo, "pace_slow": hi}],
        )
        if kind == "long":
            drift = context.get("long_run_decoupling_pct")
            session["why"] = ("Construire l'endurance : c'est la séance qui repousse "
                              "le moment où tu faiblis.")
            session["explain"] = (
                "À allure facile et longtemps, le cœur, les muscles et les tendons "
                "s'adaptent à l'effort prolongé. On mesure le progrès par la dérive "
                "cardiaque : moins la FC monte en fin de sortie à allure égale, plus "
                "l'endurance est solide (< 5 %)."
                + (f" Ta dernière sortie longue dérivait de {drift:.0f} % : c'est ici "
                   "que se trouve ta marge de progression." if drift and drift >= 5 else "")
            )
            session["sources"] = ["friel"]
        elif kind == "shakeout":
            session["why"] = "Garder les jambes éveillées sans fatigue avant la course."
            session["explain"] = ("Un footing court et très facile la veille ou l'avant-"
                                  "veille entretient les sensations sans rien coûter.")
        else:
            session["why"] = "Accumuler du volume à faible coût : la base de tout le reste."
            session["explain"] = ("La majorité des kilomètres se court lentement : c'est "
                                  "ce qui développe le moteur aérobie sans fatiguer au "
                                  "point de gâcher les séances clés.")
        return session

    # Petits volumes : échauffement/retour au calme raccourcis, sinon les seules
    # séances clés dépassent le volume de la semaine (revue P2).
    wu_s, cd_s = (10 * 60, 5 * 60) if short else (15 * 60, 10 * 60)
    if kind == "strides":
        reps, work_s, rec_s, zone = 6, 20, 60, "strides"
        title, why = "Footing + lignes droites", "Garder de la vitesse et une foulée économique."
        explain = ("Quelques accélérations de 20 s, bien récupérées, entretiennent la "
                   "coordination et la vitesse sans fatigue.")
    elif kind == "tempo":
        blocks = 2 if phase == "BASE" else 3
        reps, work_s, rec_s, zone = blocks, 8 * 60 if phase == "BASE" else 10 * 60, 2 * 60, "tempo"
        title, why = "Seuil", "Repousser l'allure que tu peux tenir longtemps."
        explain = ("Courir « confortablement dur », juste sous ton seuil, apprend au corps "
                   "à recycler l'acide lactique : l'allure de semi et de 10 km devient plus "
                   "facile à tenir.")
    elif kind == "interval":
        reps, work_s, rec_s, zone = (5 if phase == "BUILD" else 6), 3 * 60, 2 * 60, "interval"
        title, why = "Fractionné allure 5 km", "Élever ton plafond aérobie (VO2max)."
        explain = ("Des répétitions de 3 min à ton allure 5 km (environ 90-95 % de ta VMA) "
                   "sollicitent fortement la consommation d'oxygène : c'est le plafond qui "
                   "tire toutes les autres allures vers le haut.")
    elif kind == "race_pace":
        taper = phase == "TAPER"
        reps = 2 if taper else 3
        work_s = (6 if taper else 12) * 60
        rec_s, zone = 3 * 60, None
        title, why = "Allure course", "Graver l'allure de l'objectif dans les jambes."
        explain = ("Des blocs à l'allure exacte de la course apprennent à la reconnaître "
                   "et à la tenir sans regarder la montre."
                   + (" En affûtage, le volume baisse mais l'intensité reste : c'est ce "
                      "qui garde la fraîcheur ET les sensations." if taper else ""))
    else:
        raise ValueError(kind)

    if zone:
        fast, slow = _pace_range(p10, zone)
    else:
        fast, slow = int(race_pace) - 3, int(race_pace) + 3
    steps = [{"type": "warmup", "duration_s": wu_s, "pace_fast": easy_lo, "pace_slow": easy_hi},
             {"type": "repeat", "count": reps, "steps": [
                 {"type": "interval", "duration_s": work_s, "pace_fast": fast, "pace_slow": slow},
                 {"type": "recovery", "duration_s": rec_s, "pace_fast": None, "pace_slow": None},
             ]},
             {"type": "cooldown", "duration_s": cd_s, "pace_fast": easy_lo, "pace_slow": easy_hi}]
    total_s = wu_s + cd_s + reps * (work_s + rec_s)
    km = (wu_s + cd_s + reps * rec_s) / easy_mid + reps * work_s / ((fast + slow) / 2)
    session.update(
        title=title, duration_min=round(total_s / 60), distance_km=round(km, 1),
        target=f"{reps} × {work_s // 60 if work_s >= 60 else work_s}"
               f"{'′' if work_s >= 60 else '″'} à {_pace_str(fast)}–{_pace_str(slow)}/km",
        steps=steps, why=why, explain=explain,
    )
    return session


def _strength_session(day: date, phase: str, week_index: int, intro: bool) -> dict:
    if intro:
        title = "Renfo — initiation"
        target = "3 tours : squats, fentes, pont fessier, mollets, gainage — poids du corps"
        explain = ("On apprend d'abord les mouvements sans charge : la technique avant le "
                   "poids. Les courbatures des premières séances sont normales et "
                   "disparaissent vite.")
    elif phase == "PEAK":
        title = "Renfo — entretien"
        target = "2-3 séries × 3-5 reps lourdes : squat, soulevé de terre roumain, mollets"
        explain = ("Une seule séance, courte mais lourde, suffit à garder la force acquise "
                   "sans ajouter de fatigue avant les séances clés.")
    else:
        title = "Renfo — force"
        target = "3-4 séries × 4-6 reps lourdes (≥ 85 % max) : squat, fentes, mollets, gainage"
        explain = ("Le renfo lourd rend chaque foulée moins coûteuse (économie de course) et "
                   "renforce tendons et articulations. Priorité aux mollets et aux fessiers, "
                   "qui encaissent chaque appui.")
    return {
        "date": day.isoformat(), "kind": "strength", "phase": phase, "week": week_index,
        "title": title, "duration_min": 30 if intro else 40, "distance_km": 0.0,
        "target": target, "steps": [],
        "why": "Courir plus économique et plus solide : le renfo est le complément qui manque "
               "à la plupart des coureurs.",
        "explain": explain + (" Le même jour qu'une sortie : cours d'abord, renfo au moins "
                              "6 h après."),
        "sources": ["balsalobre2016", "eihara2022", "doma2013"] + (["bickel2011"] if phase == "PEAK" else []),
    }


EASY_KINDS = ("easy", "long", "shakeout")
KEY_KINDS = ("tempo", "interval", "race_pace", "long", "race")
NO_LONG_RUN_DAYS_BEFORE_RACE = 6
SHORT_SESSION_VOLUME_KM = 25.0
VOLUME_TOLERANCE = 0.10


def _runs_for_volume(requested: int, volume: float) -> int:
    """Pas six sorties de 2 km : le nombre de sorties suit le volume."""
    return min(requested, max(3, int(volume // 5)))


def _week_runs(phase, volume, monday, long_weekday, runs, race_date, today,
               p10, race_pace, idx, context, long_cap):
    """
    Séances de course d'une semaine CALENDAIRE (lundi → dimanche). Le motif
    est défini relativement au jour de sortie longue puis ramené dans la
    semaine (modulo 7) : l'espacement ≥ 48 h entre séances clés est un motif
    hebdomadaire, il reste vrai d'une semaine à l'autre.
    Réduit ensuite la semaine jusqu'à ce que le prescrit tienne dans le
    volume annoncé (+10 %) : moins de sorties faciles, puis pas de 2e séance
    clé, puis une sortie longue plus courte.
    """
    short = volume < SHORT_SESSION_VOLUME_KM
    q1, q2 = _quality_sessions(phase, runs)
    layout = _RUN_LAYOUT[runs]
    long_day = monday + timedelta(days=long_weekday)
    days = {}
    for offset, role in layout.items():
        day = monday + timedelta(days=(long_weekday - offset) % 7)
        days[day] = {"quality": q1, "quality2": q2}.get(role, "easy") or "easy"
    days[long_day] = "long"

    # Course : pas de sortie longue dans les 6 jours qui la précèdent, un
    # déblocage la veille (à la place de ce qui y était prévu), rien après.
    if monday <= race_date + timedelta(days=NO_LONG_RUN_DAYS_BEFORE_RACE) and race_date >= monday:
        for day in list(days):
            if day >= race_date:
                days.pop(day)
            elif days[day] == "long" and (race_date - day).days <= NO_LONG_RUN_DAYS_BEFORE_RACE:
                days[day] = "easy"
        eve = race_date - timedelta(days=1)
        if monday <= eve < monday + timedelta(days=7):
            days[eve] = "shakeout"
        if monday <= race_date < monday + timedelta(days=7):
            days[race_date] = "race"

    long_km = min(max(volume * LONG_RUN_SHARE, LONG_RUN_MIN_KM), long_cap)

    def compose(days_map, long_target):
        built = {}
        for day, kind in days_map.items():
            if kind in EASY_KINDS or kind == "race":
                continue
            built[day] = _run_session(kind, day, 0, p10, race_pace, phase, idx, context, short)
        fixed = sum(b["distance_km"] for b in built.values())
        n_long = sum(1 for k in days_map.values() if k == "long")
        n_shake = sum(1 for k in days_map.values() if k == "shakeout")
        easy_days = [d for d, k in days_map.items() if k == "easy"]
        remaining = volume - fixed - n_long * long_target - n_shake * 3.0
        easy_km = max(remaining / len(easy_days), EASY_RUN_MIN_KM) if easy_days else 0.0
        for day, kind in days_map.items():
            if day in built or kind == "race":
                continue
            km = {"long": long_target, "shakeout": 3.0}.get(kind, easy_km)
            built[day] = _run_session(kind, day, km, p10, race_pace, phase, idx, context, short)
        return built

    has_race = "race" in days.values()
    built = compose(days, long_km)
    for _ in range(12):
        total = sum(b["distance_km"] for d, b in built.items() if b["kind"] != "race")
        if has_race or total <= volume * (1 + VOLUME_TOLERANCE) or volume <= 0:
            break
        easy = sorted(d for d, k in days.items() if k == "easy")
        seconds = [d for d, k in days.items() if k == q2 and q2]
        firsts = [d for d, k in days.items() if k == q1 and q1 not in (None, "strides")]
        if len(easy) > 1:
            days.pop(easy[0])
        elif seconds:
            days[seconds[0]] = "easy"
        elif long_km > LONG_RUN_MIN_KM:
            long_km = max(LONG_RUN_MIN_KM, long_km - (total - volume))
        elif firsts:
            days[firsts[0]] = "strides"   # séance clé allégée : lignes droites
        else:
            break
        built = compose(days, long_km)
    # Ce qui ne rentre toujours pas : le volume annoncé devient le vrai volume
    # (jamais l'inverse — afficher 12 km pour en prescrire 17 serait mentir).
    prescribed = sum(b["distance_km"] for b in built.values() if b["kind"] != "race")
    return built, long_day, prescribed


def build_race_plan(
    race_date: date,
    distance: str,
    baseline: dict,
    today: date,
    runs_per_week: int = 4,
    long_run_weekday: int = 6,
    target_time_s: float | None = None,
    include_strength: bool = True,
    context: dict | None = None,
) -> dict:
    """
    Plan complet vers `race_date`. Retourne `weeks` (semaines calendaires
    lundi → dimanche avec phase, volume et séances), `summary` et `warnings`.
    Les séances passées (avant `today`) ne sont pas générées.

    `context` peut porter des constats personnels (ex.
    `long_run_decoupling_pct`) qui enrichissent les explications.
    """
    if distance not in DISTANCE_PROFILE:
        raise ValueError(f"Distance inconnue : {distance}")
    runs_per_week = min(max(int(runs_per_week), 3), 6)
    long_run_weekday = int(long_run_weekday) % 7
    context = context or {}
    warnings: list[str] = []
    dist_km = DISTANCES[distance]
    profile = DISTANCE_PROFILE[distance]

    if race_date <= today:
        return {"weeks": [], "summary": {}, "warnings": ["La date de course est passée."]}

    first_monday = _monday(today)
    n_weeks = (_monday(race_date) - first_monday).days // 7 + 1
    phases = plan_phases(n_weeks, distance)
    if n_weeks < MIN_PLAN_WEEKS:
        body = "affûtage" if set(phases) == {"TAPER"} else "quelques séances clés et l'affûtage"
        warnings.append(
            f"Course dans {(race_date - today).days} jours : trop tôt pour construire, "
            f"le plan se limite à {body}."
        )
    if phases.count("MAINTENANCE"):
        warnings.append(
            f"Objectif lointain : {phases.count('MAINTENANCE')} semaine(s) d'entretien "
            "d'abord, la préparation démarre ensuite."
        )

    start_km = max(baseline.get("weekly_km") or 0.0, profile["start_floor_km"])
    recent_km = baseline.get("weekly_km") or 0.0
    if 0 < recent_km and start_km > recent_km * (1 + MAX_WEEKLY_GROWTH):
        warnings.append(
            f"Départ à {start_km:.0f} km/sem pour {recent_km:.0f} km courus en moyenne ces 4 "
            "semaines : c'est le minimum du plan pour cette distance. Si c'est trop, choisis "
            "une course plus lointaine ou plus courte."
        )
    peak_km = min(max(profile["peak_km"], start_km), start_km * MAX_PEAK_OVER_START)
    volumes = weekly_volumes(phases, start_km, peak_km)

    p10 = baseline.get("pace_10k_sec") or DEFAULT_10K_PACE
    predicted = _riegel(p10 * 10, 10.0, dist_km)
    race_time = predicted
    if target_time_s:
        pace = float(target_time_s) / dist_km
        lo, hi = PLAUSIBLE_RACE_PACE
        if lo <= pace <= hi:
            race_time = float(target_time_s)
            if race_time < predicted * 0.95:
                warnings.append(
                    f"Objectif ambitieux : {int(race_time // 60)} min visées pour "
                    f"{int(predicted // 60)} min estimées d'après ta forme récente."
                )
        else:
            target_time_s = None
            warnings.append("Temps visé invraisemblable pour cette distance (allure hors "
                            "2:30–12:00/km) : ignoré, plan construit sur ta forme estimée.")
    race_pace = race_time / dist_km

    # 1) Courses de toutes les semaines.
    weeks_built = []
    for idx, (phase, volume) in enumerate(zip(phases, volumes)):
        monday = first_monday + timedelta(weeks=idx)
        runs = _runs_for_volume(runs_per_week, volume)
        built, long_day, prescribed = _week_runs(
            phase, volume, monday, long_run_weekday, runs, race_date, today, p10,
            race_pace, idx + 1, context, profile["long_cap_km"])
        is_race_week = monday <= race_date < monday + timedelta(days=7)
        if not is_race_week and prescribed > volume * (1 + VOLUME_TOLERANCE):
            warnings.append(
                f"Semaine {idx + 1} : {prescribed:.0f} km au lieu de {volume:.0f} km — c'est le "
                "minimum pour 3 sorties structurées à tes allures."
            )
            volume = round(prescribed, 1)
        if monday <= race_date < monday + timedelta(days=7):
            built[race_date] = {
                "date": race_date.isoformat(), "kind": "race", "phase": phase, "week": idx + 1,
                "title": f"🏁 {distance}", "distance_km": round(dist_km, 1),
                "duration_min": round(race_time / 60), "target": f"{_pace_str(race_pace)}/km",
                "steps": [], "why": "Le jour J : tout le plan converge ici.",
                "explain": ("Pars prudemment, à l'allure travaillée : le premier kilomètre "
                            "trop rapide se paie à la fin."),
                "sources": [],
            }
        weeks_built.append((idx, phase, volume, monday, built))

    # 2) Renfo, avec les séances clés de TOUT le plan (la veille d'une séance
    # du lundi est le dimanche de la semaine précédente). Doma 2013/2017 :
    # jamais la veille d'une séance clé ni le jour de la sortie longue, arrêt
    # J-9 ; de préférence un jour facile ou de repos, à défaut après la séance.
    key_days = {d for *_, built in weeks_built for d, b in built.items() if b["kind"] in KEY_KINDS}
    long_days = {d for *_, built in weeks_built for d, b in built.items() if b["kind"] == "long"}
    strength_intro_left = STRENGTH_INTRO_WEEKS if not baseline.get("strength_sessions_8w") else 0
    strength_cutoff = race_date - timedelta(days=STRENGTH_STOP_DAYS_BEFORE_RACE)
    weeks = []
    for idx, phase, volume, monday, built in weeks_built:
        sessions = list(built.values())
        if include_strength:
            preferred, fallback = [], []
            for offset in range(7):
                day = monday + timedelta(days=offset)
                if (day + timedelta(days=1)) in key_days or day in long_days:
                    continue
                if day > strength_cutoff or day < today:
                    continue
                (fallback if day in key_days else preferred).append(day)
            picked = []
            for day in preferred + fallback:
                if len(picked) >= STRENGTH_PER_WEEK[phase]:
                    break
                if all(abs((day - p).days) >= 2 for p in picked):
                    picked.append(day)
            for day in sorted(picked):
                sessions.append(_strength_session(day, phase, idx + 1, strength_intro_left > 0))
            if picked and strength_intro_left > 0:
                strength_intro_left -= 1

        week_sessions = sorted(
            (s for s in sessions if date.fromisoformat(s["date"]) >= today),
            key=lambda s: (s["date"], s["kind"] == "strength"),
        )
        prescribed = sum(s["distance_km"] for s in sessions if s["kind"] not in ("strength", "race"))
        weeks.append({
            "week": idx + 1, "start": monday.isoformat(), "phase": phase,
            "phase_label": PHASE_LABELS[phase], "volume_km": volume,
            "prescribed_km": round(prescribed, 1), "sessions": week_sessions,
        })

    summary = {
        "distance": distance, "race_date": race_date.isoformat(), "n_weeks": n_weeks,
        "start_km": round(start_km, 1), "peak_km": round(max(volumes, default=0), 1),
        "predicted_time_s": round(predicted), "race_pace_sec": round(race_pace, 1),
        "target_time_s": target_time_s, "pace_source": baseline.get("pace_source"),
        "paces": {z: [_pace_str(v) for v in _pace_range(p10, z)] for z in PACE_ZONES},
    }
    return {"weeks": weeks, "summary": summary, "warnings": warnings + baseline.get("assumptions", [])}


def plan_sessions(plan: dict) -> list[dict]:
    """Toutes les séances du plan, à plat, dans l'ordre chronologique."""
    return [s for w in plan.get("weeks", []) for s in w["sessions"]]


def parse_race_time(text: str | None, distance: str | None = None) -> float | None:
    """
    « 1:45:00 », « 45:30 » ou « 1h45 » → secondes ; None si vide ou illisible.
    Deux composantes se lisent mm:ss, sauf pour un semi ou un marathon où
    « 1:45 » veut évidemment dire 1 h 45 (et non 1 min 45 s).
    """
    if not text or not str(text).strip():
        return None
    raw = str(text).strip().lower().replace("h", ":").replace("'", ":").replace('"', "")
    parts = [p for p in raw.split(":") if p != ""]
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2 and ("h" in str(text).lower()
                           or DISTANCES.get(distance or "", 0) >= 21):
        nums.append(0)
    if len(nums) == 2:
        m, s = nums
        total = m * 60 + s
    elif len(nums) == 3:
        h, m, s = nums
        total = h * 3600 + m * 60 + s
    else:
        return None
    return float(total) if total > 0 else None


def predictions_by_km(raw: dict | None) -> dict[float, float]:
    """Prédictions Garmin (`time5K`…) → {distance_km: secondes}."""
    keys = {"time5K": 5.0, "time10K": 10.0, "timeHalfMarathon": 21.0975,
            "timeMarathon": 42.195}
    out = {}
    for key, km in keys.items():
        value = (raw or {}).get(key)
        if isinstance(value, (int, float)) and value > 0:
            out[km] = float(value)
    return out


def plan_brief(plan: dict) -> str:
    """Résumé texte du plan, à coller dans une conversation avec Claude."""
    s = plan.get("summary", {})
    if not s:
        return ""
    lines = [
        f"Objectif : {s['distance']} le {s['race_date']} "
        f"({s['n_weeks']} semaines, volume {s['start_km']} → {s['peak_km']} km/sem).",
        f"Temps estimé d'après ma forme récente : {int(s['predicted_time_s'] // 60)} min"
        + (f" ; objectif visé : {int(s['target_time_s'] // 60)} min." if s.get("target_time_s") else "."),
        "Allures : " + ", ".join(f"{z} {lo}–{hi}/km" for z, (lo, hi) in s["paces"].items()),
    ]
    for w in plan.get("weeks", []):
        items = "; ".join(f"{x['date'][5:]} {x['title']}"
                          + (f" {x['target']}" if x["kind"] != "strength" else "")
                          for x in w["sessions"])
        lines.append(f"S{w['week']} {w['phase_label']} {w['volume_km']} km : {items}")
    if plan.get("warnings"):
        lines.append("Avertissements : " + " | ".join(plan["warnings"]))
    return "\n".join(lines)
