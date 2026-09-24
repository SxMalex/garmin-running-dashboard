"""
Tests du client Garmin : transformations pures (streams, splits, laps,
lignes d'activité), cache disque et mapping d'erreurs.
Aucun appel réseau — l'API Garmin est systématiquement stubée.
"""

import time

import pandas as pd
import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

import garmin_client as gc
from garmin_client import (
    GarminClient,
    activity_row,
    build_streams,
    compute_grade_stream,
    compute_km_splits,
    date_windows,
    extract_hrv_days,
    extract_resting_hr_days,
    extract_sleep_days,
    extract_vo2max_days,
    laps_to_splits,
    safe_load_activities,
    summarize_activity,
)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Cache disque isolé par test + pas de cooldown API."""
    monkeypatch.setattr(gc, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(gc, "API_COOLDOWN_S", 0)


# ---------------------------------------------------------------------------
# Fixtures de données Garmin réalistes (formes validées sur l'API réelle)
# ---------------------------------------------------------------------------

@pytest.fixture
def garmin_activity():
    return {
        "activityId": 23646533940,
        "activityName": "Vitrolles - Base",
        "startTimeLocal": "2026-07-18 21:20:46",
        "activityType": {"typeKey": "running"},
        "eventType": {"typeKey": "uncategorized"},
        "distance": 6254.65,
        "duration": 2762.36,
        "movingDuration": 2760.65,
        "elevationGain": 31.0,
        "averageSpeed": 2.264,
        "averageHR": 142.0,
        "maxHR": 153.0,
        "averageRunningCadenceInStepsPerMinute": 159.625,
        "calories": 450.0,
        "startLatitude": 43.42528,
        "startLongitude": 5.28087,
        "activityTrainingLoad": 44.47,
        "vO2MaxValue": 48.0,
    }


@pytest.fixture
def garmin_raw_details():
    """Réponse minimale de get_activity_details (streams)."""
    keys = [
        "sumDuration", "sumDistance", "directHeartRate", "directElevation",
        "directSpeed", "directDoubleCadence", "directLatitude", "directLongitude",
    ]
    descriptors = [{"key": k, "metricsIndex": i} for i, k in enumerate(keys)]
    rows = []
    for i in range(11):
        rows.append({"metrics": [
            float(i * 60),           # time (s)
            float(i * 250),          # distance (m)
            140.0 + i,               # hr
            50.0 + i,                # altitude
            2.5,                     # speed m/s
            160.0,                   # cadence spm
            43.42 + i * 0.001,       # lat
            5.28 + i * 0.001,        # lon
        ]})
    # Un point sans GPS (tunnel) : latlng doit devenir None, pas [None, None]
    rows[5]["metrics"][6] = None
    rows[5]["metrics"][7] = None
    return {"metricDescriptors": descriptors, "activityDetailMetrics": rows}


@pytest.fixture
def garmin_lap():
    return {
        "lapIndex": 1,
        "distance": 1000.0,
        "duration": 444.527,
        "averageSpeed": 2.25,
        "averageHR": 138.0,
        "averageRunCadence": 156.75,
        "elevationGain": 12.0,
    }


class FakeApi:
    """Stub de l'objet Garmin : compte les appels, renvoie des données fixes."""

    def __init__(self, activities=None):
        self.activities = activities or []
        self.calls = 0

    def get_activities(self, start=0, limit=20):
        self.calls += 1
        return self.activities[start:start + limit]


# ---------------------------------------------------------------------------
# activity_row / summarize_activity
# ---------------------------------------------------------------------------

