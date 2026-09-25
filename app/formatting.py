"""
Fonctions de formatage pures (pace, vitesse, types d'activité Garmin).

Aucune dépendance à Streamlit ni au client Garmin — ce module est importable
depuis n'importe quelle couche (UI, client, logique métier) sans cycle.
"""

import re

import numpy as np

_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$])")


def md_escape(text) -> str:
    """
    Texte externe (nom d'activité, de séance, de chaussure saisi dans Garmin)
    → Markdown inerte, sur une ligne. Sans ça, une activité nommée
    `![](https://tiers/p.png)` ferait charger une image distante (fuite d'IP) et
    `**x**` réécrirait la mise en forme de la page.
    """
    return _MD_SPECIAL.sub(r"\\\1", " ".join(str(text).split()))


# ---------------------------------------------------------------------------
# Pace / vitesse
# ---------------------------------------------------------------------------

def seconds_to_pace_str(pace_sec: float) -> str:
    """Convertit un pace en secondes/km en chaîne min:sec/km."""
    if not pace_sec or pace_sec <= 0 or np.isnan(pace_sec):
        return "—"
    # Arrondi (pas troncature) : même règle que les allures du plan
    # (race_plan_logic), sinon un même objectif s'affiche 4:58 ici et 4:59 là.
    total = int(round(pace_sec))
    return f"{total // 60}:{total % 60:02d}/km"


def speed_to_pace(speed_ms: float) -> str:
    """Convertit une vitesse en m/s en pace min:sec/km."""
    if not speed_ms or speed_ms <= 0:
        return "—"
    return seconds_to_pace_str(1000 / speed_ms)


def speed_to_pace_seconds(speed_ms: float) -> float:
    """Convertit une vitesse en m/s en pace en secondes/km."""
    if not speed_ms or speed_ms <= 0:
        return 0.0
    return 1000 / speed_ms


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_WEEKDAYS_FR = [
    "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche",
]


def weekday_fr(value) -> str:
    """
    Nom du jour de la semaine en français, en minuscules (« jeudi ») — destiné à
    être inséré dans une phrase. Chaîne vide si la valeur n'est pas une date.
    """
    try:
        return _WEEKDAYS_FR[value.weekday()]
    except (AttributeError, IndexError, TypeError):
        return ""


# ---------------------------------------------------------------------------
# Types d'activité / événement
# ---------------------------------------------------------------------------

_ACTIVITY_TYPE_MAP = {
    # Course (typeKeys Garmin)
    "running": "running",
    "trail_running": "running",
    "treadmill_running": "running",
    "track_running": "running",
    "indoor_running": "running",
    "virtual_run": "running",
    "obstacle_run": "running",
    "street_running": "running",
    "ultra_run": "running",
    # Vélo
    "cycling": "cycling",
    "road_biking": "cycling",
    "mountain_biking": "cycling",
    "gravel_cycling": "cycling",
    "cyclocross": "cycling",
    "indoor_cycling": "cycling",
    "virtual_ride": "cycling",
    "e_bike_fitness": "cycling",
    "e_bike_mountain": "cycling",
    # Natation
    "swimming": "swimming",
    "lap_swimming": "swimming",
    "open_water_swimming": "swimming",
    # Marche / Randonnée
    "walking": "walking",
    "casual_walking": "walking",
    "speed_walking": "walking",
    "hiking": "hiking",
    # Musculation / Autre
    "strength_training": "strength",
    "yoga": "yoga",
    "pilates": "yoga",
    "cardio": "cardio",
    "indoor_cardio": "cardio",
    "hiit": "cardio",
    "fitness_equipment": "cardio",
}


def normalize_activity_type(type_key: str) -> str:
    """Normalise les typeKey Garmin en catégories lisibles."""
    return _ACTIVITY_TYPE_MAP.get(
        type_key, type_key.lower() if type_key else "unknown"
    )


_EVENT_TYPE_LABELS = {
    "race": "Race",
    "training": "Entraînement",
    "fitness": "Entraînement",
}


def event_type_label(event_type_key) -> str:
    """Traduit l'eventType Garmin (typeKey) en libellé lisible."""
    if not event_type_key or not isinstance(event_type_key, str):
        return "Normal"
    return _EVENT_TYPE_LABELS.get(event_type_key.lower(), "Normal")


# ---------------------------------------------------------------------------
# Streams / cartes
# ---------------------------------------------------------------------------

def decimate(values: list, target: int = 1000) -> list:
    """
    Sous-échantillonne une liste en gardant ~target points équirépartis.
    Utile pour réduire la charge Plotly sur les streams haute résolution
    (2k-15k points/activité) sans perte visuelle.
    """
    n = len(values)
    if n <= target or target <= 0:
        return list(values)
    step = n / target
    return [values[int(i * step)] for i in range(target)]


def map_zoom(lats: list[float], lons: list[float]) -> tuple[float, float, int]:
    """Retourne (center_lat, center_lon, zoom) depuis une liste de coordonnées."""
    center_lat = (min(lats) + max(lats)) / 2
    center_lon = (min(lons) + max(lons)) / 2
    max_range = max(max(lats) - min(lats), max(lons) - min(lons))
    if max_range < 0.01:
        zoom = 15
    elif max_range < 0.05:
        zoom = 13
    elif max_range < 0.15:
        zoom = 12
    elif max_range < 0.4:
        zoom = 11
    elif max_range < 1.0:
        zoom = 10
    else:
        zoom = 9
    return center_lat, center_lon, zoom
