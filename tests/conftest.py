"""
Fixtures partagées et stubs pour éviter les imports Streamlit/Plotly.
"""

import sys
from unittest.mock import MagicMock

# Stub streamlit et plotly avant tout import de page ou de module UI
for mod in [
    "streamlit", "streamlit.runtime", "streamlit.runtime.caching",
    "plotly", "plotly.graph_objects", "plotly.express", "plotly.subplots",
    "polyline",
]:
    sys.modules.setdefault(mod, MagicMock())

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta


# ---------------------------------------------------------------------------
# Fixtures DataFrames
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_running_df():
    """10 sorties de course réparties sur 30 jours."""
    now = datetime.now().replace(microsecond=0)
    rows = [
        {
            "activityId": i + 1,
            "startTimeLocal": now - timedelta(days=i * 3),
            "activityName": f"Run {i + 1}",
            "activityType": "running",
            "distance_km": 8.0 + i * 0.5,
            "duration_min": 45.0 + i * 2,
            "avgPace": "5:30/km",
            "avgPace_sec": 330.0 + i * 5,
            "avgHR": 145.0 + i,
            "maxHR": 170.0 + i,
            "avgCadence": 170.0 + i,
            "calories": 450 + i * 10,
            "elevationGain": 80.0 + i * 5,
            "avgSpeed_ms": 3.03 + i * 0.05,
            "kudosCount": i,
            "startLat": 48.85 + i * 0.01,
            "startLon": 2.35 + i * 0.01,
        }
        for i in range(10)
    ]
    df = pd.DataFrame(rows)
    df["startTimeLocal"] = pd.to_datetime(df["startTimeLocal"])
    return df


@pytest.fixture
def mixed_activities_df(sample_running_df):
    """Runs + activités vélo pour tester le filtrage par type."""
    cycling = sample_running_df.copy()
    cycling["activityType"] = "cycling"
    cycling["activityId"] = cycling["activityId"] + 100
    return pd.concat([sample_running_df, cycling], ignore_index=True)


@pytest.fixture
def empty_df():
    return pd.DataFrame()


@pytest.fixture
def make_running_df():
    """
    Factory partagée pour construire un DataFrame de sorties course.
    Utilisée par les tests de `next_session_logic` (compute_tsb, recommend_session,
    suggest_next_date) pour éviter la duplication des helpers `_make_df` locaux.

    Paramètres :
    - n : nombre de sorties (défaut 10)
    - days_apart : espacement entre sorties en jours (défaut 3)
    - pace_sec : allure en sec/km (défaut 330)
    - distance_km : distance par sortie (défaut 10.0)
    - duration_min : durée par sortie (défaut 55.0)
    - with_location : si True, inclut activityId / activityName / startLat / startLon
    """
    def _factory(
        n: int = 10,
        days_apart: int = 3,
        pace_sec: float = 330.0,
        distance_km: float = 10.0,
        duration_min: float = 55.0,
        with_location: bool = True,
    ) -> pd.DataFrame:
        now = datetime.now()
        rows = []
        for i in range(n):
            row = {
                "startTimeLocal": now - timedelta(days=i * days_apart),
                "activityType": "running",
                "distance_km": distance_km,
                "duration_min": duration_min,
                "avgPace_sec": pace_sec,
                "avgHR": 148.0,
                "elevationGain": 60.0,
            }
            if with_location:
                row.update({
                    "activityName": f"Run {i}",
                    "startLat": 48.85,
                    "startLon": 2.35,
                    "activityId": i + 1,
                })
            rows.append(row)
        df = pd.DataFrame(rows)
        df["startTimeLocal"] = pd.to_datetime(df["startTimeLocal"])
        return df
    return _factory


@pytest.fixture
def recent_running_df():
    """Sorties concentrées dans la semaine courante."""
    now = datetime.now().replace(microsecond=0)
    rows = [
        {
            "activityId": i + 1,
            "startTimeLocal": now - timedelta(hours=i * 30),
            "activityName": f"Run {i + 1}",
            "activityType": "running",
            "distance_km": 10.0,
            "duration_min": 55.0,
            "avgPace": "5:30/km",
            "avgPace_sec": 330.0,
            "avgHR": 148.0,
            "maxHR": 172.0,
            "avgCadence": 172.0,
            "calories": 600,
            "elevationGain": 60.0,
            "avgSpeed_ms": 3.03,
            "kudosCount": 1,
            "startLat": 48.85,
            "startLon": 2.35,
        }
        for i in range(4)
    ]
    df = pd.DataFrame(rows)
    df["startTimeLocal"] = pd.to_datetime(df["startTimeLocal"])
    return df


# ---------------------------------------------------------------------------
# Fixtures ORS
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_ors_geojson():
    return {
        "features": [
            {
                "geometry": {
                    "coordinates": [
                        [2.35, 48.85, 120.0],
                        [2.36, 48.86, 125.0],
                        [2.37, 48.85, 118.0],
                        [2.35, 48.85, 120.0],
                    ]
                },
                "properties": {
                    "summary": {"distance": 5200.0, "duration": 1800.0},
                    "ascent": 45.0,
                },
            }
        ]
    }


@pytest.fixture
def sample_route():
    return {
        "lats": [48.85, 48.86, 48.87, 48.85],
        "lons": [2.35, 2.36, 2.37, 2.35],
        "elevations": [120.0, 125.0, 130.0, 120.0],
        "distance_km": 5.2,
        "ascent_m": 45,
        "duration_s": 1800,
    }