class TestActivityRow:
    def test_columns_and_values(self, garmin_activity):
        row = activity_row(garmin_activity)
        assert row["activityId"] == 23646533940
        assert row["activityType"] == "running"
        assert row["distance_km"] == 6.25
        assert row["duration_min"] == 46.0  # movingDuration prioritaire
        assert row["avgHR"] == 142.0
        assert row["calories"] == 450
        assert row["startLat"] == pytest.approx(43.42528)
        assert row["workoutType"] == "uncategorized"
        assert row["vo2max"] == 48.0

    def test_cadence_not_doubled(self, garmin_activity):
        """Garmin envoie déjà des pas/min (contrairement aux RPM Strava)."""
        row = activity_row(garmin_activity)
        assert row["avgCadence"] == pytest.approx(159.625)

    def test_pace_from_speed(self, garmin_activity):
        row = activity_row(garmin_activity)
        assert row["avgPace_sec"] == pytest.approx(1000 / 2.264)
        assert row["avgPace"].endswith("/km")

    def test_unknown_type_passthrough(self, garmin_activity):
        garmin_activity["activityType"] = {"typeKey": "windsurfing_v2"}
        assert activity_row(garmin_activity)["activityType"] == "windsurfing_v2"

    def test_missing_fields(self):
        row = activity_row({"activityId": 1})
        assert row["distance_km"] == 0
        assert row["avgPace"] == "—"
        assert row["calories"] is None
        assert row["avgCadence"] is None


class TestSummarizeActivity:
    def test_summary(self):
        summary = summarize_activity({
            "distance": 10000.0, "movingDuration": 3000.0,
            "averageSpeed": 10000 / 3000, "averageHR": 150.0, "maxHR": 175.0,
            "calories": 700.0, "elevationGain": 120.0,
        })
        assert summary["distance_km"] == 10.0
        assert summary["duration_min"] == 50.0
        assert summary["avgPace"] == "5:00/km"
        assert summary["maxHR"] == 175.0

    def test_empty(self):
        summary = summarize_activity({})
        assert summary["distance_km"] == 0
        assert summary["avgPace"] == "—"


# ---------------------------------------------------------------------------
# build_streams
# ---------------------------------------------------------------------------

class TestBuildStreams:
    def test_keys(self, garmin_raw_details):
        streams = build_streams(garmin_raw_details)
        for key in ("time", "distance", "heartrate", "altitude",
                    "velocity_smooth", "cadence", "latlng", "grade_smooth"):
            assert key in streams, key

    def test_alignment(self, garmin_raw_details):
        streams = build_streams(garmin_raw_details)
        n = len(streams["distance"])
        assert all(len(v) == n for v in streams.values())

    def test_latlng_pairs(self, garmin_raw_details):
        streams = build_streams(garmin_raw_details)
        assert streams["latlng"][0] == [pytest.approx(43.42), pytest.approx(5.28)]
        assert streams["latlng"][5] is None  # point GPS manquant

    def test_empty_input(self):
        assert build_streams({}) == {}
        assert build_streams({"metricDescriptors": [], "activityDetailMetrics": []}) == {}

    def test_missing_optional_stream(self, garmin_raw_details):
        # Sans FC : le stream heartrate doit être absent, pas rempli de None
        garmin_raw_details["metricDescriptors"] = [
            d for d in garmin_raw_details["metricDescriptors"]
            if d["key"] != "directHeartRate"
        ]
        streams = build_streams(garmin_raw_details)
        assert "heartrate" not in streams
        assert "distance" in streams


class TestComputeGradeStream:
    def test_flat(self):
        grade = compute_grade_stream([0, 100, 200, 300], [50, 50, 50, 50])
        assert all(g == 0 for g in grade)

    def test_constant_slope(self):
        dists = [i * 100.0 for i in range(20)]
        alts = [i * 10.0 for i in range(20)]  # 10 m / 100 m = 10 %
        grade = compute_grade_stream(dists, alts)
        assert grade[10] == pytest.approx(10.0, abs=0.5)

    def test_mismatched_lengths(self):
        assert compute_grade_stream([0, 100], [50]) == []
        assert compute_grade_stream([], []) == []


# ---------------------------------------------------------------------------
# compute_km_splits
# ---------------------------------------------------------------------------

