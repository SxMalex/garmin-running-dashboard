"""
Conversion des séances du plan (`race_plan_logic`) au format « workout »
Garmin Connect (JSON de `upload_workout`). Logique pure, testable sans API.

Le format reprend les DTO de `garminconnect.workout` (ExecutableStepDTO /
RepeatGroupDTO, ids de `SportType`, `StepType`, `ConditionType`,
`TargetType`). Les cibles d'allure sont des zones de vitesse en m/s
(`pace.zone`) : `targetValueOne` = borne lente, `targetValueTwo` = borne
rapide.

Chaque séance poussée porte une étiquette déterministe dans son nom
(`[GD-<plan>-<clé>]`) : c'est ce qui permet de retrouver et de retirer ce
que le dashboard a créé, même si le journal local est perdu.
"""

from __future__ import annotations

import hashlib
import json

SPORT_RUNNING = {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}
SPORT_STRENGTH = {"sportTypeId": 5, "sportTypeKey": "strength_training", "displayOrder": 5}

_STEP_TYPES = {
    "warmup": {"stepTypeId": 1, "stepTypeKey": "warmup", "displayOrder": 1},
    "cooldown": {"stepTypeId": 2, "stepTypeKey": "cooldown", "displayOrder": 2},
    "interval": {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3},
    "recovery": {"stepTypeId": 4, "stepTypeKey": "recovery", "displayOrder": 4},
    "repeat": {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6},
}
_END_TIME = {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2,
             "displayable": True}
_END_LAP = {"conditionTypeId": 1, "conditionTypeKey": "lap.button", "displayOrder": 1,
            "displayable": True}
_END_ITERATIONS = {"conditionTypeId": 7, "conditionTypeKey": "iterations",
                   "displayOrder": 7, "displayable": False}
_NO_TARGET = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target",
              "displayOrder": 1}
_PACE_TARGET = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone",
                "displayOrder": 6}

PUSHABLE_KINDS = {"easy", "long", "shakeout", "strides", "tempo", "interval",
                  "race_pace", "strength"}
TAG_PREFIX = "[GD-"
MAX_NAME_LEN = 60


def plan_id_of(goal: dict, prefs: dict) -> str:
    """Identifiant court et stable d'un plan (objectif + préférences)."""
    raw = json.dumps({"goal": goal, "prefs": prefs}, sort_keys=True, default=str)
    return hashlib.md5(raw.encode()).hexdigest()[:6]


def session_key(session: dict) -> str:
    """Clé unique d'une séance dans un plan : date + type."""
    return f"{session['date']}-{session['kind']}"


def workout_tag(plan_id: str, session: dict) -> str:
    """Étiquette unique par créneau : plan + jour + course/renfo (« strides » et
    « strength » ne se confondent plus, cf. `slot`)."""
    return f"{TAG_PREFIX}{plan_id}-{session['date'].replace('-', '')}-{slot(session['date'], session['kind'])[1][:3]}]"


def fingerprint(payload: dict) -> str:
    """Empreinte du contenu envoyé : détecte une séance modifiée par un recalcul."""
    return hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:10]


# Empreinte journalisée quand le contenu d'une séance retrouvée dans Garmin
# n'a pas pu être confirmé identique au plan : jamais égale à `fingerprint()`
# (hexadécimal), elle rend l'entrée périmée pour `stale_pushes`.
UNVERIFIED_FINGERPRINT = "unverified"


CONTENT_TOLERANCE = 1e-3   # m/s ou s : Garmin relit en float32 ; 1 s/km vaut ~1e-2 m/s


def _number(value):
    return None if value is None else float(value)


def _same(a, b) -> bool:
    """Égalité de contenu, nombres à `CONTENT_TOLERANCE` près (un arrondi fixe basculait
    d'un côté ou de l'autre selon que Garmin relit la valeur en float32)."""
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= CONTENT_TOLERANCE
    return a == b


def _step_content(steps: list) -> list:
    return [(
        s.get("type"),
        (s.get("stepType") or {}).get("stepTypeKey"),
        (s.get("endCondition") or {}).get("conditionTypeKey"),
        _number(s.get("endConditionValue")),
        (s.get("targetType") or {}).get("workoutTargetTypeKey"),
        _number(s.get("targetValueOne")),
        _number(s.get("targetValueTwo")),
        _number(s.get("numberOfIterations")),
        (s.get("description") or "").strip(),
        _step_content(s.get("workoutSteps") or []),
    ) for s in sorted(steps or [], key=lambda s: s.get("stepOrder") or 0)]


