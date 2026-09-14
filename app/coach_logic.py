"""
Logique pure du coach Garmin — lit le plan adaptatif (Garmin Run Coach) et le
traduit en objectifs exploitables par la page Prochaine sortie : plan actif,
phase en cours, séance de course à venir et cibles chiffrées.

Aucune dépendance à Streamlit ni au client Garmin : ce module reçoit les
réponses brutes de l'API et rend des structures normalisées.
"""

import re
from datetime import date, timedelta

# Statut Garmin d'un plan en cours (les plans terminés sont « Completed »).
_ACTIVE_STATUS = "scheduled"

# `trainingEffectLabel` Garmin → clé de séance du dashboard. Cette clé pilote la
# couleur, l'icône et les options de tracé ORS ; les libellés inconnus retombent
# sur « endurance », le type neutre.
EFFECT_SESSION_KEYS = {
    "RECOVERY": "recuperation",
    "AEROBIC_BASE": "endurance",
    "AEROBIC_CAPACITY": "endurance",
    "LONG_RUN": "sortie_longue",
    "TEMPO": "tempo",
    "LACTATE_THRESHOLD": "tempo",
    "VO2MAX": "tempo",
    "ANAEROBIC_CAPACITY": "tempo",
    "SPEED": "tempo",
    "SPRINT": "tempo",
}
DEFAULT_SESSION_KEY = "endurance"

# Séances intenses : ce sont celles pour lesquelles une récupération dégradée
# mérite un avertissement (une sortie en endurance ne pose pas de problème).
HARD_EFFECTS = {
    "TEMPO", "LACTATE_THRESHOLD", "VO2MAX", "ANAEROBIC_CAPACITY", "SPEED", "SPRINT",
}

# Phases d'un plan Garmin Run Coach.
PHASE_LABELS = {
    "BASE": "Base",
    "BUILD": "Développement",
    "PEAK": "Pic de charge",
    "TAPER": "Affûtage",
    "TARGET_EVENT_DAY": "Jour de course",
    "RECOVERY": "Récupération",
}

# Un statut différent signifie que la séance est déjà faite ou sautée.
_PENDING_STATUS = "NOT_COMPLETE"

_RE_PACE = re.compile(r"(\d{1,2}):(\d{2})\s*/\s*km")
_RE_HR = re.compile(r"(\d{2,3})\s*bpm", re.IGNORECASE)
_RE_REPS_TIME = re.compile(r"(\d{1,2})\s*[x×]\s*(\d{1,3}):(\d{2})")
_RE_REPS_DIST = re.compile(r"(\d{1,2})\s*[x×]\s*(\d{2,5})\s*m\b", re.IGNORECASE)


