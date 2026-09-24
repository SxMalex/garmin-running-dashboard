"""
Logique métier de la page Prochaine sortie — fonctions pures testables
sans dépendance à Streamlit.
"""

import numpy as np
import pandas as pd
from datetime import date, datetime, timedelta, timezone
from formatting import seconds_to_pace_str
from forme_logic import downgrade_session, forme_downgrade

_JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
_MOIS_FR = ["jan.", "fév.", "mars", "avr.", "mai", "juin",
            "juil.", "août", "sept.", "oct.", "nov.", "déc."]

# Seuil de pace de référence pour le calcul TSB : percentile utilisé sur les
# sorties longues pour estimer la FC threshold ; 330 s/km (~5:30/km) si absent.
_TSB_THRESHOLD_PERCENTILE = 0.15
_DEFAULT_THRESHOLD_PACE_SEC = 330

# Grille du curseur « Allure seuil » de la page Forme. `reference_threshold_sec`
# s'y aligne pour que le curseur au repos affiche exactement le CTL calculé
# ailleurs dans l'application.
THRESHOLD_SLIDER_MIN = 180
THRESHOLD_SLIDER_MAX = 480
THRESHOLD_SLIDER_STEP = 5

SESSION_TYPES = {
    "recuperation": {
        "label": "Récupération active",
        "icon": "💤",
        "color": "#3987e5",
        "description": "Ta charge récente est élevée. Une sortie légère pour relancer la circulation sans stresser l'organisme.",
        "dist_factor": 0.60,
        "pace_factor": 1.15,
        "elev_factor": 0.4,
    },
    "endurance": {
        "label": "Endurance fondamentale",
        "icon": "🏃",
        "color": "#199e70",
        "description": "Séance clé du coureur. Allure confortable, conversation possible. Développe le moteur aérobie.",
        "dist_factor": 1.00,
        "pace_factor": 1.05,
        "elev_factor": 1.0,
    },
    "tempo": {
        "label": "Tempo / Seuil",
        "icon": "⚡",
        "color": "#d95926",
        "description": "Tu es bien reposé. Séance à allure soutenue pour repousser ton seuil lactique.",
        "dist_factor": 0.80,
        "pace_factor": 0.92,
        "elev_factor": 0.6,
    },
    "sortie_longue": {
        "label": "Sortie longue",
        "icon": "🏔️",
        "color": "#9085e9",
        "description": "Excellente fraîcheur. C'est le moment idéal pour une longue sortie et construire ton endurance.",
        "dist_factor": 1.40,
        "pace_factor": 1.10,
        "elev_factor": 1.3,
    },
}


def suggest_next_date(
    running_df: pd.DataFrame,
    session_key: str,
    days_since: int,
    tsb: float,
) -> date:
    """Suggère la prochaine date selon la fréquence habituelle et la fatigue."""
    today = date.today()

    # Gap typique entre séances (sur les 15 dernières)
    recent_dates = (
        running_df
        .sort_values("startTimeLocal", ascending=False)
        .head(15)["startTimeLocal"]
        .dt.date
        .tolist()
    )
    if len(recent_dates) >= 3:
        gaps = [
            (recent_dates[i] - recent_dates[i + 1]).days
            for i in range(min(10, len(recent_dates) - 1))
            if (recent_dates[i] - recent_dates[i + 1]).days > 0
        ]
        typical_gap = round(sum(gaps) / len(gaps)) if gaps else 2
    else:
        typical_gap = 2
    typical_gap = max(1, min(typical_gap, 5))

    # Ajustement fatigue (TSB)
    if tsb < -20:
        fatigue_adj = +1    # très fatigué → reporter
    elif tsb > 10:
        fatigue_adj = -1    # bien reposé → avancer
    else:
        fatigue_adj = 0

    # Ajustement type de séance
    if session_key in ("tempo", "sortie_longue"):
        session_adj = +1    # séance exigeante → besoin d'être plus frais
    elif session_key == "recuperation":
        session_adj = -1    # séance légère → peut y aller plus tôt
    else:
        session_adj = 0

    target_gap = max(1, typical_gap + fatigue_adj + session_adj)
    days_until = max(0, target_gap - days_since)
    return today + timedelta(days=days_until)


