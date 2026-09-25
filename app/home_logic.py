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
    `planned` : [{"date": "YYYY-MM-DD", "label": str}] (plan Objectif ou Run
    Coach) ; une séance faite l'emporte sur la séance prévue du même jour.
    """
    monday = today - timedelta(days=today.weekday())
    plan_by_day = {}
    for p in planned or []:
        plan_by_day.setdefault(str(p["date"])[:10], p["label"])
    df = activities_df
    if df is not None and not df.empty:
        days = pd.to_datetime(df["startTimeLocal"]).dt.date
    days_out = []
    for i in range(7):
        d = monday + timedelta(days=i)
        name = weekday_fr(d)[:3].capitalize()
        acts = df[days == d] if df is not None and not df.empty else None
        if acts is not None and not acts.empty:
            state, what = "done", _done_label(acts)
        elif d == today:
            state, what = "today", plan_by_day.get(d.isoformat(), "Repos")
        elif d > today and d.isoformat() in plan_by_day:
            state, what = "plan", plan_by_day[d.isoformat()]
        else:
            state, what = "rest", "Repos" if d > today else "—"
        days_out.append({"date": d, "name": name, "what": what, "short": short_label(what),
                         "state": state, "is_today": d == today})
    return days_out


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
        out.append({"date": s["date"], "label": label})
    return out


def planned_from_coach(coach: dict | None) -> list[dict]:
    """Séances à venir du plan Garmin Run Coach → entrées de `week_days`."""
    out = []
    for t in (coach or {}).get("tasks") or []:
        if t.get("date") is not None and not t.get("rest_day"):
            out.append({"date": str(t["date"]), "label": t.get("name") or "Séance"})
    return out


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
