"""
Faux client `garminconnect.Garmin` pour faire tourner les pages Streamlit
(AppTest) sans compte ni réseau.

Les formes de réponse reprennent celles de l'API réelle utilisées par
`GarminClient` (activités, détails metricDescriptors, sommeil, HRV…). Les
méthodes non modélisées renvoient une réponse vide : c'est aussi ce que fait
Garmin quand une montre ne supporte pas une métrique, et les pages doivent
l'encaisser sans planter.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import numpy as np
from garminconnect import Garmin

DESCRIPTORS = [
    "sumDuration", "sumDistance", "directHeartRate", "directElevation",
    "directSpeed", "directDoubleCadence", "directLatitude", "directLongitude",
]


def _run(i: int, day: datetime, kind: str = "running") -> dict:
    """Une activité au format de `get_activities` (liste)."""
    rng = np.random.default_rng(i)
    if kind == "running":
        duration = float(rng.integers(30, 100)) * 60
        speed = float(rng.uniform(2.6, 3.3))
        return {
            "activityId": 1000 + i,
            "activityName": f"Course {i}",
            "startTimeLocal": day.strftime("%Y-%m-%d %H:%M:%S"),
            "activityType": {"typeKey": "running"},
            "eventType": {"typeKey": "race" if i % 17 == 0 else "training"},
            "distance": duration * speed,
            "duration": duration,
            "movingDuration": duration,
            "averageSpeed": speed,
            "averageHR": float(rng.integers(130, 160)),
            "maxHR": float(rng.integers(165, 185)),
            "averageRunningCadenceInStepsPerMinute": float(rng.integers(168, 184)),
            "calories": 600,
            "elevationGain": float(rng.integers(20, 200)),
            "startLatitude": 43.6,
            "startLongitude": 1.44,
            "activityTrainingLoad": float(rng.integers(40, 180)),
            "vO2MaxValue": 50.0,
        }
    return {
        "activityId": 1000 + i,
        "activityName": f"{kind} {i}",
        "startTimeLocal": day.strftime("%Y-%m-%d %H:%M:%S"),
        "activityType": {"typeKey": kind},
        "eventType": {"typeKey": "uncategorized"},
        "distance": 0.0 if kind == "strength_training" else 30000.0,
        "duration": 3600.0,
        "movingDuration": 3600.0,
        "averageSpeed": 0.0 if kind == "strength_training" else 8.3,
        "averageHR": 120.0,
        "maxHR": 150.0,
        "calories": 400,
        "activityTrainingLoad": 60.0,
    }


def _details(activity: dict) -> dict:
    """Réponse `get_activity_details` : streams échantillonnés toutes les 5 s."""
    duration = int(activity.get("duration") or 0)
    speed = activity.get("averageSpeed") or 0.0
    n = max(duration // 5, 2)
    rows = []
    for k in range(n):
        t = k * 5.0
        hr = 125 + 25 * (1 - math.exp(-t / 600)) + 0.002 * t  # montée puis légère dérive
        rows.append({"metrics": [
            t, t * speed, hr, 150 + 10 * math.sin(t / 300),
            speed, 176.0, 43.6 + t * 1e-6, 1.44 + t * 1e-6,
        ]})
    return {
        "metricDescriptors": [
            {"key": key, "metricsIndex": idx} for idx, key in enumerate(DESCRIPTORS)
        ],
        "activityDetailMetrics": rows,
    }


class FakeGarmin:
    """Stub de `garminconnect.Garmin` utilisé par `GarminClient`."""

    display_name = "fake-user"

    def __init__(self, n_runs: int = 40, today: date | None = None):
        today_dt = datetime.combine(today or date.today(), datetime.min.time())
        self.activities = []
        for i in range(n_runs):
            day = today_dt - timedelta(days=3 * i, hours=-8)
            self.activities.append(_run(i, day))
        self.activities.append(_run(900, today_dt - timedelta(days=4), "cycling"))
        self.activities.append(_run(901, today_dt - timedelta(days=9), "strength_training"))
        self.activities.sort(key=lambda a: a["startTimeLocal"], reverse=True)
        self.calls: list[str] = []
        self.client = self  # api.client.connectapi(...)

    # Méthodes dont l'API réelle renvoie une liste (et non un dict) quand vide.
    _LIST_METHODS = {"get_body_battery", "get_training_readiness", "get_personal_record"}

    def __getattr__(self, name):
        # Méthode non modélisée : réponse vide, comme une métrique non supportée.
        # Seulement pour les méthodes qui existent vraiment : une faute de
        # frappe dans le code appelant doit échouer, pas renvoyer {}.
        if name.startswith("get_") and hasattr(Garmin, name):
            def _empty(*args, **kwargs):
                self.calls.append(name)
                return [] if name in self._LIST_METHODS else {}
            return _empty
        raise AttributeError(name)

    # -- activités ---------------------------------------------------------
    def get_activities(self, start=0, limit=20, *args, **kwargs):
        self.calls.append("get_activities")
        return self.activities[start:start + limit]

    def _find(self, activity_id):
        return next(a for a in self.activities if str(a["activityId"]) == str(activity_id))

    def get_activity(self, activity_id):
        self.calls.append("get_activity")
        act = self._find(activity_id)
        return {"activityId": act["activityId"], "summaryDTO": {
            "distance": act["distance"], "duration": act["duration"],
            "averageSpeed": act["averageSpeed"], "averageHR": act["averageHR"],
            "maxHR": act["maxHR"], "calories": act["calories"],
        }}

    def get_activity_splits(self, activity_id):
        self.calls.append("get_activity_splits")
        return {"lapDTOs": []}

    def get_activity_details(self, activity_id, maxchart=2000, maxpoly=4000):
        self.calls.append("get_activity_details")
        return _details(self._find(activity_id))

    # -- profil / matériel ---------------------------------------------------
    def get_full_name(self):
        return "Coureur Test"

    def get_device_last_used(self):
        return {"userProfileNumber": 1}

    def get_gear(self, *args, **kwargs):
        return []

    def connectapi(self, path, **kwargs):
        self.calls.append(f"connectapi:{path}")
        if "socialProfile" in path:
            return {"profileId": 42}
        return []

    # -- santé -----------------------------------------------------------------
    def get_sleep_data(self, cdate):
        return {"dailySleepDTO": {"calendarDate": cdate, "sleepTimeSeconds": 27000,
                                  "sleepScores": {"overall": {"value": 78}}}}

    def get_hrv_data(self, cdate):
        return {"hrvSummary": {"calendarDate": cdate, "status": "BALANCED",
                               "lastNightAvg": 55, "weeklyAvg": 54}}

    def get_training_plans(self, *args, **kwargs):
        if getattr(self, "plans_error", None):
            raise self.plans_error
        return {"trainingPlanList": list(getattr(self, "plans", []))}

    # -- écriture (calendrier) ---------------------------------------------------
    def _library(self):
        if not hasattr(self, "workouts"):
            self.workouts, self.scheduled, self._next_id = {}, {}, 5000
        return self.workouts

    def upload_workout(self, payload):
        self._library()
        self._next_id += 1
        self.workouts[self._next_id] = payload
        self.calls.append("upload_workout")
        return {"workoutId": self._next_id, "workoutName": payload["workoutName"]}

    def schedule_workout(self, workout_id, date_str):
        self._library()
        if getattr(self, "schedule_error", None):
            raise self.schedule_error
        self._next_id += 1
        self.scheduled[self._next_id] = (workout_id, date_str)
        self.calls.append("schedule_workout")
        return {"workoutScheduleId": self._next_id}

    def unschedule_workout(self, schedule_id):
        self._library().pop(None, None)
        self.scheduled.pop(int(schedule_id), None)

    def delete_workout(self, workout_id):
        self._library().pop(int(workout_id), None)

    def get_workouts(self, start=0, limit=100):
        items = [{"workoutId": k, **v} for k, v in self._library().items()]
        return items[start:start + limit]

    def get_scheduled_workouts(self, year, month):
        self._library()
        items = [{"workoutScheduleId": sid, "date": day, "workout": {"workoutId": wid}}
                 for sid, (wid, day) in self.scheduled.items()
                 if day.startswith(f"{int(year):04d}-{int(month):02d}")]
        return {"calendarItems": items}

    def get_workout_by_id(self, workout_id):
        lib = self._library()
        if int(workout_id) not in lib:
            raise RuntimeError("API Error 404 - Not Found")
        return {"workoutId": int(workout_id), **lib[int(workout_id)]}

    def get_race_predictions(self, *args, **kwargs):
        return {}