def format_date_fr(d: date) -> str:
    """Formate une date en français lisible (ex. 'Demain', 'Jeudi 1 mai')."""
    today = date.today()
    delta = (d - today).days
    if delta == 0:
        return "Aujourd'hui"
    if delta == 1:
        return "Demain"
    return f"{_JOURS_FR[d.weekday()]} {d.day} {_MOIS_FR[d.month - 1]}"


# ---------------------------------------------------------------------------
# Charge d'entraînement (PMC) — course + sport croisé
# ---------------------------------------------------------------------------

# Type d'activité normalisé (cf. `formatting.normalize_activity_type`) dont le
# TSS se calcule à l'allure. Tout le reste (wing, vélo, natation, muscu…) passe
# par la charge d'entraînement Garmin.
RUNNING_TYPE = "running"

# La charge Garmin (`activityTrainingLoad`, dérivée de l'EPOC) n'est pas dans
# l'unité du TSS d'allure. On la convertit avec un facteur recalibré sur les
# courses de l'athlète — celles qui portent les DEUX métriques — de sorte que
# 1 h de wing pèse sur la même échelle que 1 h de course. Sans cette
# calibration, le CTL changerait d'unité selon la part de sport croisé de la
# semaine et ne serait plus comparable d'une année à l'autre.
CROSS_TRAINING_FALLBACK_K = 0.5
CROSS_TRAINING_K_MIN = 0.2
CROSS_TRAINING_K_MAX = 2.0
_MIN_RUNS_FOR_CALIBRATION = 10

# Plafond de TSS par activité, course comme sport croisé.
_MAX_TSS_PER_ACTIVITY = 400


def _running_rows(activities_df: pd.DataFrame) -> pd.DataFrame:
    """
    Sorties de course exploitables pour le TSS d'allure.

    Un DataFrame sans colonne `activityType` est traité comme 100 % course :
    c'est ce qui garde les appels historiques (et les fixtures de test) au même
    résultat qu'avant l'ajout du sport croisé.
    """
    if activities_df is None or activities_df.empty:
        return pd.DataFrame()
    if "avgPace_sec" not in activities_df.columns:
        return pd.DataFrame()
    rows = activities_df
    if "activityType" in rows.columns:
        rows = rows[rows["activityType"] == RUNNING_TYPE]
    return rows[rows["avgPace_sec"] > 0]


def _cross_training_rows(activities_df: pd.DataFrame) -> pd.DataFrame:
    """
    Activités hors course portant une charge d'entraînement Garmin.

    Sans colonne `activityType` ou `trainingLoad`, il n'y a rien à convertir :
    on retourne un DataFrame vide plutôt que de deviner.
    """
    if activities_df is None or activities_df.empty:
        return pd.DataFrame()
    if not {"activityType", "trainingLoad"} <= set(activities_df.columns):
        return pd.DataFrame()
    rows = activities_df[activities_df["activityType"] != RUNNING_TYPE]
    if rows.empty:
        return pd.DataFrame()
    load = pd.to_numeric(rows["trainingLoad"], errors="coerce")
    return rows[load.notna() & (load > 0)]


def _pace_tss(runs: pd.DataFrame, threshold_sec: float) -> pd.Series:
    """TSS d'allure : durée × IF², IF = allure_seuil / allure_moyenne."""
    intensity = (threshold_sec / runs["avgPace_sec"]).clip(upper=1.5)
    return (runs["duration_min"] / 60 * intensity ** 2 * 100).clip(
        upper=_MAX_TSS_PER_ACTIVITY
    )


