"""
Logique pure de la page Progression — records personnels Garmin,
estimations Riegel et formatage des temps de course. Testable sans Streamlit.
"""

import numpy as np
import pandas as pd

# Cibles de course communes aux prédictions Garmin et aux estimations Riegel
RACE_TARGETS = [
    ("5 km",      5.0,     "time5K"),
    ("10 km",    10.0,     "time10K"),
    ("Semi",     21.0975,  "timeHalfMarathon"),
    ("Marathon", 42.195,   "timeMarathon"),
]

_RIEGEL_EXPONENT = 1.06

# typeId Garmin → (groupe, libellé, format) — seuls les ids sûrs sont mappés,
# les autres sont ignorés plutôt que mal étiquetés.
PR_TYPES = {
    1:  ("course",   "1 km",                    "time"),
    2:  ("course",   "1 mile",                  "time"),
    3:  ("course",   "5 km",                    "time"),
    4:  ("course",   "10 km",                   "time"),
    5:  ("course",   "Semi-marathon",           "time"),
    6:  ("course",   "Marathon",                "time"),
    7:  ("course",   "Plus longue course",      "dist_km"),
    8:  ("velo",     "Plus longue sortie",      "dist_km"),
    9:  ("velo",     "D+ max",                  "elev_m"),
    11: ("velo",     "40 km le plus rapide",    "time"),
    12: ("quotidien", "Pas sur un jour",        "count"),
    13: ("quotidien", "Pas sur une semaine",    "count"),
    14: ("quotidien", "Pas sur un mois",        "count"),
    17: ("natation", "Plus longue distance",    "dist_m"),
    18: ("natation", "100 m",                   "time"),
    20: ("natation", "400 m",                   "time"),
}

PR_GROUP_LABELS = {
    "course": "🏃 Course",
    "velo": "🚴 Vélo",
    "natation": "🏊 Natation",
    "quotidien": "👣 Quotidien",
}


def fmt_race_time(total_sec: float) -> str:
    """Formate un temps de course en h/min/s (ex. 1h56'23\" ou 23'57\")."""
    h, rem = divmod(int(total_sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}'{s:02d}\"" if h else f"{m}'{s:02d}\""


def fmt_race_pace(total_sec: float, dist_km: float) -> str:
    """Allure moyenne correspondant à un temps sur une distance."""
    pace = total_sec / dist_km
    return f"{int(pace // 60)}:{int(pace % 60):02d}/km"


def _fmt_pr_value(value: float, fmt: str) -> str:
    if fmt == "time":
        return fmt_race_time(value)
    if fmt == "dist_km":
        return f"{value / 1000:.1f} km"
    if fmt == "dist_m":
        return f"{value:.0f} m"
    if fmt == "elev_m":
        return f"{value:.0f} m"
    if fmt == "count":
        return f"{value:,.0f}".replace(",", " ")
    return str(value)


def parse_personal_records(raw: list | None) -> list[dict]:
    """
    Transforme la réponse `get_personal_record` Garmin en lignes affichables :
    {group, group_label, label, value_str, date_str, activity_name, activity_id}.
    Les typeId inconnus ou sans valeur sont ignorés.
    """
    rows = []
    for pr in raw or []:
        if not isinstance(pr, dict):
            continue
        type_id = pr.get("typeId")
        value = pr.get("value")
        if type_id not in PR_TYPES or value in (None, 0):
            continue
        group, label, fmt = PR_TYPES[type_id]

        date_str = ""
        stamp = (
            pr.get("actStartDateTimeInGMTFormatted")
            or pr.get("prStartTimeGmtFormatted")
        )
        if stamp:
            date_str = pd.to_datetime(stamp).strftime("%d/%m/%Y")

        rows.append((type_id, {
            "group": group,
            "group_label": PR_GROUP_LABELS[group],
            "label": label,
            "value_str": _fmt_pr_value(float(value), fmt),
            "date_str": date_str,
            "activity_name": pr.get("activityName") or "",
            "activity_id": pr.get("activityId") or 0,
        }))

    order = {g: i for i, g in enumerate(PR_GROUP_LABELS)}
    # Ordre des typeId Garmin = ordre de distance croissante (cf. PR_TYPES) :
    # trier par label alphabétique donnait ['1 km', '10 km', '5 km', ...].
    type_order = {tid: i for i, tid in enumerate(PR_TYPES)}
    rows.sort(key=lambda r: (order[r[1]["group"]], type_order[r[0]]))
    return [row for _, row in rows]


def riegel_estimates(running_df: pd.DataFrame) -> dict[str, float]:
    """
    Temps estimés {label: secondes} via la formule de Riegel
    (T2 = T1 × (D2/D1)^1.06) sur les meilleures sorties du DataFrame.
    """
    if running_df.empty:
        return {}
    valid = running_df[
        (running_df["avgPace_sec"] > 0) & (running_df["distance_km"] >= 1.0)
    ]
    if valid.empty:
        return {}
    d1 = valid["distance_km"].to_numpy()
    t1 = valid["avgPace_sec"].to_numpy() * d1
    results = {}
    for label, target_km, _garmin_key in RACE_TARGETS:
        best_sec = float((t1 * (target_km / d1) ** _RIEGEL_EXPONENT).min())
        if np.isfinite(best_sec) and best_sec > 0:
            results[label] = best_sec
    return results


def predictions_history_df(raw: list | None) -> pd.DataFrame:
    """
    Transforme l'historique `get_race_predictions(from, to)` en DataFrame long :
    colonnes date, distance (label), time_sec. Vide si pas de données.
    """
    rows = []
    for day in raw or []:
        if not isinstance(day, dict) or not day.get("calendarDate"):
            continue
        for label, _km, key in RACE_TARGETS:
            if day.get(key):
                rows.append({
                    "date": day["calendarDate"],
                    "distance": label,
                    "time_sec": day[key],
                })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df
