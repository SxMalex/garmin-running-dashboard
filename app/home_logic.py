"""
Logique pure de l'Accueil (cockpit) : la semaine en 7 cases et les « signaux »
tirés des données — ce qu'on ne voit pas à l'œil nu. Sans Streamlit, testé.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pandas as pd

from formatting import weekday_fr

# Titre de l'Accueil par niveau de verdict (forme_logic) : court, lisible en 2 s.
HOME_HEADLINES = {
    2: "Tu es frais. C'est le moment de pousser.",
    1: "Rien à signaler : déroule ton plan.",
    0: "Lève le pied aujourd'hui.",
}

SHOE_WARN_KM, SHOE_RETIRE_KM = 600, 800

_SHORT_TYPES = {"running": "Course", "cycling": "Vélo", "swimming": "Natation",
                "walking": "Marche", "hiking": "Rando", "strength": "Renfo",
                "strength_training": "Renfo", "yoga": "Yoga"}


def _done_label(day_acts: pd.DataFrame) -> str:
    runs = day_acts[day_acts["activityType"] == "running"]
    if not runs.empty:
        return f"Course {runs['distance_km'].sum():.0f} km"
    kind = str(day_acts.iloc[0]["activityType"])
    return _SHORT_TYPES.get(kind, kind.replace("_", " ").capitalize())


def short_label(what: str) -> str:
    """Libellé d'une case sur téléphone : les km s'il y en a, sinon une initiale."""
    # La distance totale de la séance est en fin de libellé (ex. « Fractionné
    # allure 5 km 9.1 km ») : on prend la DERNIÈRE occurrence, pas la 1re, qui
    # peut être l'allure d'un fractionné.
    km = re.findall(r"(\d+(?:[.,]\d+)?)\s*km", what or "")
    if km:
        return f"{float(km[-1].replace(',', '.')):.0f}"
    if not what or what in ("—", "Repos"):
        return ""
    return what[0].upper()


def week_days(activities_df: pd.DataFrame, today: date,
              planned: list[dict] | None = None) -> list[dict]:
    """
    Lundi → dimanche de la semaine de `today`. Chaque case :
    {"date", "name", "what", "short", "state", "is_today"} avec state ∈
    done / today / plan / rest.
    `planned` : [{"date": "YYYY-MM-DD", "label": str, "run": bool}] (plan
    Objectif ou Run Coach ; `run` absent = course) ; une séance faite
    l'emporte sur la séance prévue du même jour. Sauf aujourd'hui : tant
    qu'aucune COURSE n'est enregistrée, une course prévue reste « à faire »
    même après un renfo ou du vélo le matin — comme la carte séance
    (`todays_session` ne tient compte que des courses).
    """
    monday = today - timedelta(days=today.weekday())
    plan_by_day, run_by_day = {}, {}
    for p in planned or []:
        day = str(p["date"])[:10]
        plan_by_day.setdefault(day, p["label"])
        if p.get("run", True):
            run_by_day.setdefault(day, p["label"])
    df = activities_df
    if df is not None and not df.empty:
        days = pd.to_datetime(df["startTimeLocal"]).dt.date
    days_out = []
    for i in range(7):
        d = monday + timedelta(days=i)
        name = weekday_fr(d)[:3].capitalize()
        acts = df[days == d] if df is not None and not df.empty else None
        done = acts is not None and not acts.empty
        if done and d == today and d.isoformat() in run_by_day:
            done = (acts["activityType"] == "running").any()
        if done:
            state, what = "done", _done_label(acts)
        elif d == today:
            # La course du jour d'abord : c'est elle que la carte séance annonce.
            state, what = "today", run_by_day.get(d.isoformat(), plan_by_day.get(d.isoformat(), "Repos"))
        elif d > today and d.isoformat() in plan_by_day:
            state, what = "plan", plan_by_day[d.isoformat()]
        else:
            state, what = "rest", "Repos" if d > today else "—"
        days_out.append({"date": d, "name": name, "what": what, "short": short_label(what),
                         "state": state, "is_today": d == today})
    return days_out


def run_totals(activities_df: pd.DataFrame | None, since: date) -> dict:
    """
    Courses depuis `since` (inclus, date locale) : {"km", "runs", "elevation",
    "pace_sec", "hr"}. Allure = temps total ÷ distance totale (moyenne des
    allures pondérée par la distance), FC = moyenne pondérée par la durée —
    une sortie de 3 km ne pèse pas autant qu'une de 20. Sans course : 0 km,
    allure et FC None (« — » à l'affichage, jamais la moyenne de tout
    l'historique).
    """
    out = {"km": 0.0, "runs": 0, "elevation": 0.0, "pace_sec": None, "hr": None}
    if activities_df is None or activities_df.empty:
        return out
    df = activities_df[activities_df["activityType"] == "running"]
    df = df[pd.to_datetime(df["startTimeLocal"]).dt.date >= since]
    if df.empty:
        return out
    km = pd.to_numeric(df["distance_km"], errors="coerce")
    pace = pd.to_numeric(df["avgPace_sec"], errors="coerce")
    minutes = pd.to_numeric(df["duration_min"], errors="coerce")
    hr = pd.to_numeric(df["avgHR"], errors="coerce")
    paced = (pace > 0) & (km > 0)
    timed = hr.notna() & (minutes > 0)
    out.update(
        km=round(float(km.fillna(0).sum()), 1), runs=int(len(df)),
        elevation=float(pd.to_numeric(df["elevationGain"], errors="coerce").fillna(0).sum()),
        pace_sec=(float((pace[paced] * km[paced]).sum() / km[paced].sum()) if paced.any() else None),
        hr=(float((hr[timed] * minutes[timed]).sum() / minutes[timed].sum()) if timed.any() else None),
    )
    return out


def planned_from_goal(sessions: list[dict] | None) -> list[dict]:
    """Séances du plan Objectif → entrées de `week_days`."""
    out = []
    for s in sessions or []:
        if s.get("kind") == "strength":
            label = f"Renfo {s.get('duration_min', 0):.0f} min"
        elif s.get("kind") == "race":
            label = "Course !"
        else:
            label = f"{s.get('title', 'Course')} {s.get('distance_km', 0):g} km"
        out.append({"date": s["date"], "label": label, "run": s.get("kind") != "strength"})
    return out


def planned_from_coach(coach: dict | None) -> list[dict]:
    """Séances à venir du plan Garmin Run Coach → entrées de `week_days`."""
    out = []
    for t in (coach or {}).get("tasks") or []:
        if t.get("date") is not None and not t.get("rest_day"):
            out.append({"date": str(t["date"]), "label": t.get("name") or "Séance",
                        "run": t.get("sport") == "running"})
    return out


_WATCH_LEVEL_TEXT = {0: "Veille santé", 1: "À surveiller", 2: "Alerte santé"}


def health_signal(watch) -> dict:
    """Carte « veille santé » (illness_logic.HealthWatch) au format des signaux."""
    body = watch.message
    if watch.flagged:
        details = ", ".join(f"{s.label} {s.value:g} {s.unit} (norme {s.baseline:g})"
                            for s in watch.flagged)
        body = f"{details}. {body}"
    return {"status": watch.status, "level": _WATCH_LEVEL_TEXT[watch.level],
            "title": watch.title, "body": body}


def spike_signal(spike: dict) -> dict | None:
    """Pic de sortie (running_form_logic.run_spike) : prévu d'abord, sinon le dernier."""
    p, last = spike.get("planned"), spike.get("last")
    if p and p["level"] == "comeback":
        return {"status": "warning", "level": "À anticiper", "title": f"Reprise : {p['km']:g} km prévus",
                "body": "Aucune course depuis plus d'un mois. Reprends par des sorties courtes et "
                        "faciles (la moitié de ton volume d'avant) plutôt que cette distance d'emblée."}
    if last and last["level"] == "comeback" and not p:
        return {"status": "warning", "level": "À surveiller", "title": "Reprise après une coupure",
                "body": f"{last['km']:.1f} km après plus d'un mois sans courir. Tendons et mollets "
                        "réagissent avec retard : garde les prochaines sorties courtes et faciles."}
    if p:
        pct = f"+{(p['ratio'] - 1) * 100:.0f} %"
        ref = (f"La plus longue séance prévue avant elle fait {p['ref_km']:.0f} km"
               if p.get("ref_source") == "planned" else
               f"Ta plus longue sortie du mois fait {p['ref_km']:.0f} km")
        return {"status": "serious" if p["level"] == "high" else "warning", "level": "À anticiper",
                "title": f"{p['km']:g} km prévus = {pct}",
                "body": f"{ref}. Un saut de plus de "
                        "10 % sur une seule sortie augmente le risque de blessure : raccourcis-la "
                        "ou garde une allure très facile."}
    if last and last["level"] != "ok":
        pct = f"+{(last['ratio'] - 1) * 100:.0f} %"
        return {"status": "warning", "level": "À surveiller", "title": f"Dernière sortie {pct} de distance",
                "body": f"{last['km']:.1f} km contre {last['ref_km']:.0f} km au plus ce mois-ci. "
                        "Écoute les tendons et mollets ces 48 h, et garde les prochaines sorties faciles."}
    return None


def home_signals(risk: dict | None, ef_change: float | None,
                 shoes: list[dict] | None) -> list[dict]:
    """
    Signaux de l'Accueil, du plus utile au moins utile. Chaque signal :
    {"status", "level", "title", "body"} ; status ∈ good/warning/serious/info.
    Un signal n'apparaît que si la donnée existe : jamais de chiffre inventé.
    """
    out = []
    risk = risk or {}
    acwr, zone = risk.get("acwr"), risk.get("acwr_zone")
    if acwr is not None:
        txt = f"{acwr:.2f}".replace(".", ",")
        if zone == "optimal":
            out.append({"status": "good", "level": "Bien dosé", "title": "Charge sous contrôle",
                        "body": f"Ta semaine pèse {txt} fois ton mois : zone 0,8-1,3, "
                                "celle où l'on progresse sans casser."})
        elif zone == "vigilance":
            out.append({"status": "warning", "level": "À surveiller", "title": "Charge en hausse rapide",
                        "body": f"Ta semaine pèse {txt} fois ton mois. Au-delà de 1,5, "
                                "le risque de blessure grimpe : garde les footings faciles."})
        elif zone == "risque":
            out.append({"status": "serious", "level": "Alerte", "title": "Pic de charge",
                        "body": f"Ta semaine pèse {txt} fois ton mois : allège les 2-3 "
                                "prochains jours."})
        elif zone == "sous_charge":
            out.append({"status": "info", "level": "Info", "title": "Semaine plus légère",
                        "body": f"Ta semaine pèse {txt} fois ton mois : bien pour récupérer, "
                                "pas pour progresser si ça dure."})
    if risk.get("monotony_high"):
        out.append({"status": "warning", "level": "À surveiller", "title": "Semaine monotone",
                    "body": "Des séances trop semblables jour après jour fatiguent plus "
                            "que leur charge : alterne facile et dur."})
    if ef_change is not None:
        pct = f"{abs(ef_change):.1f}".replace(".", ",")
        if ef_change >= 2:
            out.append({"status": "good", "level": "En progrès", "title": f"+{pct} % d'efficacité",
                        "body": "Sur 90 jours, tu cours plus vite pour le même effort cardiaque."})
        elif ef_change <= -3:
            out.append({"status": "warning", "level": "À surveiller", "title": f"−{pct} % d'efficacité",
                        "body": "Sur 90 jours, il te faut plus de battements pour la même "
                                "allure : fatigue, chaleur ou manque de volume facile."})
        else:
            out.append({"status": "info", "level": "Stable", "title": "Efficacité stable",
                        "body": "Même rapport vitesse / FC qu'il y a 3 mois."})
    active = [s for s in shoes or [] if not s.get("retired")]
    if active:
        top = max(active, key=lambda s: s.get("distance_km") or 0)
        km = top.get("distance_km") or 0
        if km >= SHOE_RETIRE_KM:
            status, level, body = "serious", "À remplacer", "Au-delà de 800 km, l'amorti a beaucoup perdu."
        elif km >= SHOE_WARN_KM:
            status, level, body = ("warning", "Bientôt", "Zone de remplacement (600-800 km) : "
                                   "garde-les pour les footings.")
        else:
            status, level, body = "good", "Chaussures", f"Encore ~{SHOE_WARN_KM - km:.0f} km avant la zone d'usure."
        out.append({"status": status, "level": level, "title": f"{top['name']} : {km:.0f} km",
                    "body": body})
    return out[:4]