class TestComputeKmSplits:
    def _streams(self, km: float, pace_sec: float = 300.0, step_m: float = 50.0):
        """Streams synthétiques à allure constante."""
        n = int(km * 1000 / step_m) + 1
        return {
            "distance": [i * step_m for i in range(n)],
            "time": [i * step_m * pace_sec / 1000 for i in range(n)],
            "heartrate": [150.0] * n,
            "altitude": [100.0 + i * 0.1 for i in range(n)],
        }

    def test_split_count_and_pace(self):
        splits = compute_km_splits(self._streams(3.0))
        assert len(splits) == 3
        for s in splits:
            assert s["pace_sec"] == pytest.approx(300.0, rel=0.02)
            assert s["pace"].endswith("/km")
            assert s["avg_hr"] == 150.0

    def test_partial_last_km_kept(self):
        # 2.5 km → 3e split partiel (500 m) conservé avec la bonne allure
        splits = compute_km_splits(self._streams(2.5))
        assert len(splits) == 3
        assert splits[2]["distance_m"] == pytest.approx(500.0, abs=60)
        assert splits[2]["pace_sec"] == pytest.approx(300.0, rel=0.05)

    def test_tiny_leftover_ignored(self):
        # 2.02 km → le résidu de 20 m ne crée pas de split
        splits = compute_km_splits(self._streams(2.02, step_m=10.0))
        assert len(splits) == 2

    def test_elev_diff(self):
        splits = compute_km_splits(self._streams(2.0))
        # +0.1 m par pas de 50 m → +2 m par km
        assert splits[0]["elev_diff"] == pytest.approx(2.0, abs=0.3)

    def test_empty_or_short(self):
        assert compute_km_splits({}) == []
        assert compute_km_splits({"distance": [0], "time": [0]}) == []


# ---------------------------------------------------------------------------
# laps_to_splits
# ---------------------------------------------------------------------------

class TestLapsToSplits:
    def test_basic(self, garmin_lap):
        splits = laps_to_splits([garmin_lap])
        s = splits[0]
        assert s["lap"] == 1
        assert s["distance_km"] == 1.0
        assert s["duration_min"] == pytest.approx(7.41, abs=0.01)
        assert s["pace_sec"] == pytest.approx(1000 / 2.25)
        assert s["avgCadence"] == pytest.approx(156.75)  # spm, sans doublement

    def test_empty(self):
        assert laps_to_splits([]) == []
        assert laps_to_splits(None) == []


# ---------------------------------------------------------------------------
# Cache disque
# ---------------------------------------------------------------------------

class TestDiskCache:
    def test_roundtrip(self):
        gc._cache_set(1, "clef", {"a": 1})
        assert gc._cache_get(1, "clef") == {"a": 1}

    def test_isolation_par_athlete(self):
        gc._cache_set(1, "clef", "athlete1")
        assert gc._cache_get(2, "clef") is None

    def test_expiration(self, monkeypatch):
        gc._cache_set(1, "clef", "valeur")
        monkeypatch.setattr(gc, "CACHE_TTL", 0)
        time.sleep(0.01)
        assert gc._cache_get(1, "clef") is None

    def test_invalidate(self, garmin_activity):
        client = GarminClient(api=FakeApi(), athlete_id=1)
        gc._cache_set(1, "clef", "valeur")
        client.invalidate_cache()
        assert gc._cache_get(1, "clef") is None

    def test_invalidate_spares_streams_unless_asked(self):
        client = GarminClient(api=FakeApi(), athlete_id=1)
        gc._cache_set(1, "s", {"time": [0]}, bucket=gc.STREAMS_BUCKET)
        client.invalidate_cache()
        assert gc._cache_get(1, "s", bucket=gc.STREAMS_BUCKET) == {"time": [0]}
        client.invalidate_cache(include_streams=True)
        assert gc._cache_get(1, "s", bucket=gc.STREAMS_BUCKET) is None

    def test_custom_ttl(self, monkeypatch):
        gc._cache_set(1, "clef", "valeur")
        monkeypatch.setattr(gc, "CACHE_TTL", 0)
        assert gc._cache_get(1, "clef", ttl=3600) == "valeur"

    def test_atomic_write_leaves_no_tmp(self):
        gc._cache_set(1, "clef", {"a": 1})
        folder = gc._cache_path(1, "clef").parent
        assert not list(folder.glob("*.tmp"))

    def test_failed_write_keeps_previous_value(self, monkeypatch):
        """Une écriture qui échoue en plein dump ne corrompt pas l'ancien fichier."""
        gc._cache_set(1, "clef", {"v": 1})

        def broken_dump(obj, f, **kw):
            f.write('{"timestamp": 1, "da')
            raise OSError("disque plein")

        with monkeypatch.context() as m:
            m.setattr(gc.json, "dump", broken_dump)
            gc._cache_set(1, "clef", {"v": 2})
        assert gc._cache_get(1, "clef") == {"v": 1}
        assert not list(gc._cache_path(1, "clef").parent.glob("*.tmp"))

    def test_invalidate_sweeps_expired_streams(self, monkeypatch):
        client = GarminClient(api=FakeApi(), athlete_id=1)
        gc._cache_set(1, "vieux", {"time": [0]}, bucket=gc.STREAMS_BUCKET)
        folder = gc._cache_path(1, "vieux", bucket=gc.STREAMS_BUCKET).parent
        (folder / "orphelin.123.tmp").write_text("x")
        monkeypatch.setattr(gc, "STREAMS_TTL", -1)
        client.invalidate_cache()
        assert list(folder.iterdir()) == []


