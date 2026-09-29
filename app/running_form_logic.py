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
    # Au pour-cent près : un plan à +10 %/semaine arrondi au dixième de km
    # (12,8 → 14,1 = 1,1016) n'est pas un saut de plus de 10 %.
    if ratio is None:
        return "none"
    r = round(ratio, 2)
    return "high" if r > SPIKE_HIGH else ("watch" if r > SPIKE_WATCH else "ok")


def longest_run_before(activities: pd.DataFrame, when: pd.Timestamp, days: int = 30) -> float | None:
    """Plus longue course dans les `days` jours précédant `when` (exclu)."""
    runs = activities[activities["activityType"] == "running"]
    t = pd.to_datetime(runs["startTimeLocal"])
    window = runs[(t < when) & (t >= when - pd.Timedelta(days=days))]
    km = pd.to_numeric(window["distance_km"], errors="coerce").dropna()
    return float(km.max()) if not km.empty and km.max() > 0 else None


COMEBACK_KM = 8.0      # après un mois sans courir, au-delà : trop d'un coup


def _ran_before(runs: pd.DataFrame, when: pd.Timestamp, days: int = 30) -> bool:
    """Une course existe AVANT la fenêtre de `days` jours : c'est une reprise, pas un premier pas."""
    return bool((pd.to_datetime(runs["startTimeLocal"]) < when - pd.Timedelta(days=days)).any())


def planned_runs(coach: dict | None, goal_sessions: list[dict] | None,
                 activities: pd.DataFrame, today: pd.Timestamp) -> list[dict]:
    """
    Séances de course à venir, au format de `run_spike`, depuis la MÊME source
    que la semaine affichée : Run Coach s'il pilote, sinon le plan Objectif.
    Run Coach prescrit une durée : la distance est estimée à ton allure médiane
    des 30 derniers jours (comme le générateur de parcours), à défaut des 90
    derniers, à défaut de tout l'historique — en reprise, justement, il n'y a
    rien sur 30 jours, et une distance nulle masquait l'alerte. Le jour de
    l'objectif Run Coach n'est pas une séance d'entraînement : écarté (comme
    `kind == "race"` pour le plan Objectif).
    """
    from coach_logic import coach_unknown, estimated_distance_km

    if coach_unknown(coach):
        return []            # panne Garmin : ni Run Coach ni, à sa place, le plan Objectif
    if not coach:
        return list(goal_sessions or [])

    pace = None
    if activities is not None and not activities.empty and "activityType" in activities:
        runs = activities[activities["activityType"] == "running"]
        when = pd.to_datetime(runs["startTimeLocal"])
        for days in (30, 90, None):
            window = runs if days is None else runs[when >= today - pd.Timedelta(days=days)]
            p = pd.to_numeric(window.get("avgPace_sec"), errors="coerce")
            p = p[p > 0] if p is not None else p
            if p is not None and not p.empty:
                pace = float(p.median())
                break
    event_day = (today.normalize() + pd.Timedelta(days=int(coach["days_to_event"]))).date() \
        if coach.get("days_to_event") is not None else None
    out = []
    for t in coach.get("tasks") or []:
        if t.get("rest_day") or t.get("sport") != "running" or t.get("date") is None:
            continue
        if event_day is not None and t["date"] == event_day:
            continue
        km = estimated_distance_km(t.get("duration_min") or 0, pace or 0)
        out.append({"date": str(t["date"]), "kind": "long" if t.get("session_key") == "sortie_longue"
                    else t.get("session_key") or "run", "distance_km": km, "title": t.get("name", "")})
    return out


def run_spike(activities: pd.DataFrame, planned: list[dict] | None = None,
              today: pd.Timestamp | None = None) -> dict:
    """
    {"last": {...} | None, "planned": {...} | None}.

    - `last` : la dernière course (≤ 7 jours) rapportée à la plus longue des 30
      jours qui la précèdent ;
    - `planned` : la première séance prévue qui dépasse de plus de 10 % la plus
      longue course réelle du mois OU la plus longue séance prévue avant elle.
      Un plan qui progresse de 10 %/semaine n'est donc pas signalé contre
      lui-même (seul un saut par rapport au réel ou à la séance d'avant l'est).
    - Reprise : sans course dans les 30 jours mais avec un historique plus
      ancien, une sortie (faite ou prévue) d'au moins `COMEBACK_KM` est signalée
      (`level` = "comeback", `ratio` = None) — c'est le cas le plus risqué, que
      le ratio ne voyait pas faute de référence.
    """
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    out = {"last": None, "planned": None}
    if activities is None or activities.empty:
        return out
    runs = activities[activities["activityType"] == "running"].sort_values("startTimeLocal")
    if not runs.empty:
        last = runs.iloc[-1]
        when = pd.Timestamp(last["startTimeLocal"])
        km = float(last["distance_km"])
        ref = longest_run_before(activities, when)
        if (today - when.normalize()).days <= 7:
            base = {"date": when, "km": km, "ref_km": ref, "name": str(last.get("activityName") or "")}
            if ref:
                ratio = km / ref
                out["last"] = {**base, "ratio": ratio, "level": spike_level(ratio)}
            elif km >= COMEBACK_KM and _ran_before(runs, when):
                out["last"] = {**base, "ratio": None, "level": "comeback"}
    ref_now = longest_run_before(activities, today + pd.Timedelta(days=1))
    comeback = ref_now is None and not runs.empty and _ran_before(runs, today + pd.Timedelta(days=1))
    longest_planned = 0.0
    for s in sorted(planned or [], key=lambda s: str(s["date"])):
        day = pd.Timestamp(s["date"])
        km = float(s.get("distance_km") or 0)
        if day < today or s.get("kind") in ("strength", "race") or not km:
            continue
        ref = max(ref_now or 0.0, longest_planned)
        if not ref:
            if comeback and km >= COMEBACK_KM:
                out["planned"] = {"date": day, "km": km, "ref_km": None, "ratio": None,
                                  "level": "comeback", "title": s.get("title", "")}
                break
            longest_planned = max(longest_planned, km)
            continue
        ratio = km / ref
        if spike_level(ratio) != "ok":
            out["planned"] = {"date": day, "km": km, "ref_km": ref, "ratio": ratio,
                              "level": spike_level(ratio), "title": s.get("title", ""),
                              # la référence : une course faite, ou une séance prévue avant
                              "ref_source": "planned" if longest_planned > (ref_now or 0) else "real"}
            break
        longest_planned = max(longest_planned, km)
    return out