def cross_training_factor(activities_df: pd.DataFrame, threshold_sec: float) -> float:
    """
    Facteur de conversion charge Garmin → TSS, calibré sur l'athlète.

    Les courses portent à la fois une allure et une charge Garmin : le rapport
    des deux sommes donne le facteur qui met le sport croisé sur l'échelle du
    TSS d'allure. Agrégé (et non moyenné activité par activité) pour conserver
    la charge cumulée, borné pour rester robuste, et fonction de
    `threshold_sec` afin que le curseur d'allure seuil de la page Forme fasse
    bouger les deux parts dans le même sens.

    Repli sur `CROSS_TRAINING_FALLBACK_K` si l'historique de course est trop
    court pour calibrer quoi que ce soit.
    """
    runs = _running_rows(activities_df)
    if runs.empty or "trainingLoad" not in runs.columns:
        return CROSS_TRAINING_FALLBACK_K
    load = pd.to_numeric(runs["trainingLoad"], errors="coerce")
    runs = runs[load.notna() & (load > 0)]
    if len(runs) < _MIN_RUNS_FOR_CALIBRATION:
        return CROSS_TRAINING_FALLBACK_K
    total_load = float(pd.to_numeric(runs["trainingLoad"]).sum())
    if total_load <= 0:
        return CROSS_TRAINING_FALLBACK_K
    k = float(_pace_tss(runs, threshold_sec).sum()) / total_load
    return min(max(k, CROSS_TRAINING_K_MIN), CROSS_TRAINING_K_MAX)


def daily_tss(activities_df: pd.DataFrame, threshold_sec: float) -> pd.DataFrame:
    """
    TSS quotidien, décomposé en course et sport croisé.
    Colonnes : `day`, `tss_run`, `tss_cross`, `tss` (= somme des deux).

    C'est le point d'entrée unique du stress d'entraînement : `compute_pmc_series`
    n'agrège rien d'autre, donc une activité comptée ici l'est partout dans
    l'application (Accueil, Forme, Prochaine sortie, Coach IA, Comparatif).
    """
    columns = ["day", "tss_run", "tss_cross", "tss"]
    runs = _running_rows(activities_df)
    cross = _cross_training_rows(activities_df)

    parts = []
    if not runs.empty:
        parts.append(
            _pace_tss(runs, threshold_sec)
            .groupby(runs["startTimeLocal"].dt.normalize())
            .sum()
            .rename("tss_run")
        )
    if not cross.empty:
        k = cross_training_factor(activities_df, threshold_sec)
        converted = (
            pd.to_numeric(cross["trainingLoad"], errors="coerce") * k
        ).clip(upper=_MAX_TSS_PER_ACTIVITY)
        parts.append(
            converted
            .groupby(cross["startTimeLocal"].dt.normalize())
            .sum()
            .rename("tss_cross")
        )
    if not parts:
        return pd.DataFrame(columns=columns)

    out = pd.concat(parts, axis=1).fillna(0.0)
    for col in ("tss_run", "tss_cross"):
        if col not in out.columns:
            out[col] = 0.0
    out["tss"] = out["tss_run"] + out["tss_cross"]
    return out.sort_index().rename_axis("day").reset_index()[columns]


def compute_pmc_series(activities_df: pd.DataFrame, threshold_sec: float) -> pd.DataFrame:
    """
    Calcule la série quotidienne du modèle Performance Management Chart.
    Retourne un DataFrame avec colonnes : date, tss, tss_run, tss_cross,
    ctl, atl, tsb.

    Le TSS agrège **toutes** les activités (cf. `daily_tss`) : la course par son
    allure, le reste par sa charge Garmin recalibrée. Une session de wing ou de
    vélo pèse donc sur le CTL comme elle pèse sur l'organisme — les exclure
    faisait afficher « bien reposé » au lendemain de la plus grosse séance de la
    semaine.

    Conventions : `ctl`, `atl` et `tsb` sont les valeurs en fin de journée
    (après TSS du jour), et `tsb` vaut exactement `ctl - atl` sur la même ligne.
    C'est la SEULE définition du TSB dans l'application : `compute_tsb` en
    dérive, la courbe de `tab_charge` la trace (le TSB est donc bien l'écart
    vertical entre les courbes CTL et ATL) et la page Comparatif la reprend.
    Faire du `tsb` une fraîcheur d'avant-séance ferait réapparaître deux
    chiffres de TSB différents sur la même page.

    Retourne un DataFrame vide si pas de données exploitables.
    """
    columns = ["date", "tss", "tss_run", "tss_cross", "ctl", "atl", "tsb"]
    daily = daily_tss(activities_df, threshold_sec)
    if daily.empty:
        return pd.DataFrame(columns=columns)

    daily = daily.set_index("day")
    today_ts = pd.Timestamp(datetime.now().date())
    full_range = pd.date_range(daily.index.min(), today_ts, freq="D")
    if len(full_range) == 0:
        return pd.DataFrame(columns=columns)
    frame = daily.reindex(full_range).fillna(0.0)

    k_ctl = np.exp(-1 / 42)
    k_atl = np.exp(-1 / 7)
    ctl_v = atl_v = 0.0
    records = []
    for d, row in frame.iterrows():
        tss = float(row["tss"])
        ctl_v = ctl_v * k_ctl + tss * (1 - k_ctl)
        atl_v = atl_v * k_atl + tss * (1 - k_atl)
        records.append({
            "date": d, "tss": tss,
            "tss_run": float(row["tss_run"]), "tss_cross": float(row["tss_cross"]),
            "ctl": ctl_v, "atl": atl_v, "tsb": ctl_v - atl_v,
        })

    return pd.DataFrame(records)