class DetailsApi(FakeApi):
    def __init__(self, details=None, error=None):
        super().__init__()
        self.details, self.error, self.detail_calls = details, error, 0

    def get_activity_details(self, activity_id, maxchart=2000, maxpoly=4000):
        self.detail_calls += 1
        if self.error:
            raise self.error
        return self.details


class TestGetStreams:
    def test_cached_in_streams_bucket(self, garmin_raw_details, monkeypatch):
        monkeypatch.setattr(gc, "API_COOLDOWN_S", 0)
        api = DetailsApi(garmin_raw_details)
        client = GarminClient(api=api, athlete_id=1)
        first = client.get_streams(7)
        client.invalidate_cache()  # « Actualiser » ne doit pas re-télécharger
        assert client.get_streams(7) == first
        assert api.detail_calls == 1

    def test_empty_streams_not_cached(self, monkeypatch):
        monkeypatch.setattr(gc, "API_COOLDOWN_S", 0)
        api = DetailsApi({"metricDescriptors": [], "activityDetailMetrics": []})
        client = GarminClient(api=api, athlete_id=1)
        assert client.get_streams(7) == {}
        client.get_streams(7)
        assert api.detail_calls == 2

    def test_strict_raises_on_api_error(self):
        client = GarminClient(api=DetailsApi(error=RuntimeError("429")), athlete_id=1)
        with pytest.raises(RuntimeError):
            client.get_streams(7, strict=True)

    def test_unparsable_details_do_not_raise(self, monkeypatch):
        monkeypatch.setattr(gc, "API_COOLDOWN_S", 0)
        client = GarminClient(api=DetailsApi({"metricDescriptors": "oups",
                                              "activityDetailMetrics": [1]}), athlete_id=1)
        assert client.get_streams(7, strict=True) == {}

    def test_lenient_returns_empty_on_api_error(self, monkeypatch):
        monkeypatch.setattr(gc.time, "sleep", lambda s: None)
        client = GarminClient(api=DetailsApi(error=RuntimeError("boom")), athlete_id=1)
        assert client.get_streams(7) == {}


# ---------------------------------------------------------------------------
# GarminClient.get_activities
# ---------------------------------------------------------------------------

class TestGetActivities:
    def test_dataframe_contract(self, garmin_activity):
        client = GarminClient(api=FakeApi([garmin_activity]), athlete_id=1)
        df = client.get_activities(limit=10)
        expected = {
            "activityId", "startTimeLocal", "activityName", "activityType",
            "distance_km", "duration_min", "avgPace", "avgPace_sec",
            "avgHR", "maxHR", "avgCadence", "calories", "elevationGain",
            "avgSpeed_ms", "startLat", "startLon", "workoutType",
            "trainingLoad", "vo2max",
        }
        # Contrat complet (19 colonnes, cf. CLAUDE.md) : trainingLoad porte le
        # PMC du sport croisé, son absence passerait inaperçue sans ce test.
        assert set(df.columns) == expected
        assert df["trainingLoad"].iloc[0] == garmin_activity.get("activityTrainingLoad")
        assert pd.api.types.is_datetime64_any_dtype(df["startTimeLocal"])

    def test_second_call_hits_cache(self, garmin_activity):
        api = FakeApi([garmin_activity])
        client = GarminClient(api=api, athlete_id=1)
        client.get_activities(limit=10)
        client.get_activities(limit=10)
        assert api.calls == 1

    def test_limit_respected(self, garmin_activity):
        acts = [dict(garmin_activity, activityId=i) for i in range(30)]
        client = GarminClient(api=FakeApi(acts), athlete_id=1)
        df = client.get_activities(limit=5)
        assert len(df) == 5

    def test_empty(self):
        client = GarminClient(api=FakeApi([]), athlete_id=1)
        assert client.get_activities(limit=10).empty