def _as_date(value) -> date | None:
    """Parse une date Garmin ('2026-08-14' ou '2026-08-14T08:39:08.0')."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def active_plan(plans_raw) -> dict | None:
    """
    Plan Garmin en cours, normalisé, ou None si aucun n'est actif.

    Garmin conserve l'historique des plans terminés : on ne garde que le statut
    « Scheduled », et le plus récemment démarré s'il y en avait plusieurs.
    """
    items = []
    if isinstance(plans_raw, dict):
        items = plans_raw.get("trainingPlanList") or []
    elif isinstance(plans_raw, list):
        items = plans_raw

    candidates = []
    for plan in items:
        if not isinstance(plan, dict):
            continue
        status = ((plan.get("trainingStatus") or {}).get("statusKey") or "").lower()
        if status != _ACTIVE_STATUS:
            continue
        candidates.append({
            "plan_id": plan.get("trainingPlanId"),
            "name": plan.get("name") or "Plan Garmin",
            "level": ((plan.get("trainingLevel") or {}).get("levelKey") or ""),
            "subtype": ((plan.get("trainingSubType") or {}).get("subTypeKey") or ""),
            "start_date": _as_date(plan.get("startDate")),
            "end_date": _as_date(plan.get("endDate")),
            "weeks": plan.get("durationInWeeks"),
            "weekly_workouts": plan.get("avgWeeklyWorkouts"),
        })

    if not candidates:
        return None
    return max(candidates, key=lambda p: (p["start_date"] or date.min))


def plan_phases(plan_raw) -> list[dict]:
    """Phases du plan, normalisées et triées : {phase, label, start, end, current}."""
    if not isinstance(plan_raw, dict):
        return []
    raw = plan_raw.get("adaptivePlanPhases") or plan_raw.get("planPhases") or []
    phases = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("trainingPhase"):
            continue
        key = item["trainingPhase"]
        phases.append({
            "phase": key,
            "label": PHASE_LABELS.get(key, key.replace("_", " ").capitalize()),
            "start": _as_date(item.get("startDate")),
            "end": _as_date(item.get("endDate")),
            "current": bool(item.get("currentPhase")),
        })
    return sorted(phases, key=lambda p: p["start"] or date.min)


def current_phase(plan_raw, today: date | None = None) -> dict | None:
    """
    Phase en cours : le drapeau `currentPhase` de Garmin, avec repli sur
    l'encadrement par les dates (le drapeau peut être périmé après minuit).
    """
    phases = plan_phases(plan_raw)
    if not phases:
        return None
    flagged = next((p for p in phases if p["current"]), None)
    if flagged:
        return flagged
    if today is None:
        return None
    return next(
        (p for p in phases
         if p["start"] and p["end"] and p["start"] <= today <= p["end"]),
        None,
    )


def target_event_date(plan_raw) -> date | None:
    """Date de l'objectif du plan (phase TARGET_EVENT_DAY), sinon date de fin."""
    for phase in plan_phases(plan_raw):
        if phase["phase"] == "TARGET_EVENT_DAY":
            return phase["start"] or phase["end"]
    return None


def parse_workout_target(description) -> dict:
    """
    Extrait les cibles chiffrées d'une description Garmin.

    Formats rencontrés : « 5x1:00@4:15/km » (répétitions + allure), « 3x6:00@5:05/km »,
    « 147bpm » (cible de FC), « 8x400m@4:00/km ». Les champs non trouvés valent None
    et `raw` conserve la chaîne d'origine pour l'affichage.
    """
    target = {
        "pace_sec": None, "hr_bpm": None, "reps": None,
        "rep_duration_s": None, "rep_distance_m": None,
        "raw": description if isinstance(description, str) else "",
    }
    if not isinstance(description, str) or not description.strip():
        return target

    pace = _RE_PACE.search(description)
    if pace:
        target["pace_sec"] = int(pace.group(1)) * 60 + int(pace.group(2))

    hr = _RE_HR.search(description)
    if hr:
        target["hr_bpm"] = int(hr.group(1))

    reps_time = _RE_REPS_TIME.search(description)
    if reps_time:
        target["reps"] = int(reps_time.group(1))
        target["rep_duration_s"] = int(reps_time.group(2)) * 60 + int(reps_time.group(3))
    else:
        reps_dist = _RE_REPS_DIST.search(description)
        if reps_dist:
            target["reps"] = int(reps_dist.group(1))
            target["rep_distance_m"] = int(reps_dist.group(2))
    return target


def plan_tasks(plan_raw) -> list[dict]:
    """
    Séances programmées du plan, normalisées et triées par date.

    Une entrée par tâche : jour de repos forcé compris (`rest_day`), renforcement
    comme course (`sport`). `pending` distingue ce qui reste à faire.
    """
    if not isinstance(plan_raw, dict):
        return []

    tasks = []
    for item in plan_raw.get("taskList") or []:
        if not isinstance(item, dict):
            continue
        workout = item.get("taskWorkout") or {}
        if not isinstance(workout, dict):
            continue
        day = _as_date(item.get("calendarDate")) or _as_date(workout.get("scheduledDate"))
        if day is None:
            continue
        sport = ((workout.get("sportType") or {}) or {}).get("sportTypeKey") or ""
        effect = workout.get("trainingEffectLabel") or ""
        duration_s = workout.get("estimatedDurationInSecs") or 0
        rest_day = bool(workout.get("restDay"))
        status = workout.get("adaptiveCoachingWorkoutStatus") or ""
        tasks.append({
            "date": day,
            "sport": sport,
            "name": workout.get("workoutName") or ("Repos" if rest_day else "Séance"),
            "description": workout.get("workoutDescription") or "",
            "duration_min": round(duration_s / 60) if duration_s else 0,
            "effect": effect,
            "phrase": workout.get("workoutPhrase") or "",
            "rest_day": rest_day,
            "pending": status == _PENDING_STATUS,
            "priority": workout.get("priorityType") or "",
            "week": item.get("weekId"),
            "uuid": workout.get("workoutUuid") or "",
            "target": parse_workout_target(workout.get("workoutDescription")),
            "session_key": EFFECT_SESSION_KEYS.get(effect, DEFAULT_SESSION_KEY),
            "is_hard": effect in HARD_EFFECTS,
        })
    return sorted(tasks, key=lambda t: (t["date"], 0 if t["sport"] == "running" else 1))