# Zones ACWR (Gabbett, Br J Sports Med 2016) — indicateur discuté
# (Impellizzeri et al. 2020) : signal d'alerte, pas prédiction de blessure.
ACWR_ZONES = [(0.8, "sous_charge"), (1.3, "optimal"), (1.5, "vigilance"), (float("inf"), "risque")]
MONOTONY_HIGH = 2.0  # Foster 1998


def load_risk(pmc: pd.DataFrame) -> dict:
    """
    Indicateurs avancés de gestion de charge, sur la série `compute_pmc_series`
    (déjà réindexée jour par jour, jours sans séance à 0 — sinon une fenêtre
    de 7 lignes compterait 7 activités et non 7 jours).

    - `acwr` : TSS moyen des 7 derniers jours ÷ TSS moyen des 28 derniers ;
    - `monotony` (Foster) : moyenne ÷ écart-type du TSS quotidien sur 7 jours
      (None si l'écart-type est nul : 7 jours identiques, typiquement 7 × 0) ;
    - `strain` : charge de la semaine × monotonie.
    Retourne {} si moins de 28 jours d'historique.
    """
    if pmc is None or len(pmc) < 28:
        return {}
    tss = pmc["tss"].astype(float)
    acute = float(tss.iloc[-7:].mean())
    chronic = float(tss.iloc[-28:].mean())
    acwr = acute / chronic if chronic > 0 else None
    sd = float(tss.iloc[-7:].std(ddof=0))
    monotony = acute / sd if sd > 0 else None
    zone = None
    if acwr is not None:
        zone = next(name for limit, name in ACWR_ZONES if acwr < limit)
    return {
        "acute": round(acute, 1),
        "chronic": round(chronic, 1),
        "acwr": round(acwr, 2) if acwr is not None else None,
        "acwr_zone": zone,
        "monotony": round(monotony, 2) if monotony is not None else None,
        "monotony_high": monotony is not None and monotony > MONOTONY_HIGH,
        "strain": round(float(tss.iloc[-7:].sum()) * monotony) if monotony is not None else None,
    }


def reference_threshold_sec(activities_df: pd.DataFrame) -> int:
    """
    Allure seuil de référence (sec/km) déduite des sorties longues : percentile
    bas des allures sur les courses ≥ 8 km, valeur par défaut si aucune.

    Ne regarde que la course, même quand on lui passe l'historique complet :
    une nage ou une sortie vélo de 8 km n'a rien à dire sur l'allure seuil.

    Un seul seuil pour tout l'historique — c'est ce qui rend les TSS comparables
    d'une période (et d'une année) à l'autre. La valeur est arrondie sur la grille
    du curseur d'allure seuil de la page Forme (pas de 5 s entre 3:00 et 8:00) et
    bornée à cette plage : curseur au repos et calculs internes tombent ainsi
    exactement sur le même chiffre, sinon deux CTL cohabitent dans l'application.
    """
    runs = _running_rows(activities_df)
    if runs.empty or "distance_km" not in runs.columns:
        return _DEFAULT_THRESHOLD_PACE_SEC
    long_runs = runs[runs["distance_km"] >= 8]
    if long_runs.empty:
        return _DEFAULT_THRESHOLD_PACE_SEC
    raw = int(long_runs["avgPace_sec"].quantile(_TSB_THRESHOLD_PERCENTILE))
    snapped = round((raw - THRESHOLD_SLIDER_MIN) / THRESHOLD_SLIDER_STEP) \
        * THRESHOLD_SLIDER_STEP + THRESHOLD_SLIDER_MIN
    return max(THRESHOLD_SLIDER_MIN, min(THRESHOLD_SLIDER_MAX, snapped))