# ---------------------------------------------------------------------------
# safe_load_activities — traduction des erreurs
# ---------------------------------------------------------------------------

class _RaisingClient:
    def __init__(self, exc):
        self._exc = exc

    def get_activities(self, limit):
        raise self._exc


class TestSafeLoadActivities:
    def test_success(self, garmin_activity):
        client = GarminClient(api=FakeApi([garmin_activity]), athlete_id=1)
        df, err = safe_load_activities(client, 10)
        assert err is None
        assert len(df) == 1

    @pytest.mark.parametrize("exc,fragment", [
        (GarminConnectAuthenticationError("boom"), "Reconnecte-toi"),
        (GarminConnectTooManyRequestsError("429"), "429"),
        (GarminConnectConnectionError("réseau"), "réseau"),
        (RuntimeError("autre"), "inattendue"),
    ])
    def test_errors(self, exc, fragment):
        df, err = safe_load_activities(_RaisingClient(exc), 10)
        assert df.empty
        assert fragment in err


# ---------------------------------------------------------------------------
# Séries quotidiennes par plage (page Comparatif annuel)
# ---------------------------------------------------------------------------

class TestDateWindows:
    def test_une_seule_fenetre_si_dans_la_limite(self):
        assert date_windows("2026-01-01", "2026-01-28", 28) == [
            ("2026-01-01", "2026-01-28")
        ]

    def test_decoupe_en_fenetres_contigues_et_sans_recouvrement(self):
        windows = date_windows("2026-01-01", "2026-03-01", 28)
        assert windows == [
            ("2026-01-01", "2026-01-28"),
            ("2026-01-29", "2026-02-25"),
            ("2026-02-26", "2026-03-01"),
        ]

    def test_borne_de_fin_incluse(self):
        assert date_windows("2026-05-10", "2026-05-10", 28) == [
            ("2026-05-10", "2026-05-10")
        ]

    def test_annee_complete_en_un_appel_quand_la_fenetre_le_permet(self):
        assert date_windows("2025-01-01", "2025-12-31", 365) == [
            ("2025-01-01", "2025-12-31")
        ]

    def test_intervalle_inverse_ou_fenetre_invalide(self):
        assert date_windows("2026-05-10", "2026-05-01", 28) == []
        assert date_windows("2026-05-01", "2026-05-10", 0) == []


class TestRangeExtractors:
    def test_sleep_remonte_le_score_global(self):
        raw = [{
            "calendarDate": "2026-07-01",
            "sleepTimeSeconds": 29880,
            "sleepScores": {"overall": {"value": 94, "qualifierKey": "EXCELLENT"}},
        }]
        rows = extract_sleep_days(raw)
        assert rows[0]["sleepScoreValue"] == 94
        assert rows[0]["sleepTimeSeconds"] == 29880

    def test_sleep_sans_scores(self):
        rows = extract_sleep_days([{"calendarDate": "2026-07-01"}])
        assert rows[0]["sleepScoreValue"] is None

    def test_hrv_deballe_hrv_summaries(self):
        raw = {"hrvSummaries": [
            {"calendarDate": "2026-08-09", "lastNightAvg": 61, "status": "BALANCED"},
            {"pasDeDate": True},
        ], "userProfilePk": 1}
        rows = extract_hrv_days(raw)
        assert len(rows) == 1
        assert rows[0]["lastNightAvg"] == 61

    def test_vo2max_deballe_generic(self):
        raw = [
            {"userId": 1, "generic": {"calendarDate": "2026-08-09", "vo2MaxPreciseValue": 48.6}},
            {"userId": 1, "generic": None},
        ]
        rows = extract_vo2max_days(raw)
        assert rows == [{"calendarDate": "2026-08-09", "vo2MaxPreciseValue": 48.6}]

    def test_resting_hr_remonte_values(self):
        raw = [{"calendarDate": "2026-08-10", "values": {"restingHR": 52, "wellnessMaxAvgHR": 149}}]
        rows = extract_resting_hr_days(raw)
        assert rows[0] == {
            "calendarDate": "2026-08-10", "restingHR": 52, "wellnessMaxAvgHR": 149,
        }

    def test_resting_hr_sans_values(self):
        rows = extract_resting_hr_days([{"calendarDate": "2026-08-10"}])
        assert rows == [{"calendarDate": "2026-08-10"}]

    @pytest.mark.parametrize("extractor", [
        extract_sleep_days, extract_hrv_days,
        extract_vo2max_days, extract_resting_hr_days,
    ])
    def test_reponses_vides_ou_inattendues(self, extractor):
        assert extractor([]) == []
        assert extractor({}) == []
        assert extractor(None) == []


