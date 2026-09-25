"""
Logique pure de la page Activités : intensité de chaque sortie (en % de
l'allure seuil), charge, et répartition de l'intensité dans la semaine — ce
qu'un tableau ne montre pas. Testée, sans Streamlit.

L'allure seuil est celle du TSS (`reference_threshold_sec`) : l'intensité
affichée ici est donc exactement l'IF qui entre dans la charge de Forme.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from next_session_logic import cross_training_factor, pace_tss

# Zones d'intensité par IF (allure seuil ÷ allure) — repères usuels des zones
# d'allure (≈ Coggan / Daniels) : sous 0,78 on récupère, au-dessus de 1,03 on
# est au-delà du seuil (fractionné).
INTENSITY_ZONES = [
    (0.78, "recup", "Récupération"),
    (0.88, "endurance", "Endurance"),
    (0.95, "tempo", "Tempo"),
    (1.03, "seuil", "Seuil"),
    (float("inf"), "vma", "VMA / fractionné"),
]
ZONE_LABELS = {key: label for _, key, label in INTENSITY_ZONES} | {"autre": "Autre sport"}
EASY_ZONES = {"recup", "endurance"}
HARD_ZONES = {"seuil", "vma"}

# Indicateurs de l'explorateur : clé → (colonne, libellé, format de survol, sens)
METRICS = {
    "intensite": ("intensity_pct", "Intensité (% du seuil)", "%{y:.0f} %", "higher"),
    "fc": ("avgHR", "FC moyenne (bpm)", "%{y:.0f} bpm", "neutral"),
    "allure": ("pace_min", "Allure (min/km)", "%{customdata[2]}", "lower"),
    "charge": ("tss", "Charge (TSS)", "%{y:.0f}", "higher"),
    "calories": ("calories", "Calories (kcal)", "%{y:.0f} kcal", "neutral"),
    "distance": ("distance_km", "Distance (km)", "%{y:.1f} km", "higher"),
    "cadence": ("avgCadence", "Cadence (pas/min)", "%{y:.0f} pas/min", "neutral"),
    "denivele": ("elevationGain", "Dénivelé (m D+)", "%{y:.0f} m", "neutral"),
}


def intensity_zone(intensity: float | None) -> str:
    if intensity is None or not np.isfinite(intensity):
        return "autre"
    return next(key for limit, key, _ in INTENSITY_ZONES if intensity < limit)


def enrich(activities: pd.DataFrame, threshold_sec: float) -> pd.DataFrame:
    """
    Ajoute `intensity` (IF), `intensity_pct`, `zone`, `tss`, `pace_min` à chaque
    activité. La course est notée à l'allure (même formule que le PMC) ; le
    sport croisé par sa charge Garmin ramenée sur l'échelle du TSS.
    """
    df = activities.copy()
    if df.empty:
        for col in ("intensity", "intensity_pct", "zone", "tss", "pace_min"):
            df[col] = pd.Series(dtype=float if col != "zone" else object)
        return df
    is_run = (df["activityType"] == "running") & (pd.to_numeric(df["avgPace_sec"], errors="coerce") > 0)
    pace = pd.to_numeric(df["avgPace_sec"], errors="coerce").where(is_run)
    df["intensity"] = (threshold_sec / pace).clip(upper=1.5)
    df["intensity_pct"] = df["intensity"] * 100
    df["zone"] = [intensity_zone(v) for v in df["intensity"]]
    df["pace_min"] = pace / 60
    tss = pd.Series(np.nan, index=df.index)
    if is_run.any():
        tss[is_run] = pace_tss(df[is_run], threshold_sec)
    load = pd.to_numeric(df.get("trainingLoad"), errors="coerce") if "trainingLoad" in df else None
    if load is not None:
        k = cross_training_factor(activities, threshold_sec)
        tss[~is_run] = load[~is_run] * k
    df["tss"] = tss
    return df


def intensity_distribution(enriched: pd.DataFrame) -> pd.DataFrame:
    """
    Minutes de course par semaine (lundi) et par zone : colonnes week, zone,
    minutes. Le sport croisé est exclu (pas d'allure donc pas de zone).
    """
    runs = enriched[enriched["zone"] != "autre"]
    if runs.empty:
        return pd.DataFrame(columns=["week", "zone", "minutes"])
    week = pd.to_datetime(runs["startTimeLocal"]).dt.to_period("W-SUN").dt.start_time
    out = (runs.assign(week=week).groupby(["week", "zone"], as_index=False)["duration_min"]
           .sum().rename(columns={"duration_min": "minutes"}))
    return out


def polarization(enriched: pd.DataFrame) -> dict | None:
    """
    Part du temps de course en facile / intermédiaire / dur, et un verdict.
    Repère : ~80 % en facile (Seiler). Le piège courant est la « zone grise » :
    trop de tempo, ni assez facile pour récupérer ni assez dur pour progresser.
    """
    runs = enriched[enriched["zone"] != "autre"]
    total = float(runs["duration_min"].sum())
    if total <= 0:
        return None
    easy = float(runs.loc[runs["zone"].isin(EASY_ZONES), "duration_min"].sum()) / total
    hard = float(runs.loc[runs["zone"].isin(HARD_ZONES), "duration_min"].sum()) / total
    grey = 1 - easy - hard
    if easy >= 0.75:
        status, verdict = "good", "Répartition polarisée : l'essentiel en facile, c'est ce qui construit l'endurance."
    elif grey >= 0.3:
        status, verdict = "warning", ("Beaucoup de « zone grise » (tempo) : ralentis tes footings "
                                      "pour pouvoir vraiment accélérer sur les séances clés.")
    else:
        status, verdict = "warning", "Moins de 75 % en facile : la fatigue s'accumule plus vite que la forme."
    return {"easy": easy, "grey": grey, "hard": hard, "status": status, "verdict": verdict,
            "minutes": total}