def compute_tsb(activities_df: pd.DataFrame) -> tuple[float, float, float]:
    """
    Retourne (CTL, ATL, TSB) actuels sur l'ensemble des activités.

    Passer l'historique complet, pas seulement les courses : c'est cette
    fonction que lisent l'Accueil, la page Forme, le Coach IA et
    `recommend_session`, et elles doivent toutes annoncer le même chiffre.
    """
    threshold_sec = reference_threshold_sec(activities_df)
    pmc = compute_pmc_series(activities_df, threshold_sec)
    if pmc.empty:
        return 0.0, 0.0, 0.0
    last = pmc.iloc[-1]
    # On lit le TSB de la série plutôt que de le recalculer : un seul chiffre
    # de fraîcheur entre cette fonction, la courbe PMC et la page Comparatif.
    return (
        round(float(last["ctl"]), 1),
        round(float(last["atl"]), 1),
        round(float(last["tsb"]), 1),
    )


def recommend_session(
    running_df: pd.DataFrame,
    downgrade: int = 0,
    load_df: pd.DataFrame | None = None,
) -> dict:
    """
    Analyse les dernières sorties et retourne un dict de recommandations.

    `downgrade` (0-2) rétrograde la séance choisie d'autant de crans quand la
    récupération est dégradée (HRV hors baseline, mauvais sommeil) — voir
    `forme_logic.forme_downgrade`. La clé `downgraded_from` du résultat vaut
    la séance initiale si une rétrogradation a eu lieu, None sinon.

    `running_df` reste la base des cibles de la séance (distance, allure, D+ et
    fréquence habituelles se lisent sur les courses). `load_df` porte
    l'historique complet dont sort le TSB : les pages passent le DataFrame non
    filtré, pour que la fraîcheur qui choisit la séance soit celle affichée
    ailleurs dans l'application. Par défaut, `running_df` sert aussi de source
    de charge — le contrat de sortie est inchangé dans les deux cas.
    """
    recent = running_df.sort_values("startTimeLocal", ascending=False).head(20)

    avg_dist = recent["distance_km"].mean()
    avg_pace_sec = recent.loc[recent["avgPace_sec"] > 0, "avgPace_sec"].mean()
    avg_elev = recent["elevationGain"].dropna().mean()

    last_run_date = recent["startTimeLocal"].max()
    days_since = (datetime.now() - last_run_date).days

    long_runs = recent[recent["distance_km"] >= avg_dist * 1.2]
    days_since_long = (
        (datetime.now() - long_runs["startTimeLocal"].max()).days
        if not long_runs.empty else 999
    )

    ctl, atl, tsb = compute_tsb(running_df if load_df is None else load_df)

    if tsb < -20:
        session_key = "recuperation"
    elif tsb > 10 and days_since_long >= 6:
        session_key = "sortie_longue"
    elif tsb > 10:
        session_key = "tempo"
    else:
        session_key = "endurance"

    if days_since >= 5:
        session_key = "endurance"

    original_key = session_key
    if downgrade > 0:
        session_key = downgrade_session(session_key, downgrade)

    s = SESSION_TYPES[session_key]
    target_dist_km = round(avg_dist * s["dist_factor"], 1)
    target_dist_km = max(3.0, target_dist_km)
    target_pace_sec = avg_pace_sec * s["pace_factor"]
    target_elev = round(avg_elev * s["elev_factor"]) if avg_elev and not np.isnan(avg_elev) else 0
    duration_min = round(target_dist_km * target_pace_sec / 60)

    suggested_date = suggest_next_date(running_df, session_key, days_since, tsb)

    return {
        "session_key": session_key,
        "downgraded_from": original_key if session_key != original_key else None,
        "session": s,
        "ctl": ctl, "atl": atl, "tsb": tsb,
        "days_since": days_since,
        "target_dist_km": target_dist_km,
        "target_pace_sec": target_pace_sec,
        "target_pace_str": seconds_to_pace_str(target_pace_sec),
        "target_elev": target_elev,
        "duration_min": duration_min,
        "avg_dist": round(avg_dist, 1),
        "avg_pace_sec": avg_pace_sec,
        "avg_pace_str": seconds_to_pace_str(avg_pace_sec),
        "suggested_date": suggested_date,
        "suggested_date_str": format_date_fr(suggested_date),
    }