class _RangeApi:
    """Stub connectapi : enregistre les chemins appelés et sert des jours vides."""

    def __init__(self, payload=None, fail_on=None):
        self.paths = []
        self.payload = payload
        self.fail_on = fail_on or set()

    def connectapi(self, path, params=None):
        self.paths.append((path, params))
        if path in self.fail_on:
            raise RuntimeError("400 range too big")
        return self.payload if self.payload is not None else []


class TestRangeMethods:
    def test_sleep_decoupe_en_fenetres_de_28_jours(self):
        api = _RangeApi()
        client = GarminClient(api=api, athlete_id=1)
        client.get_sleep_range("2026-01-01", "2026-03-01")
        assert len(api.paths) == 3
        assert api.paths[0][1]["startDate"] == "2026-01-01"
        assert api.paths[0][1]["endDate"] == "2026-01-28"

    def test_hrv_une_annee_en_un_appel(self):
        api = _RangeApi({"hrvSummaries": [
            {"calendarDate": "2025-06-01", "lastNightAvg": 55}
        ]})
        client = GarminClient(api=api, athlete_id=1)
        rows = client.get_hrv_range("2025-01-01", "2025-12-31")
        assert len(api.paths) == 1
        assert api.paths[0][0] == "/hrv-service/hrv/daily/2025-01-01/2025-12-31"
        assert rows[0]["lastNightAvg"] == 55

    def test_vo2max_et_fc_repos_utilisent_leurs_endpoints(self):
        api = _RangeApi()
        client = GarminClient(api=api, athlete_id=1)
        client.get_vo2max_range("2026-01-01", "2026-01-05")
        client.get_resting_hr_range("2026-01-01", "2026-01-05")
        assert api.paths[0][0] == "/metrics-service/metrics/maxmet/daily/2026-01-01/2026-01-05"
        assert api.paths[1][0] == "/usersummary-service/stats/heartRate/daily/2026-01-01/2026-01-05"

    def test_deuxieme_appel_servi_par_le_cache_disque(self):
        api = _RangeApi({"hrvSummaries": [{"calendarDate": "2025-06-01", "lastNightAvg": 55}]})
        client = GarminClient(api=api, athlete_id=1)
        client.get_hrv_range("2025-01-01", "2025-12-31")
        rows = client.get_hrv_range("2025-01-01", "2025-12-31")
        assert len(api.paths) == 1  # aucun appel réseau supplémentaire
        assert rows[0]["lastNightAvg"] == 55

    def test_une_fenetre_en_echec_ne_perd_pas_les_autres(self):
        payload = [{"calendarDate": "2026-01-01", "restingHR": 52}]
        api = _RangeApi(
            payload,
            fail_on={"/usersummary-service/stats/heartRate/daily/2026-01-01/2026-01-28"},
        )
        client = GarminClient(api=api, athlete_id=1)
        rows = client.get_resting_hr_range("2026-01-01", "2026-02-10")
        assert len(api.paths) == 2      # les deux fenêtres ont été tentées
        assert len(rows) == 1           # seule la seconde a produit des lignes