def next_running_task(tasks: list[dict], today: date) -> dict | None:
    """
    Prochaine séance de course restant à faire, à partir d'aujourd'hui inclus.
    Les jours de repos, le renforcement et les séances déjà faites sont ignorés.
    """
    for task in tasks:
        if task["date"] < today or task["rest_day"] or task["sport"] != "running":
            continue
        if task["pending"]:
            return task
    return None


def week_schedule(tasks: list[dict], today: date, days: int = 7) -> list[dict]:
    """Tâches programmées sur les `days` prochains jours (aujourd'hui inclus)."""
    return [
        task for task in tasks
        if today <= task["date"] < today + timedelta(days=days)
    ]


def target_label(task: dict) -> str:
    """
    Cible de la séance en une ligne lisible : « 5 × 1:00 à 4:15/km »,
    « FC cible 147 bpm », ou la description Garmin brute si le format est inconnu.
    """
    if not task:
        return "—"
    target = task.get("target") or {}
    parts = []
    if target.get("reps") and target.get("rep_duration_s"):
        minutes, seconds = divmod(int(target["rep_duration_s"]), 60)
        parts.append(f"{target['reps']} × {minutes}:{seconds:02d}")
    elif target.get("reps") and target.get("rep_distance_m"):
        parts.append(f"{target['reps']} × {target['rep_distance_m']} m")

    if target.get("pace_sec"):
        pace = int(target["pace_sec"])
        parts.append(f"à {pace // 60}:{pace % 60:02d}/km")
    elif target.get("hr_bpm"):
        parts.append(f"FC cible {target['hr_bpm']} bpm")

    if parts:
        return " ".join(parts)
    return target.get("raw") or "Allure libre"


def coach_plan_context(plans_raw, plan_raw, today: date) -> dict | None:
    """
    Contexte complet du coach, ou None si aucun plan actif.

    Retourne le plan, sa phase courante, l'objectif visé et ses séances : de quoi
    piloter la page sans retoucher aux réponses brutes de l'API.
    """
    plan = active_plan(plans_raw)
    if plan is None:
        return None

    tasks = plan_tasks(plan_raw)
    event_date = target_event_date(plan_raw) or plan.get("end_date")
    return {
        "plan": plan,
        "phase": current_phase(plan_raw, today),
        "event_date": event_date,
        "days_to_event": (event_date - today).days if event_date else None,
        "tasks": tasks,
        "next_run": next_running_task(tasks, today),
        "week": week_schedule(tasks, today),
        "today_tasks": [t for t in tasks if t["date"] == today],
    }


def estimated_distance_km(duration_min: float, pace_sec: float) -> float:
    """
    Distance approximative d'une séance à partir de sa durée Garmin et d'une
    allure de référence — Garmin prescrit une durée, or le générateur de parcours
    a besoin d'une longueur de boucle. Pour une séance à intervalles, l'allure de
    référence doit rester l'allure moyenne réelle des sorties (échauffement et
    récupérations incluses), pas l'allure des répétitions.
    """
    if not duration_min or not pace_sec or pace_sec <= 0:
        return 0.0
    return round(duration_min * 60 / pace_sec, 1)


