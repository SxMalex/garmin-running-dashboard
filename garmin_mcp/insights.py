"""
Outils « raisonnement » du serveur MCP : ils exposent les métriques et
verdicts CALCULÉS par le dashboard (charge, forme, dérive, plan), pas les
données brutes. Chaque fonction reçoit un `GarminClient` et réutilise la
logique pure de `app/` — mêmes chiffres que les pages, par construction.

Tout ce qui sort d'ici est sérialisable en JSON (types Python natifs).
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

import goal_store
from coach_logic import load_coach_context
from forme_logic import compute_forme_verdict, parse_recovery
from next_session_logic import (
    SESSION_TYPES,
    compute_pmc_series,
    compute_tsb,
    cross_training_factor,
    load_risk,
    reference_threshold_sec,
    todays_session,
)
from physio_logic import (
    aerobic_decoupling,
    decoupling_candidates,
    decoupling_history,
    decoupling_level,
    efficiency_change,
    efficiency_trend,
    hr_cadence_lock,
)
from race_plan_logic import (
    DISTANCES,
    athlete_baseline,
    build_race_plan,
    parse_race_time,
    plan_brief,
    predictions_by_km,
)
from garmin_client import ACTIVITY_HISTORY_LIMIT

MAX_TREND_RUNS = 8  # budget d'appels API par requête MCP


def _clean(obj):
    """Types numpy/pandas/datetime → types JSON."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        value = float(obj)
        return None if math.isnan(value) or math.isinf(value) else round(value, 3)
    if isinstance(obj, (pd.Timestamp, datetime, date)):
        return obj.isoformat()
    if obj is pd.NaT:
        return None
    return obj


def _activities(gc) -> pd.DataFrame:
    # Même profondeur d'historique que les pages : sinon CTL/TSB différents.
    return gc.get_activities(limit=ACTIVITY_HISTORY_LIMIT)


def daily_briefing(gc, today: date | None = None) -> dict:
    """Verdict du jour : fraîcheur, récupération, séance (plan Garmin d'abord), risque."""
    today = today or date.today()
    df = _activities(gc)
    ctl, atl, tsb = compute_tsb(df) if not df.empty else (0.0, 0.0, None)
    cdate = today.isoformat()
    recovery = parse_recovery(gc.get_hrv(cdate), gc.get_sleep(cdate), gc.get_daily_stats(cdate))
    verdict = compute_forme_verdict(tsb, recovery["hrv_status"], recovery["sleep_score"])
    coach = load_coach_context(gc, today)
    n_runs = int((df["activityType"] == "running").sum()) if not df.empty else 0
    # Même garde que l'Accueil (≥ 3 courses) : sinon séance annoncée ici et
    # pas sur la page, voire plantage sur un historique vide.
    session = (todays_session(df, recovery["hrv_status"], recovery["sleep_score"], coach)
               if n_runs >= 3 else None)
    rec = session["rec"] if session else None
    task = (rec or {}).get("coach_task")
    pmc = compute_pmc_series(df, reference_threshold_sec(df)) if not df.empty else None
    return _clean({
        "date": cdate,
        "load": {"ctl": ctl, "atl": atl, "tsb": tsb},
        "recovery": {"hrv_status": recovery["hrv_status"], "hrv_last_night": recovery["hrv_last"],
                     "sleep_score": recovery["sleep_score"],
                     "sleep_hours": (recovery["sleep_sec"] or 0) / 3600 or None,
                     "resting_hr": recovery["daily"].get("restingHeartRate"),
                     "body_battery_max": recovery["daily"].get("bodyBatteryHighestValue")},
        "verdict": {"label": verdict["label"], "headline": verdict["headline"],
                    "reasons": verdict["reasons"]},
        "session": ({
            "source": "garmin_run_coach" if task else "dashboard",
            "type": SESSION_TYPES[rec["session_key"]]["label"],
            "name": (task or {}).get("name"),
            "date": (task or {}).get("date") or rec.get("suggested_date_str"),
            "target_distance_km": rec.get("target_dist_km"),
            "target_pace": rec.get("target_pace_str"),
            "downgraded_by_recovery": session["downgrade"],
            "warning": session["alert"],
        } if rec else {"note": "Moins de 3 courses dans l'historique : pas de séance suggérée."}),
        "coach_plan": ({"name": coach["plan"]["name"],
                        "phase": (coach.get("phase") or {}).get("label"),
                        "days_to_event": coach.get("days_to_event")} if coach else None),
        "load_risk": load_risk(pmc) if pmc is not None else {},
        "note": "La séance suit le plan Garmin Run Coach s'il est actif : c'est la montre "
                "qui fait référence, le dashboard ne réécrit pas la séance.",
    })