# ---------------------------------------------------------------------------
# Écriture : push / retrait de séances (calendrier Garmin)
# ---------------------------------------------------------------------------

class WriteApi(FakeApi):
    def __init__(self, schedule_error=None, delete_error=None, library=None):
        super().__init__()
        self.schedule_error, self.delete_error = schedule_error, delete_error
        self.library = dict(library or {})
        self.deleted, self.unscheduled = [], []

    def upload_workout(self, payload):
        wid = 100 + len(self.library)
        self.library[wid] = payload
        return {"workoutId": wid}

    def schedule_workout(self, workout_id, date_str):
        if self.schedule_error:
            raise self.schedule_error
        return {"workoutScheduleId": 900 + workout_id}

    def unschedule_workout(self, schedule_id):
        self.unscheduled.append(schedule_id)

    def delete_workout(self, workout_id):
        if self.delete_error:
            raise self.delete_error
        self.deleted.append(workout_id)
        self.library.pop(workout_id, None)

    def get_workout_by_id(self, workout_id):
        if workout_id not in self.library:
            raise RuntimeError("API Error 404 - Not Found")
        return self.library[workout_id]

    def get_workouts(self, start=0, limit=100):
        items = [{"workoutId": k, **v} for k, v in sorted(self.library.items())]
        return items[start:start + limit]


class TestPushWorkout:
    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr(gc, "API_COOLDOWN_S", 0)

    def test_push_returns_ids(self):
        client = GarminClient(api=WriteApi(), athlete_id=1)
        ids = client.push_workout({"workoutName": "x [GD-a-]"}, "2026-10-01")
        assert ids == {"workout_id": 100, "schedule_id": 1000}

    def test_failed_schedule_rolls_back(self):
        api = WriteApi(schedule_error=RuntimeError("API Error 429"))
        client = GarminClient(api=api, athlete_id=1)
        with pytest.raises(RuntimeError):
            client.push_workout({"workoutName": "x"}, "2026-10-01")
        assert api.library == {} and api.deleted == [100]

    def test_remove_tolerates_real_404_only(self):
        api = WriteApi(library={5: {"workoutName": "a [GD-p-]"}},
                       delete_error=RuntimeError("API Error 404 - gone"))
        assert GarminClient(api=api, athlete_id=1).remove_workout(5, 9) is True
        api.delete_error = RuntimeError("API Error 500 - /workout/9404 failed")
        with pytest.raises(RuntimeError):
            GarminClient(api=api, athlete_id=1).remove_workout(5, 9)

    def test_remove_checks_tag(self):
        api = WriteApi(library={5: {"workoutName": "Séance du club"}})
        client = GarminClient(api=api, athlete_id=1)
        assert client.remove_workout(5, 9, required_tag="[GD-p-") is False
        assert api.deleted == [] and api.unscheduled == []

    def test_remove_already_deleted_is_ok(self):
        api = WriteApi()
        assert GarminClient(api=api, athlete_id=1).remove_workout(5, None, required_tag="[GD-") is True

    def test_list_workouts_paginates(self):
        api = WriteApi(library={i: {"workoutName": f"w{i}"} for i in range(230)})
        assert len(GarminClient(api=api, athlete_id=1).list_workouts(page_size=100)) == 230


@pytest.mark.parametrize("exc,status", [
    (RuntimeError("API Error 404 - Not Found"), 404),
    (RuntimeError("HTTP 503 Service Unavailable"), 503),
    (RuntimeError("500 error at /workout/9404"), None),
])
def test_http_status(exc, status):
    assert gc._http_status(exc) == status


def test_http_status_from_response_attribute():
    class Resp:
        status_code = 404
    err = RuntimeError("boom")
    err.response = Resp()
    assert gc._http_status(err) == 404


def test_training_plans_strict_bypasses_cache():
    class PlansApi(FakeApi):
        plans = {"trainingPlanList": []}

        def get_training_plans(self):
            self.calls += 1
            return self.plans
    api = PlansApi()
    client = GarminClient(api=api, athlete_id=1)
    client.get_training_plans()
    client.get_training_plans()
    assert api.calls == 1
    client.get_training_plans(strict=True)
    assert api.calls == 2