def merge_coach_into_recommendation(rec: dict, context: dict | None) -> dict:
    """
    Fait piloter la recommandation par le coach Garmin, en conservant le contrat
    de `recommend_session` (la page et le générateur ORS restent inchangés).

    Ce que Garmin impose : le type de séance, son nom, sa durée, ses cibles et sa
    date. Ce qui reste calculé localement : CTL/ATL/TSB, moyennes récentes, et
    l'allure de référence quand Garmin ne donne qu'une cible de FC.

    Sans plan actif ou sans séance de course à venir, `rec` est renvoyé tel quel :
    la logique interne reprend la main.
    """
    task = (context or {}).get("next_run")
    if not task:
        return dict(rec, coach=None)

    merged = dict(rec)
    reference_pace = rec.get("avg_pace_sec") or rec.get("target_pace_sec") or 0
    duration_min = task["duration_min"] or rec.get("duration_min") or 0

    merged["session_key"] = task["session_key"]
    merged["coach"] = context
    merged["coach_task"] = task
    merged["coach_target_label"] = target_label(task)
    merged["duration_min"] = duration_min
    merged["target_dist_km"] = (
        estimated_distance_km(duration_min, reference_pace) or rec.get("target_dist_km")
    )
    merged["target_dist_is_estimated"] = True
    # L'allure affichée reste celle à laquelle il courra le parcours : la cible
    # Garmin ne vaut que pour les répétitions, pas pour la séance entière.
    merged["target_pace_sec"] = reference_pace or rec.get("target_pace_sec")
    merged["suggested_date"] = task["date"]
    # Une séance imposée n'est pas une séance rétrogradée : on efface le signal
    # de la logique interne pour ne pas afficher deux messages contradictoires.
    merged["downgraded_from"] = None
    return merged


# Axe nutritionnel par type de séance. Repères d'entraînement généraux destinés à
# orienter un prompt — pas des prescriptions diététiques, et volontairement
# qualitatifs (aucune quantité n'est déduite sans poids ni journal alimentaire).
NUTRITION_FOCUS = {
    "tempo": (
        "séance intense : privilégier des glucides bien digérés la veille au soir "
        "et au petit-déjeuner si la séance est matinale, et un apport en protéines "
        "dans les heures qui suivent"
    ),
    "sortie_longue": (
        "sortie longue : repas riche en glucides la veille, en-cas facile à digérer "
        "avant le départ, et repas complet (glucides + protéines) au retour"
    ),
    "endurance": (
        "sortie en endurance : rien de particulier à prévoir, des repas équilibrés "
        "habituels suffisent"
    ),
    "recuperation": (
        "séance de récupération : journée sans besoin spécifique, l'occasion de "
        "repas simples et légers"
    ),
}
_REST_DAY_FOCUS = (
    "journée de repos : pas de besoin lié à l'effort, l'occasion de cuisiner un "
    "plat qui demande un peu plus de temps"
)


def nutrition_focus(task: dict | None) -> str:
    """
    Axe nutritionnel correspondant à la séance à venir, en une phrase.

    Sert à orienter un prompt de génération de recettes : ce sont des repères
    d'entraînement courants, pas un conseil diététique individualisé.
    """
    if not task:
        return "aucune séance programmée : repas équilibrés habituels"
    if task.get("rest_day"):
        return _REST_DAY_FOCUS
    return NUTRITION_FOCUS.get(
        task.get("session_key"), NUTRITION_FOCUS["endurance"]
    )


def hard_session_alert(context: dict | None, downgrade: int) -> str | None:
    """
    Message d'avertissement quand le coach programme une séance intense alors que
    la récupération est dégradée. None s'il n'y a rien à signaler.

    On avertit sans réécrire la séance : le plan adaptatif Garmin se réajuste
    lui-même si la séance est sautée, et réécrire la cible ici ferait diverger
    l'application de la montre.
    """
    task = (context or {}).get("next_run")
    if not task or downgrade <= 0 or not task["is_hard"]:
        return None
    return (
        f"Garmin programme une séance intense ({task['name']} — "
        f"{target_label(task)}) alors que ta récupération est en retrait. "
        "À toi de juger : la décaler d'un jour, ou la faire en réduisant les "
        "répétitions. Le plan s'adapte de lui-même si tu la sautes."
    )