def workout_content(workout: dict) -> list:
    """
    Ce que la montre affiche et exécute (nom, sport, étapes, durées, cibles),
    sans ce que Garmin ajoute à la lecture (ids, propriétaire, dates) : compare
    la séance relue (`get_workout_by_id`) au payload qu'on enverrait aujourd'hui.
    """
    workout = workout or {}
    return [workout.get("workoutName"), (workout.get("sportType") or {}).get("sportTypeKey"),
            [_step_content(seg.get("workoutSteps"))
             for seg in sorted(workout.get("workoutSegments") or [],
                               key=lambda seg: seg.get("segmentOrder") or 0)]]


def reconciled_fingerprint(payload: dict, remote: dict) -> str:
    """
    Empreinte à journaliser pour une séance retrouvée par son étiquette
    (journal perdu, réponse d'envoi perdue) : celle du payload seulement si la
    séance relue a le même contenu. Sinon — plan recalculé depuis (même nom,
    autres allures ; ou autre séance sur le même créneau) ou séance modifiée
    dans Garmin — `UNVERIFIED_FINGERPRINT`, qui
    la fait signaler comme à retirer au lieu de passer pour à jour.
    """
    if _same(workout_content(remote), workout_content(payload)):
        return fingerprint(payload)
    return UNVERIFIED_FINGERPRINT


def workout_name(plan_id: str, session: dict) -> str:
    tag = workout_tag(plan_id, session)
    title = session.get("title", "Séance")
    return f"{title[:MAX_NAME_LEN - len(tag) - 1]} {tag}"


def _target(step: dict) -> dict:
    fast, slow = step.get("pace_fast"), step.get("pace_slow")
    if not fast or not slow:
        return {"targetType": dict(_NO_TARGET)}
    return {
        "targetType": dict(_PACE_TARGET),
        "targetValueOne": round(1000 / slow, 4),   # borne lente (m/s)
        "targetValueTwo": round(1000 / fast, 4),   # borne rapide (m/s)
    }


def _executable(step: dict, order: int) -> dict:
    return {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": dict(_STEP_TYPES[step["type"]]),
        "endCondition": dict(_END_TIME),
        "endConditionValue": float(step["duration_s"]),
        **_target(step),
    }


def _steps(steps: list[dict], counter: list[int]) -> list[dict]:
    out = []
    for step in steps:
        counter[0] += 1
        order = counter[0]
        if step["type"] == "repeat":
            out.append({
                "type": "RepeatGroupDTO",
                "stepOrder": order,
                "stepType": dict(_STEP_TYPES["repeat"]),
                "numberOfIterations": int(step["count"]),
                "endCondition": dict(_END_ITERATIONS),
                "endConditionValue": float(step["count"]),
                "smartRepeat": False,
                "workoutSteps": _steps(step["steps"], counter),
            })
        else:
            out.append(_executable(step, order))
    return out


def workout_payload(session: dict, plan_id: str) -> dict:
    """JSON `upload_workout` d'une séance du plan."""
    kind = session["kind"]
    if kind not in PUSHABLE_KINDS:
        raise ValueError(f"Séance non exportable : {kind}")
    duration_s = int(session.get("duration_min", 0) * 60)
    description = f"{session.get('target', '')}\n\nPourquoi : {session.get('why', '')}".strip()
    if kind == "strength":
        sport = SPORT_STRENGTH
        steps = [{
            "type": "ExecutableStepDTO", "stepOrder": 1,
            "stepType": dict(_STEP_TYPES["interval"]),
            # Pas de durée imposée : la séance se termine au bouton « tour ».
            "endCondition": dict(_END_LAP), "endConditionValue": None,
            "targetType": dict(_NO_TARGET),
            "description": session.get("target", ""),
        }]
    else:
        sport = SPORT_RUNNING
        steps = _steps(session.get("steps") or [], [0])
    return {
        "workoutName": workout_name(plan_id, session),
        "description": description[:1000],
        "sportType": dict(sport),
        "estimatedDurationInSecs": duration_s,
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": dict(sport),
            "workoutSteps": steps,
        }],
    }