def training_load(gc, days: int = 90) -> dict:
    """Série CTL/ATL/TSB hebdomadaire sur `days` jours + calibration du sport croisé."""
    df = _activities(gc)
    if df.empty:
        return {"weeks": [], "note": "Aucune activité."}
    threshold = reference_threshold_sec(df)
    pmc = compute_pmc_series(df, threshold)
    recent = pmc[pmc["date"] >= pd.Timestamp(date.today() - timedelta(days=days))]
    weekly = (recent.set_index("date").resample("W-SUN")
              .agg({"tss": "sum", "tss_run": "sum", "tss_cross": "sum",
                    "ctl": "last", "atl": "last", "tsb": "last"}).reset_index())
    return _clean({
        "threshold_pace_sec_per_km": threshold,
        "cross_training_factor": cross_training_factor(df, threshold),
        "current": pmc.iloc[-1][["ctl", "atl", "tsb"]].to_dict(),
        "weeks": weekly.rename(columns={"date": "week_ending"}).to_dict("records"),
        "load_risk": load_risk(pmc),
        "definitions": "TSS = durée × intensité² (1 h au seuil = 100) ; CTL = moyenne "
                       "exponentielle 42 j ; ATL = 7 j ; TSB = CTL − ATL en fin de journée.",
    })


def activity_analysis(gc, activity_id: int) -> dict:
    """Qualité du signal cardio (FC calée sur la cadence) et dérive d'une sortie."""
    streams = gc.get_streams(int(activity_id))
    if not streams:
        return {"activity_id": activity_id, "error": "Pas de streams pour cette activité."}
    lock = hr_cadence_lock(streams)
    drift = aerobic_decoupling(streams, exclude_mask=lock["mask"])
    return _clean({
        "activity_id": activity_id,
        "cadence_lock": {k: v for k, v in lock.items() if k != "mask"},
        "decoupling": {**drift, "level": decoupling_level(drift["decoupling_pct"]) if drift["valid"] else None},
        "reading_guide": "Dérive < 5 % : endurance solide ; 5-10 % : à consolider ; "
                         "> 10 % : marquée. Non mesurable sur fractionné ou sortie progressive.",
    })


def aerobic_trend(gc) -> dict:
    """Efficacité aérobie (tendance) et dérive des sorties longues récentes."""
    df = _activities(gc)
    trend = efficiency_trend(df)
    items = []
    for c in decoupling_candidates(df, max_runs=MAX_TREND_RUNS):
        try:
            items.append((c, gc.get_streams(int(c["activityId"]), strict=True)))
        except Exception:
            break  # premier refus Garmin : on s'arrête là
    hist = decoupling_history(items)
    return _clean({
        "efficiency_now": trend["ef_smooth"].iloc[-1] if not trend.empty else None,
        "efficiency_change_90d_pct": efficiency_change(trend, days=90),
        "decoupling_long_runs": hist.to_dict("records"),
        "runs_analysed": len(items),
        "note": "Efficacité = m/min par battement (vitesse ÷ FC), médiane 6 semaines.",
    })


def race_plan_preview(gc, distance: str, race_date: str, runs_per_week: int = 4,
                      long_run_weekday: int = 6, target_time: str | None = None,
                      include_strength: bool = True) -> dict:
    """Plan course + renfo vers un objectif (lecture seule : rien n'est enregistré ni poussé)."""
    if distance not in DISTANCES:
        return {"error": f"Distance inconnue. Choix : {list(DISTANCES)}"}
    today = date.today()
    df = _activities(gc)
    baseline = athlete_baseline(df, today, predictions_by_km(gc.get_race_predictions()))
    plan = build_race_plan(date.fromisoformat(race_date), distance, baseline, today,
                           runs_per_week=runs_per_week, long_run_weekday=long_run_weekday,
                           target_time_s=parse_race_time(target_time, distance),
                           include_strength=include_strength)
    return _clean({"summary": plan["summary"], "warnings": plan["warnings"],
                   "baseline": baseline, "brief": plan_brief(plan), "weeks": plan["weeks"]})


def current_goal(gc) -> dict:
    """
    Objectif enregistré dans le dashboard et SON plan : le plan figé à la
    validation (celui que la page affiche et envoie à la montre), sinon un
    aperçu recalculé, marqué comme tel.
    """
    from workout_export import plan_id_of
    doc = goal_store.load(gc.athlete_id)
    goal, prefs = doc.get("goal"), doc.get("prefs") or {}
    if not goal:
        return {"goal": None, "note": "Aucun objectif enregistré (page Objectif du dashboard)."}
    validated = doc.get("validated") or {}
    is_validated = validated.get("plan_id") == plan_id_of(goal, prefs)
    if is_validated:
        plan = validated["plan"]
        body = {"summary": plan.get("summary"), "warnings": plan.get("warnings", []),
                "brief": plan_brief(plan), "weeks": plan.get("weeks")}
    else:
        body = race_plan_preview(
            gc, goal["distance"], goal["race_date"], prefs.get("runs_per_week", 4),
            prefs.get("long_run_weekday", 6), goal.get("target_text"),
            prefs.get("include_strength", True))
    return _clean({"goal": goal, "prefs": prefs,
                   "validated": is_validated,
                   "plan_source": "plan validé (figé)" if is_validated
                                  else "aperçu recalculé (plan pas encore validé)",
                   "sessions_pushed_to_garmin": len(doc.get("pushed") or {}),
                   "plan": body})
