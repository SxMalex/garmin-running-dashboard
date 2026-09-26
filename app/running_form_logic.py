"""
Forme de foulée et pic de sortie — deux signaux de blessure qu'on ne voit pas
à l'œil nu. Logique pure, testée.

1. **Foulée à allure égale.** Le temps de contact au sol, le ratio vertical, la
   longueur de foulée et la puissance dépendent d'abord de la vitesse : comparer
   deux sorties brutes ne dit rien. On modélise chaque métrique en fonction de la
   vitesse sur tout l'historique récent, puis on compare le **résidu** des 6
   dernières semaines à celui des 12 précédentes : « à allure égale, ton contact
   au sol a pris +9 ms ». Une dérive lente accompagne la fatigue accumulée et
   précède souvent la blessure (foulée qui s'écrase, moins de réactivité).

2. **Pic de sortie unique.** Une sortie nettement plus longue que la plus longue
   des 30 jours précédents augmente le risque de blessure, davantage que la
   charge de la semaine (Frandsen et al., BJSM 2025, ~5 200 coureurs : risque
   accru dès +10 %). Vérifié sur la dernière sortie ET sur la prochaine sortie
   longue prévue — là, on peut encore agir.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

RECENT_WEEKS, REF_WEEKS = 6, 12
MIN_RUNS = 5


@dataclass(frozen=True)
class FormMetric:
    column: str
    label: str
    unit: str
    bad_direction: int        # +1 : une hausse à allure égale est mauvaise
    threshold: float          # écart notable (unités, ou fraction si relative)
    relative: bool = False
    meaning: str = ""


FORM_METRICS = (
    FormMetric("avgGroundContact_ms", "Contact au sol", "ms", +1, 8.0,
               meaning="le pied reste plus longtemps au sol : foulée moins réactive"),
    FormMetric("avgVerticalRatio", "Ratio vertical", "%", +1, 0.4,
               meaning="plus d'énergie part en rebond qu'en avancée"),
    FormMetric("avgStride_cm", "Longueur de foulée", "cm", -1, 3.0,
               meaning="la foulée raccourcit, signe classique de fatigue"),
    FormMetric("avgPower_w", "Puissance", "W", +1, 0.03, relative=True,
               meaning="il faut plus de watts pour la même vitesse : économie de course en baisse"),
)


def pace_adjusted_trend(activities: pd.DataFrame, metric: FormMetric,
                        today: pd.Timestamp | None = None) -> dict | None:
    """
    Écart de la métrique à allure égale : médiane des résidus (métrique − modèle
    linéaire en vitesse) des 6 dernières semaines moins celle des 12 d'avant.
    None s'il manque des sorties dans l'une des deux fenêtres.
    """
    if activities is None or activities.empty or metric.column not in activities:
        return None
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    runs = activities[(activities["activityType"] == "running")].copy()
    runs["speed"] = pd.to_numeric(runs.get("avgSpeed_ms"), errors="coerce")
    runs["y"] = pd.to_numeric(runs[metric.column], errors="coerce")
    runs["t"] = pd.to_datetime(runs["startTimeLocal"])
    start = today - pd.Timedelta(weeks=RECENT_WEEKS + REF_WEEKS)
    runs = runs[(runs["t"] >= start) & (runs["speed"] > 0) & (runs["y"] > 0)].dropna(subset=["y"])
    split = today - pd.Timedelta(weeks=RECENT_WEEKS)
    recent, ref = runs[runs["t"] >= split], runs[runs["t"] < split]
    if len(recent) < MIN_RUNS or len(ref) < MIN_RUNS:
        return None
    slope, intercept = np.polyfit(runs["speed"], runs["y"], 1)
    resid = runs["y"] - (intercept + slope * runs["speed"])
    delta = float(resid[runs["t"] >= split].median() - resid[runs["t"] < split].median())
    level = float(runs["y"].median())
    threshold = metric.threshold * level if metric.relative else metric.threshold
    worse = delta * metric.bad_direction
    status = "warning" if worse >= threshold else ("good" if worse <= -threshold else "neutral")
    return {"column": metric.column, "label": metric.label, "unit": metric.unit, "delta": delta,
            "delta_pct": delta / level * 100 if level else None, "status": status,
            "meaning": metric.meaning, "recent_n": int(len(recent)), "ref_n": int(len(ref)),
            "series": runs.assign(resid=resid)[["t", "resid", "y", "speed"]]}


def form_report(activities: pd.DataFrame, today: pd.Timestamp | None = None) -> list[dict]:
    """Toutes les métriques de foulée mesurables (liste vide sans capteur)."""
    out = []
    for m in FORM_METRICS:
        r = pace_adjusted_trend(activities, m, today)
        if r:
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# Pic de sortie unique
# ---------------------------------------------------------------------------
SPIKE_WATCH, SPIKE_HIGH = 1.10, 1.30


def spike_level(ratio: float | None) -> str:
    if ratio is None:
        return "none"
    return "high" if ratio > SPIKE_HIGH else ("watch" if ratio > SPIKE_WATCH else "ok")


def longest_run_before(activities: pd.DataFrame, when: pd.Timestamp, days: int = 30) -> float | None:
    """Plus longue course dans les `days` jours précédant `when` (exclu)."""
    runs = activities[activities["activityType"] == "running"]
    t = pd.to_datetime(runs["startTimeLocal"])
    window = runs[(t < when) & (t >= when - pd.Timedelta(days=days))]
    km = pd.to_numeric(window["distance_km"], errors="coerce").dropna()
    return float(km.max()) if not km.empty and km.max() > 0 else None


def run_spike(activities: pd.DataFrame, planned: list[dict] | None = None,
              today: pd.Timestamp | None = None) -> dict:
    """
    {"last": {...} | None, "planned": {...} | None} : pour la dernière course et la
    prochaine séance prévue (plan Objectif) de plus de 12 km ou plus longue que
    tout le mois, le rapport à la plus longue des 30 jours précédents.
    """
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    out = {"last": None, "planned": None}
    if activities is None or activities.empty:
        return out
    runs = activities[activities["activityType"] == "running"].sort_values("startTimeLocal")
    if not runs.empty:
        last = runs.iloc[-1]
        when = pd.Timestamp(last["startTimeLocal"])
        ref = longest_run_before(activities, when)
        if ref and (today - when.normalize()).days <= 7:
            ratio = float(last["distance_km"]) / ref
            out["last"] = {"date": when, "km": float(last["distance_km"]), "ref_km": ref,
                           "ratio": ratio, "level": spike_level(ratio),
                           "name": str(last.get("activityName") or "")}
    ref_now = longest_run_before(activities, today + pd.Timedelta(days=1))
    for s in planned or []:
        day = pd.Timestamp(s["date"])
        km = float(s.get("distance_km") or 0)
        if day < today or s.get("kind") in ("strength", "race") or not km or not ref_now:
            continue
        ratio = km / ref_now
        if spike_level(ratio) != "ok":
            out["planned"] = {"date": day, "km": km, "ref_km": ref_now, "ratio": ratio,
                              "level": spike_level(ratio), "title": s.get("title", "")}
            break
    return out