def parse_ors_route(geojson: dict) -> dict | None:
    """Extrait coordonnées, distance réelle et dénivelé depuis la réponse ORS."""
    try:
        feature = geojson["features"][0]
        coords = feature["geometry"]["coordinates"]
        summary = feature["properties"]["summary"]
        ascent = feature["properties"].get("ascent", 0) or 0

        if not coords:
            return None

        lats = [c[1] for c in coords]
        lons = [c[0] for c in coords]
        eles = [c[2] for c in coords] if len(coords[0]) > 2 else []

        return {
            "lats": lats,
            "lons": lons,
            "elevations": eles,
            "distance_km": round(summary["distance"] / 1000, 2),
            "duration_s": summary.get("duration", 0),
            "ascent_m": round(ascent),
        }
    except (KeyError, IndexError, TypeError):
        return None


def build_gpx(route: dict, session_label: str, target_pace_str: str) -> str:
    """Génère un fichier GPX (course) compatible Garmin Connect."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    name = f"Prochaine sortie — {session_label}"
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="Running Dashboard"',
        '     xmlns="http://www.topografix.com/GPX/1/1"',
        '     xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
        '     xsi:schemaLocation="http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd">',
        f'  <metadata><name>{name}</name><time>{now}</time></metadata>',
        '  <trk>',
        f'    <name>{name}</name>',
        f'    <desc>Allure cible : {target_pace_str} — {route["distance_km"]:.2f} km · D+ {route["ascent_m"]} m</desc>',
        '    <trkseg>',
    ]
    for i, (lat, lon) in enumerate(zip(route["lats"], route["lons"])):
        ele_tag = f"<ele>{route['elevations'][i]:.1f}</ele>" if route["elevations"] else ""
        lines.append(f'      <trkpt lat="{lat:.6f}" lon="{lon:.6f}">{ele_tag}</trkpt>')
    lines += ["    </trkseg>", "  </trk>", "</gpx>"]
    return "\n".join(lines)


# Plan Objectif validé → contrat de recommend_session. La page Objectif est la
# seule à écrire ce plan dans le calendrier : une fois envoyé, c'est lui que la
# montre affiche, donc lui que l'Accueil doit annoncer.
_GOAL_KIND_TO_SESSION = {
    "easy": "endurance", "strides": "endurance", "shakeout": "recuperation",
    "long": "sortie_longue", "tempo": "tempo", "interval": "tempo",
    "race_pace": "tempo", "race": "tempo",
}
_GOAL_KEY_KINDS = {"tempo", "interval", "race_pace", "long", "race"}


def session_overall_pace(session: dict) -> float | None:
    """
    Allure d'ensemble (s/km) d'une séance du plan : moyenne des allures de ses
    étapes pondérée par leur durée (répétitions déroulées, récupérations sans
    cible ignorées). C'est l'allure du parcours, comme pour Run Coach ; la
    cible de répétition reste dans `session["target"]`. Pour la course :
    l'allure de course.
    """
    if session.get("pace_sec"):
        return float(session["pace_sec"])
    total_s = weighted = 0.0

    def walk(steps, times):
        nonlocal total_s, weighted
        for step in steps or []:
            if step.get("type") == "repeat":
                walk(step.get("steps"), times * int(step.get("count") or 1))
            elif step.get("pace_fast") and step.get("pace_slow"):
                dur = float(step.get("duration_s") or 0) * times
                total_s += dur
                weighted += dur * (step["pace_fast"] + step["pace_slow"]) / 2

    walk(session.get("steps"), 1)
    return weighted / total_s if total_s else None


def merge_goal_plan_into_recommendation(rec: dict, sessions: list[dict] | None,
                                        today: date | None = None,
                                        ran_today: bool = False) -> dict:
    """
    Fait piloter la recommandation par la prochaine séance de course du plan
    Objectif validé (même contrat que `recommend_session`, comme
    `merge_coach_into_recommendation`). La séance du jour est ignorée si une
    course a déjà été enregistrée aujourd'hui. Sans plan ou sans course à
    venir, `rec` est renvoyé tel quel avec `goal_session=None`.
    """
    today = today or date.today()
    runs = [s for s in sessions or []
            if s.get("kind") in _GOAL_KIND_TO_SESSION
            and (date.fromisoformat(s["date"]) > today
                 or (date.fromisoformat(s["date"]) == today and not ran_today))]
    if not runs:
        return dict(rec, goal_session=None)
    s = min(runs, key=lambda x: x["date"])
    day = date.fromisoformat(s["date"])
    pace = session_overall_pace(s) or rec.get("target_pace_sec")
    merged = dict(rec)
    merged.update(
        session_key=_GOAL_KIND_TO_SESSION[s["kind"]],
        goal_session=s,
        target_dist_km=s.get("distance_km") or rec.get("target_dist_km"),
        duration_min=s.get("duration_min") or rec.get("duration_min"),
        target_pace_sec=pace,
        target_pace_str=seconds_to_pace_str(pace) if pace else rec.get("target_pace_str"),
        suggested_date=day,
        suggested_date_str=format_date_fr(day),
        downgraded_from=None,
    )
    return merged


def todays_session(activities_df: pd.DataFrame, hrv_status, sleep_score,
                   coach_context: dict | None, goal_sessions: list[dict] | None = None) -> dict:
    """
    Séance du jour telle que l'annonce toute l'app : plan Garmin Run Coach s'il
    est actif (même sans séance de course à venir : la montre le suit), sinon
    le plan Objectif validé du dashboard, sinon la logique interne, modulée par
    la récupération (HRV, sommeil). Retourne {"rec", "downgrade", "alert"}.
    Accueil, Prochaine sortie et serveur MCP passent tous par ici — une page
    qui recomposerait ces appels risquerait d'annoncer une autre séance.
    """
    from coach_logic import hard_session_alert, merge_coach_into_recommendation

    downgrade = forme_downgrade(hrv_status, sleep_score)
    has_data = activities_df is not None and not activities_df.empty
    running = activities_df[activities_df["activityType"] == RUNNING_TYPE] if has_data else pd.DataFrame()
    base = recommend_session(running, downgrade=downgrade, load_df=activities_df)
    rec = merge_coach_into_recommendation(base, coach_context)
    alert = hard_session_alert(coach_context, downgrade)
    if coach_context is None and goal_sessions:
        ran_today = bool(not running.empty and (
            pd.to_datetime(running["startTimeLocal"]).dt.date == date.today()).any())
        rec = merge_goal_plan_into_recommendation(base, goal_sessions, ran_today=ran_today)
        rec["coach"] = rec["coach_task"] = None
        goal = rec.get("goal_session")
        if goal and downgrade and goal["kind"] == "race":
            alert = ("Récupération dégradée (HRV ou sommeil) le jour de la course : pars "
                     "prudemment, à l'allure travaillée, et ne cherche pas à rattraper un "
                     "départ lent.")
        elif goal and downgrade and goal["kind"] in _GOAL_KEY_KINDS:
            alert = ("Récupération dégradée (HRV ou sommeil) et séance clé au programme de "
                     "ton plan Objectif : écoute tes sensations, quitte à la décaler d'un jour.")
    return {"rec": rec, "downgrade": downgrade, "alert": alert}