def is_dashboard_workout(workout: dict, plan_id: str | None = None) -> bool:
    """Séance créée par le dashboard (et, si précisé, par ce plan)."""
    name = (workout or {}).get("workoutName") or ""
    prefix = f"{TAG_PREFIX}{plan_id}-" if plan_id else TAG_PREFIX
    return prefix in name


# ---------------------------------------------------------------------------
# Garde et sélection du push
# ---------------------------------------------------------------------------

PUSH_HORIZON_DAYS = 14   # au-delà, le plan aura bougé (volume, forme) : on pousse par vagues


def coach_state(plans_raw, error: Exception | None = None) -> str:
    """
    « active » (plan Garmin Run Coach en cours), « none » (aucun) ou
    « unknown » (Garmin n'a pas répondu). Trois états et non deux : un appel
    raté ne doit pas être lu comme « pas de plan », sinon on pousserait des
    séances concurrentes de celles de la montre.
    """
    if error is not None:
        return "unknown"
    from coach_logic import active_plan
    return "active" if active_plan(plans_raw) else "none"


def push_gate(write_enabled: bool, state: str) -> tuple[bool, str]:
    """(autorisé, explication) pour pousser des séances dans le calendrier."""
    if not write_enabled:
        return False, ("Écriture Garmin désactivée (GARMIN_WRITE_ENABLED). Elle n'est "
                       "activée par défaut qu'en local : une instance exposée doit être "
                       "protégée avant de pouvoir modifier ton calendrier.")
    if state == "active":
        return False, ("Un plan Garmin Run Coach est actif : il reste la référence de ta "
                       "montre. Termine-le ou mets-le en pause pour pousser ce plan.")
    if state == "unknown":
        return False, ("Impossible de vérifier auprès de Garmin qu'aucun plan Run Coach "
                       "n'est actif. Réessaie dans un moment.")
    return True, ""


def slot(date_iso: str, kind: str) -> tuple[str, str]:
    """Créneau d'une séance : un jour peut porter une course ET un renfo, pas plus."""
    return date_iso, "strength" if kind == "strength" else "run"


def pushable_sessions(
    sessions: list[dict], today_iso: str, pushed: dict,
    horizon_days: int = PUSH_HORIZON_DAYS,
) -> list[dict]:
    """
    Séances à venir (aujourd'hui inclus) dans l'horizon, dont le créneau
    (jour + course/renfo) n'est pas déjà occupé par une séance envoyée.
    Dédupliquer sur le créneau et non sur le type : si le plan change entre
    deux envois, un « seuil » devenu « lignes droites » le même jour ne doit
    pas donner deux séances sur la montre (revue P2).
    """
    from datetime import date, timedelta
    today = date.fromisoformat(today_iso)
    last = today + timedelta(days=horizon_days)
    taken = {slot(e.get("date", ""), e.get("kind", "")) for e in pushed.values()}
    return [
        s for s in sessions
        if s.get("kind") in PUSHABLE_KINDS
        and today <= date.fromisoformat(s["date"]) <= last
        and slot(s["date"], s["kind"]) not in taken
    ]


def stale_pushes(pushed: dict, plan_id: str, today_iso: str,
                 sessions: list[dict] | None = None) -> dict:
    """
    Séances à venir déjà envoyées qui ne correspondent plus au plan affiché :
    autre plan (objectif ou préférences changés), créneau disparu, ou contenu
    différent (empreinte) après « Recalculer », ou jamais confirmé
    (`UNVERIFIED_FINGERPRINT`, séance rattachée par son nom). Elles doivent être retirées
    avant tout nouvel envoi, sinon la montre mélange deux versions du plan.
    """
    current = {}
    for s in sessions or []:
        if s.get("kind") in PUSHABLE_KINDS:
            current[slot(s["date"], s["kind"])] = fingerprint(workout_payload(s, plan_id))
    stale = {}
    for key, e in pushed.items():
        if e.get("date", "") < today_iso:
            continue
        if e.get("plan_id") != plan_id:
            stale[key] = e
        elif sessions is not None:
            expected = current.get(slot(e.get("date", ""), e.get("kind", "")))
            if expected is None or (e.get("fingerprint") and e["fingerprint"] != expected):
                stale[key] = e
    return stale


def future_pushes(pushed: dict, today_iso: str) -> dict:
    """Séances envoyées à partir d'aujourd'hui (les passées restent dans l'historique)."""
    return {k: e for k, e in pushed.items() if e.get("date", "") >= today_iso}
