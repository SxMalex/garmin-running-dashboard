"""
Tests du client Garmin : transformations pures (streams, splits, laps,
lignes d'activité), cache disque et mapping d'erreurs.
Aucun appel réseau — l'API Garmin est systématiquement stubée.
"""

import json
import time

import pandas as pd
import pytest
from pathlib import Path
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
        "activityId": 10000000001,
        "activityName": "Sortie test",
        "startTimeLocal": "2026-01-15 08:00:00",
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
        "startLatitude": 43.6000,
        "startLongitude": 1.4400,
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
        assert row["activityId"] == 10000000001
        assert row["activityType"] == "running"
        assert row["distance_km"] == 6.25
        assert row["duration_min"] == 46.0  # movingDuration prioritaire
        assert row["avgHR"] == 142.0
        assert row["calories"] == 450
        assert row["startLat"] == pytest.approx(43.6000)
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

    def test_cooldown_applied_between_each_real_page(self, garmin_activity, monkeypatch):
        """Un historique > batch_size (100) fait plusieurs appels réels : le
        cooldown doit s'appliquer après chacun, pas une seule fois à la fin
        (sinon la pagination contourne la protection anti-bannissement)."""
        sleeps = []
        monkeypatch.setattr(gc.time, "sleep", lambda s: sleeps.append(s))
        acts = [dict(garmin_activity, activityId=i) for i in range(250)]
        api = FakeApi(acts)
        client = GarminClient(api=api, athlete_id=1)
        client.get_activities(limit=250)
        # 250 activités / batch_size 100 -> 3 pages réelles (100, 100, 50).
        assert api.calls == 3
        assert len(sleeps) == api.calls


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

    @pytest.mark.parametrize("exc", [GarminConnectConnectionError("![](https://tiers.example/p.png)"),
                                     RuntimeError("![](https://tiers.example/p.png)")])
    def test_server_text_is_escaped(self, exc):
        """Revue #1 : le message est rendu en Markdown par toutes les pages."""
        _, err = safe_load_activities(_RaisingClient(exc), 10)
        assert "![](" not in err and "tiers" in err


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
    def __init__(self, schedule_error=None, delete_error=None, library=None, schedules=None):
        super().__init__()
        self.schedule_error, self.delete_error = schedule_error, delete_error
        self.library = dict(library or {})
        # {schedule_id: {"workoutId": ..., "workoutName": ...}} — forme de
        # get_scheduled_workout_by_id, utilisée pour vérifier l'appartenance
        # du schedule avant un unschedule (cf. remove_workout).
        self.schedules = dict(schedules or {})
        self.deleted, self.unscheduled = [], []

    def upload_workout(self, payload):
        wid = 100 + len(self.library)
        self.library[wid] = payload
        return {"workoutId": wid}

    def schedule_workout(self, workout_id, date_str):
        if self.schedule_error:
            raise self.schedule_error
        sid = 900 + workout_id
        self.schedules[sid] = {"workoutId": workout_id}
        return {"workoutScheduleId": sid}

    def unschedule_workout(self, schedule_id):
        self.unscheduled.append(schedule_id)
        self.schedules.pop(schedule_id, None)

    def delete_workout(self, workout_id):
        if self.delete_error:
            raise self.delete_error
        self.deleted.append(workout_id)
        self.library.pop(workout_id, None)

    def get_workout_by_id(self, workout_id):
        if workout_id not in self.library:
            raise RuntimeError("API Error 404 - Not Found")
        return self.library[workout_id]

    def get_scheduled_workout_by_id(self, schedule_id):
        if schedule_id not in self.schedules:
            raise RuntimeError("API Error 404 - Not Found")
        return self.schedules[schedule_id]

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
                       schedules={9: {"workoutId": 5}},
                       delete_error=RuntimeError("API Error 404 - gone"))
        assert GarminClient(api=api, athlete_id=1).remove_workout(5, 9) is True
        assert api.unscheduled == [9]
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

    def test_remove_refuses_schedule_owned_by_another_workout(self):
        """Journal corrompu : schedule_id pointe vers un AUTRE workout —
        refus, ni unschedule ni delete ne sont appelés."""
        api = WriteApi(library={5: {"workoutName": "a [GD-p-]"}},
                       schedules={9: {"workoutId": 999, "workoutName": "Séance du club"}})
        client = GarminClient(api=api, athlete_id=1)
        assert client.remove_workout(5, 9, required_tag="[GD-p-") is False
        assert api.unscheduled == [] and api.deleted == []

    def test_remove_refuses_mismatched_schedule_even_without_tag(self):
        """La vérification d'appartenance du schedule s'applique même sans
        `required_tag` (garde-fou général contre un journal corrompu)."""
        api = WriteApi(library={5: {"workoutName": "a"}}, schedules={9: {"workoutId": 999}})
        client = GarminClient(api=api, athlete_id=1)
        assert client.remove_workout(5, 9) is False
        assert api.unscheduled == [] and api.deleted == []

    def test_remove_404_workout_checks_tag_via_schedule(self):
        """Workout déjà supprimé (404) : seule la lecture du schedule permet
        encore de vérifier l'étiquette avant de déplanifier."""
        api = WriteApi(schedules={9: {"workoutId": 5, "workoutName": "Séance du club"}})
        client = GarminClient(api=api, athlete_id=1)
        assert client.remove_workout(5, 9, required_tag="[GD-p-") is False
        assert api.unscheduled == [] and api.deleted == []

    def test_remove_404_workout_allows_when_schedule_carries_tag(self):
        api = WriteApi(schedules={9: {"workoutId": 5, "workoutName": "x [GD-p-]"}})
        client = GarminClient(api=api, athlete_id=1)
        assert client.remove_workout(5, 9, required_tag="[GD-p-") is True
        assert api.unscheduled == [9]

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


# ---------------------------------------------------------------------------
# Session partagée, déconnexion, identifiant d'athlète (revue PR #1)
# ---------------------------------------------------------------------------
class _StubClient:
    """Imite garminconnect 0.3.6 : le rafraîchissement réécrit le tokenstore mémorisé."""

    def __init__(self, store, profile=None, fail=0):
        self._tokenstore_path = str(store)
        self.di_token, self.di_refresh_token, self.jwt_web = "tok", "refresh", None
        self.profile, self.fail, self.profile_calls = profile or {"profileId": 777}, fail, 0
        self.active = self.max_active = 0

    def connectapi(self, path, **kwargs):
        self.profile_calls += 1
        if self.fail:
            self.fail -= 1
            raise RuntimeError("API Error 429")
        return self.profile

    def _refresh_session(self):
        import time as _t
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        _t.sleep(0.01)
        if self._tokenstore_path:
            Path(self._tokenstore_path).mkdir(parents=True, exist_ok=True)
            (Path(self._tokenstore_path) / "garmin_tokens.json").write_text("{}")
        self.active -= 1


class _StubApi:
    def __init__(self, client, display_name="uuid-1"):
        self.client, self.display_name = client, display_name


@pytest.fixture
def session_env(tmp_path, monkeypatch):
    import garmin_client as gcm
    store = tmp_path / "tokens"
    monkeypatch.setenv("GARMIN_TOKENSTORE", str(store))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(gcm, "ATHLETE_ID_RETRY_S", 0)
    gcm.reset_session_state()
    yield gcm, store
    gcm.reset_session_state()


def test_logout_holds_even_if_another_tab_refreshes_afterwards(session_env):
    """Revue #1 : un onglet resté ouvert recréait garmin_tokens.json après la déconnexion."""
    gcm, store = session_env
    api = _StubApi(_StubClient(store))
    gcm.adopt_session(api)
    api.client._refresh_session()
    assert (store / "garmin_tokens.json").exists()
    gcm.end_session()
    assert not store.exists()
    api.client._refresh_session()            # l'onglet de l'autre appareil rafraîchit…
    assert not store.exists()                # …et ne recrée rien
    assert api.client.di_refresh_token is None
    assert gcm.shared_athlete_id() == (0, False)


def test_tabs_share_one_session_and_refresh_one_at_a_time(session_env, monkeypatch):
    import threading
    gcm, store = session_env
    resumed = []
    api = _StubApi(_StubClient(store))
    monkeypatch.setattr(gcm, "resume_session", lambda: resumed.append(1) or api)
    assert gcm.shared_session() is api and gcm.shared_session() is api
    assert resumed == [1]                     # une reprise pour tout le process
    threads = [threading.Thread(target=api.client._refresh_session) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert api.client.max_active == 1         # jamais deux rafraîchissements du même jeton


def test_new_login_neutralizes_the_previous_session(session_env):
    gcm, store = session_env
    old, new = _StubApi(_StubClient(store)), _StubApi(_StubClient(store))
    gcm.adopt_session(old)
    gcm.adopt_session(new)
    assert old.client._tokenstore_path is None and new.client._tokenstore_path
    assert gcm.shared_session() is new


def test_athlete_id_survives_a_transient_failure(session_env):
    """Revue #1 : un 429 sur socialProfile rangeait l'objectif sous un autre id."""
    gcm, store = session_env
    assert gcm.resolve_athlete_id(_StubApi(_StubClient(store, fail=2))) == (777, True)   # retry
    # Garmin muet, mais l'id de ce compte est connu d'une connexion précédente
    assert gcm.resolve_athlete_id(_StubApi(_StubClient(store, fail=9))) == (777, True)
    # Jamais résolu pour ce compte : repli sur le hash, marqué NON fiable
    athlete_id, reliable = gcm.resolve_athlete_id(_StubApi(_StubClient(store, fail=9), "uuid-2"))
    assert athlete_id not in (0, 777) and reliable is False


# --- Matrice de cas limites de la session partagée (contre-revue B1-B5) -------
class _SlowProfileClient(_StubClient):
    def __init__(self, store, delay, fail=0):
        super().__init__(store, fail=fail)
        self.delay = delay

    def connectapi(self, path, **kwargs):
        import time as _t
        _t.sleep(self.delay)
        return super().connectapi(path, **kwargs)


def test_an_existing_session_never_waits_for_the_network(session_env, monkeypatch):
    """Contre-revue : le recontrôle d'id tenait le verrou global (tous les onglets figés)."""
    import time as _t
    gcm, store = session_env
    monkeypatch.setattr(gcm, "ATHLETE_ID_RECHECK_S", 0)
    api = _StubApi(_SlowProfileClient(store, delay=0.0, fail=99))
    gcm.adopt_session(api)                                   # id de repli : non fiable
    assert gcm.shared_athlete_id()[1] is False
    api.client.delay = 0.5                                    # Garmin lent pendant le recontrôle
    t0 = _t.perf_counter()
    gcm.shared_athlete_id()                                   # lance le recontrôle en arrière-plan
    assert gcm.shared_session() is api
    assert _t.perf_counter() - t0 < 0.2                       # ni l'un ni l'autre n'attend
    worker = gcm._SESSION["recheck"]
    api.client.fail = 0
    worker.join(5)


def test_background_recheck_upgrades_the_id_and_ignores_a_replaced_session(session_env, monkeypatch):
    gcm, store = session_env
    monkeypatch.setattr(gcm, "ATHLETE_ID_RECHECK_S", 0)
    api = _StubApi(_StubClient(store, fail=99), display_name="uuid-9")
    gcm.adopt_session(api)
    assert gcm._SESSION["reliable"] is False                  # id de repli
    api.client.fail = 0
    gcm._SESSION["checked_at"] = -1
    gcm.shared_athlete_id()                                   # déclenche LE recontrôle
    worker = gcm._SESSION["recheck"]
    assert worker is not None
    worker.join(5)                                            # attendre CE fil (pas un précédent)
    assert (gcm._SESSION["athlete_id"], gcm._SESSION["reliable"]) == (777, True)
    # Recontrôle d'une session remplacée entre-temps : son résultat est jeté
    old = _StubApi(_StubClient(store, fail=99), display_name="uuid-10")
    gcm.adopt_session(old)
    gcm.adopt_session(_StubApi(_StubClient(store, profile={"profileId": 555}), display_name="uuid-11"))
    old.client.fail = 0                                     # l'ancien recontrôle aboutit (777)…
    gcm._recheck_athlete_id(old)
    assert gcm.shared_athlete_id() == (555, True)           # …sans écraser la session courante


def test_failed_resume_is_not_replayed_on_every_run(session_env, monkeypatch):
    gcm, store = session_env
    calls = []
    monkeypatch.setattr(gcm, "resume_session", lambda: calls.append(1))
    for _ in range(5):
        assert gcm.shared_session() is None
    assert calls == [1]                                       # Garmin injoignable : 1 essai, pas 5
    gcm._SESSION["resume_failed_at"] -= gcm.RESUME_RETRY_S + 1
    gcm.shared_session()
    assert calls == [1, 1]                                    # retenté après le délai


def test_simultaneous_refreshes_hit_garmin_once(session_env):
    """Six onglets à l'expiration : un seul rafraîchissement (les autres trouvent le jeton neuf)."""
    import threading
    gcm, store = session_env

    class Rotating(_StubClient):
        refreshes = 0

        def _refresh_session(self):
            import time as _t
            _t.sleep(0.02)
            Rotating.refreshes += 1
            self.di_token = f"tok{Rotating.refreshes}"

    api = _StubApi(Rotating(store))
    gcm.adopt_session(api)
    barrier = threading.Barrier(6)

    def tab():
        barrier.wait()
        api.client._refresh_session()

    threads = [threading.Thread(target=tab) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert Rotating.refreshes == 1


def test_logout_during_an_inflight_refresh_leaves_no_valid_token(session_env):
    import threading
    gcm, store = session_env

    class Slow(_StubClient):
        def _refresh_session(self):
            import time as _t
            _t.sleep(0.2)
            self.di_token, self.di_refresh_token = "fresh", "fresh-refresh"

    api = _StubApi(Slow(store))
    gcm.adopt_session(api)
    t = threading.Thread(target=api.client._refresh_session)
    t.start()
    gcm.end_session()                                        # pendant le rafraîchissement
    t.join()
    assert api.client.di_token is None and api.client.di_refresh_token is None
    assert api.client._tokenstore_path is None and not store.exists()
    api.client._refresh_session()                            # plus rien ne repart
    assert api.client.di_token is None


def test_corrupt_athlete_ids_file_is_repaired(session_env):
    gcm, store = session_env
    path = gcm._athlete_ids_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"uuid-1": 77')                         # écriture interrompue
    assert gcm.resolve_athlete_id(_StubApi(_StubClient(store))) == (777, True)
    import json as _json
    assert _json.loads(path.read_text()) == {"uuid-1": 777}
    assert not list(path.parent.glob(".athlete_ids.json.*.tmp"))  # pas de fichier temporaire oublié


def test_logout_that_cannot_wait_for_a_slow_refresh_still_wins(session_env, monkeypatch):
    """Rafraîchissement plus long que l'attente de la déconnexion : les jetons revenus sont effacés."""
    import threading
    gcm, store = session_env

    class VerySlow(_StubClient):
        def _refresh_session(self):
            import time as _t
            _t.sleep(0.3)
            self.di_token, self.di_refresh_token = "fresh", "fresh-refresh"

    api = _StubApi(VerySlow(store))
    gcm.adopt_session(api)
    t = threading.Thread(target=api.client._refresh_session)
    t.start()
    import time as _t
    _t.sleep(0.05)                                           # le rafraîchissement tient le verrou
    gcm.end_session()                                        # n'attend que 10 ms
    t.join()
    assert api.client.di_token is None and api.client.di_refresh_token is None


def test_concurrent_first_loads_resume_once(session_env, monkeypatch):
    """Deux onglets ouverts au démarrage : une seule reprise, la même session pour les deux."""
    import threading
    import time as _t
    gcm, store = session_env
    api, calls = _StubApi(_StubClient(store)), []

    def slow_resume():
        calls.append(1)
        _t.sleep(0.2)
        return api

    monkeypatch.setattr(gcm, "resume_session", slow_resume)
    got = []
    threads = [threading.Thread(target=lambda: got.append(gcm.shared_session())) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls == [1] and got == [api, api, api]


def test_session_helpers_tolerate_odd_objects(session_env, monkeypatch):
    gcm, store = session_env
    gcm._neutralize(object())                                # pas de client : rien à faire

    class Frozen:
        __slots__ = ()                                       # setattr impossible
    gcm._wipe_tokens(Frozen())                               # ne lève pas
    api = _StubApi(_StubClient(store))
    gcm.adopt_session(api)
    wrapper = api.client._refresh_session
    gcm.adopt_session(api)                                   # réadoption : pas de double enveloppe
    assert api.client._refresh_session is wrapper


def test_athlete_id_edge_cases(session_env, monkeypatch):
    gcm, store = session_env
    empty = _StubClient(store, profile={"displayName": "x"})    # réponse sans id
    athlete_id, reliable = gcm.resolve_athlete_id(_StubApi(empty, display_name="uuid-e"))
    assert empty.profile_calls == 1 and reliable is False      # pas de nouvel essai inutile
    path = gcm._athlete_ids_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"uuid-z": "pas-un-nombre"}')
    assert gcm._known_athlete_id("uuid-z") is None
    import os
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disque plein")))
    assert gcm.resolve_athlete_id(_StubApi(_StubClient(store), "uuid-w")) == (777, True)  # jamais bloquant


def test_account_without_display_name_is_not_remembered(session_env):
    """Sans display_name, rien à associer : aucun fichier d'ids écrit (et l'id reste fiable)."""
    gcm, store = session_env
    assert gcm.resolve_athlete_id(_StubApi(_StubClient(store), display_name="")) == (777, True)
    assert not gcm._athlete_ids_path().exists()



@pytest.mark.parametrize("written, kept", [("R1", False), ("reconnexion", True)])
def test_refused_resume_removes_the_tokens_its_login_rewrote(session_env, written, kept):
    """Revue #1 : `login()` rafraîchit un jeton proche de l'expiration et réécrit le
    tokenstore ; une déconnexion passée entre-temps refusait la reprise mais laissait
    ce fichier, et le visiteur suivant était reconnecté. Celui d'une reconnexion reste."""
    gcm, store = session_env
    generation = gcm._SESSION["generation"]
    gcm.end_session()                                       # déconnexion pendant la reprise
    client = _StubClient(store)
    client.di_refresh_token = "R1"                          # jeton neuf du rafraîchissement
    store.mkdir(parents=True, exist_ok=True)
    (store / "garmin_tokens.json").write_text(json.dumps({"di_refresh_token": written}))
    assert gcm.adopt_session(_StubApi(client), generation=generation) is False
    assert (store / "garmin_tokens.json").exists() is kept

# --- Contre-revue 2 : la déconnexion doit gagner contre toute reprise concurrente ---
def test_logout_wins_against_a_resume_started_during_a_slow_refresh(session_env, monkeypatch):
    """Rafraîchissement lent dans l'onglet A, déconnexion dans B, un onglet C relance un run."""
    import threading
    import time as _t
    gcm, store = session_env

    class Slow(_StubClient):
        def _refresh_session(self):
            _t.sleep(0.5)
            self.di_token, self.di_refresh_token = "fresh", "fresh-refresh"
            if self._tokenstore_path:
                Path(self._tokenstore_path).mkdir(parents=True, exist_ok=True)
                (Path(self._tokenstore_path) / "garmin_tokens.json").write_text("{}")

    api = _StubApi(Slow(store))
    gcm.adopt_session(api)
    store.mkdir(parents=True, exist_ok=True)
    (store / "garmin_tokens.json").write_text("{}")
    monkeypatch.setattr(gcm, "resume_session",
                        lambda: _StubApi(_StubClient(store)) if (store / "garmin_tokens.json").exists() else None)
    tab_a = threading.Thread(target=api.client._refresh_session)
    tab_a.start()
    _t.sleep(0.05)
    t0 = _t.perf_counter()
    gcm.end_session()
    assert _t.perf_counter() - t0 < 0.2                     # la déconnexion n'attend pas le rafraîchissement
    tab_c = threading.Thread(target=gcm.shared_session)
    tab_c.start()
    tab_a.join()
    tab_c.join()
    assert gcm._SESSION["api"] is None
    assert not (store / "garmin_tokens.json").exists()


def test_resume_in_flight_when_logging_out_is_discarded(session_env, monkeypatch):
    """Reprise lancée avant la déconnexion, terminée après : elle ne s'installe pas."""
    import threading
    import time as _t
    gcm, store = session_env
    resumed = _StubApi(_StubClient(store))

    def slow_resume():
        _t.sleep(0.3)
        return resumed

    monkeypatch.setattr(gcm, "resume_session", slow_resume)
    tab = threading.Thread(target=gcm.shared_session)
    tab.start()
    _t.sleep(0.05)
    gcm.end_session()
    tab.join()
    assert gcm._SESSION["api"] is None
    assert resumed.client._tokenstore_path is None           # neutralisée, pas abandonnée vivante


def test_explicit_login_after_logout_is_installed(session_env):
    gcm, store = session_env
    gcm.end_session()
    api = _StubApi(_StubClient(store))
    gcm.adopt_session(api)                                   # connexion explicite : légitime
    assert gcm.shared_session() is api


def test_a_finished_recheck_does_not_forget_a_newer_one(session_env):
    import threading
    gcm, store = session_env
    newer = threading.Thread(target=lambda: None)
    gcm._SESSION["recheck"] = newer
    gcm._recheck_athlete_id(_StubApi(_StubClient(store)))   # un ancien fil se termine
    assert gcm._SESSION["recheck"] is newer


# ---------------------------------------------------------------------------
# Revue PR #1, lot G — jetons garth hérités, planification déplacée, cooldown
# ---------------------------------------------------------------------------
class _LegacyGarmin:
    """Imite garminconnect 0.3.6 : ne lit QUE garmin_tokens.json du tokenstore."""

    def __init__(self, email=None, password=None, return_on_mfa=False):
        self.client = self

    def login(self, tokenstore=None):
        if tokenstore is None:                                # connexion par identifiants
            return "ok", None
        if not (Path(tokenstore).expanduser() / "garmin_tokens.json").exists():
            raise gc.GarminConnectConnectionError("Token path not loading cleanly")
        return None, None

    def dump(self, path):
        (Path(path) / "garmin_tokens.json").write_text("{}")


@pytest.fixture
def legacy_store(tmp_path, monkeypatch):
    store = tmp_path / "garmin"
    store.mkdir()
    for name in gc.LEGACY_TOKEN_FILES:
        (store / name).write_text('{"oauth_token_secret": "longue-duree"}')
    monkeypatch.setenv("GARMIN_TOKENSTORE", str(store))
    monkeypatch.setattr(gc, "Garmin", _LegacyGarmin)
    return store


def test_upgrade_with_only_garth_tokens_purges_them_and_asks_to_log_in(legacy_store):
    """Cas réel de la mise à jour : seuls oauth1/oauth2 existent, illisibles par 0.3.6."""
    (legacy_store / "notes.txt").write_text("à garder")
    assert gc.resume_session() is None                        # reconnexion obligatoire
    assert sorted(p.name for p in legacy_store.iterdir()) == ["notes.txt"]


def test_login_after_upgrade_leaves_only_the_new_token_file(legacy_store):
    status, api = gc.login_with_credentials("a@b.c", "pw")
    assert status == "ok" and api is not None
    assert sorted(p.name for p in legacy_store.iterdir()) == ["garmin_tokens.json"]


def test_resume_with_both_formats_keeps_the_session_and_drops_garth(legacy_store):
    (legacy_store / "garmin_tokens.json").write_text("{}")
    assert gc.resume_session() is not None
    assert sorted(p.name for p in legacy_store.iterdir()) == ["garmin_tokens.json"]


@pytest.mark.parametrize("layout", ["absent", "file"])
def test_purge_ignores_a_missing_or_file_tokenstore(tmp_path, monkeypatch, caplog, layout):
    target = tmp_path / ("absent" if layout == "absent" else "garmin_tokens.json")
    if layout == "file":
        target.write_text("{}")
    monkeypatch.setenv("GARMIN_TOKENSTORE", str(target))
    gc._purge_legacy_tokens()
    assert target.exists() is (layout == "file")              # ni créé, ni supprimé
    assert "Jeton hérité" not in caplog.text                  # pas d'alerte à chaque reprise


def test_undeletable_legacy_token_never_blocks_the_resume(legacy_store):
    (legacy_store / "oauth1_token.json").unlink()
    (legacy_store / "oauth1_token.json").mkdir()               # unlink → IsADirectoryError
    (legacy_store / "garmin_tokens.json").write_text("{}")
    assert gc.resume_session() is not None
    assert not (legacy_store / "oauth2_token.json").exists()  # l'autre est bien purgé


class CalendarApi(WriteApi):
    """`get_scheduled_workouts(year, month)` : un calendrier par mois, erreurs par mois."""

    def __init__(self, calendar=None, errors=None, **kw):
        super().__init__(**kw)
        self.calendar, self.errors, self.months, self.scheduled_on = calendar or {}, errors or {}, [], []

    def get_scheduled_workouts(self, year, month):
        self.months.append((year, month))
        if (year, month) in self.errors:
            raise self.errors[(year, month)]
        return self.calendar.get((year, month))

    def schedule_workout(self, workout_id, date_str):
        self.scheduled_on.append(date_str)
        return super().schedule_workout(workout_id, date_str)


def _items(*entries):
    """Forme réelle du calendar-service : items à plat (id = schedule, workoutId, date)."""
    return {"calendarItems": [{"id": sid, "itemType": "workout", "workoutId": wid, "date": day}
                              for sid, wid, day in entries]}


class TestFindSchedule:
    def test_not_moved_costs_one_month_read(self):
        api = CalendarApi({(2026, 9): _items((71, 5, "2026-09-15"))})
        assert GarminClient(api=api, athlete_id=1).ensure_scheduled(5, "2026-09-15") == 71
        assert api.months == [(2026, 9)] and api.scheduled_on == []

    def test_moved_one_day_across_the_month_is_not_rescheduled(self):
        """Cas de la revue : journal perdu, séance du 30/09 décalée au 01/10 dans Garmin."""
        api = CalendarApi({(2026, 9): _items((70, 8, "2026-09-30")),     # autre séance ce jour-là
                           (2026, 10): _items((72, 5, "2026-10-01"))})
        assert GarminClient(api=api, athlete_id=1).ensure_scheduled(5, "2026-09-30") == 72
        assert api.months == [(2026, 9), (2026, 10)] and api.scheduled_on == []

    def test_moved_back_across_the_year(self):
        api = CalendarApi({(2025, 12): _items((73, 5, "2025-12-31"))})
        assert GarminClient(api=api, athlete_id=1).find_schedule(5, "2026-01-01") == 73
        assert api.months == [(2026, 1), (2025, 12)]

    def test_moved_far_within_the_same_month(self):
        api = CalendarApi({(2026, 9): _items((74, 5, "2026-09-28"))})
        assert GarminClient(api=api, athlete_id=1).find_schedule(5, "2026-09-02") == 74

    @pytest.mark.parametrize("day,months", [
        ("2026-09-24", [(2026, 9), (2026, 10)]),   # +7 j = 01/10 : octobre lu
        ("2026-09-23", [(2026, 9)]),               # +7 j = 30/09 : octobre hors fenêtre
        ("2026-10-08", [(2026, 10)]),              # -7 j = 01/10 : septembre hors fenêtre
        ("2026-10-07", [(2026, 10), (2026, 9)]),   # -7 j = 30/09 : septembre lu
    ])
    def test_search_window_bounds(self, day, months):
        api = CalendarApi()
        assert GarminClient(api=api, athlete_id=1).find_schedule(5, day) is None
        assert api.months == months

    def test_duplicate_schedules_the_closest_wins(self):
        api = CalendarApi({(2026, 9): _items((80, 5, "2026-09-02"), (81, 5, "2026-09-16"),
                                             (82, 5, "2026-09-29"))})
        assert GarminClient(api=api, athlete_id=1).find_schedule(5, "2026-09-15") == 81

    def test_entries_without_date_or_id_or_for_another_workout_do_not_count(self):
        cal = {"calendarItems": [
            {"id": 90, "workoutId": 5},                                  # pas de date
            {"id": 91, "workoutId": 5, "date": "bientôt"},              # date illisible
            {"workoutId": 5, "date": "2026-09-15"},                      # pas d'id de planif.
            {"id": 92, "workoutId": 6, "date": "2026-09-15"},            # autre séance
        ]}
        api = CalendarApi({(2026, 9): cal})
        client = GarminClient(api=api, athlete_id=1)
        assert client.ensure_scheduled(5, "2026-09-15") == 905                # créée par schedule_workout
        assert api.scheduled_on == ["2026-09-15"]

    def test_nested_workout_form_is_recognised(self):
        cal = {"calendarItems": [{"workoutScheduleId": 93, "date": "2026-09-16",
                                  "workout": {"workoutId": 5}}]}
        assert GarminClient(api=CalendarApi({(2026, 9): cal}), athlete_id=1).find_schedule(5, "2026-09-15") == 93

    @pytest.mark.parametrize("payload", [None, {}, [], {"calendarItems": None}])
    def test_empty_calendar_schedules_once(self, payload):
        api = CalendarApi({(2026, 9): payload})
        assert GarminClient(api=api, athlete_id=1).ensure_scheduled(5, "2026-09-15") == 905
        assert api.scheduled_on == ["2026-09-15"]

    @pytest.mark.parametrize("status", [429, 500])
    def test_error_on_the_neighbour_month_is_never_read_as_absent(self, status):
        api = CalendarApi(errors={(2026, 10): RuntimeError(f"API Error {status}")})
        with pytest.raises(RuntimeError):
            GarminClient(api=api, athlete_id=1).ensure_scheduled(5, "2026-09-30")
        assert api.scheduled_on == []


class TestWriteCooldown:
    """Chaque appel de séance (lecture comprise) est suivi du cooldown, même en échec."""

    @pytest.fixture
    def events(self, monkeypatch):
        log = []
        monkeypatch.setattr(gc, "API_COOLDOWN_S", 0.25)
        monkeypatch.setattr(gc.time, "sleep", lambda s: log.append(("sleep", s)))
        return log

    @staticmethod
    def _spy(api, log, *names):
        for name in names:
            original = getattr(api, name)

            def call(*a, _o=original, _n=name):
                log.append(("call", _n))
                return _o(*a)
            setattr(api, name, call)
        return api

    @staticmethod
    def _paced(log, n_calls):
        """n appels, chacun immédiatement suivi d'une pause de API_COOLDOWN_S."""
        assert [e[0] for e in log] == ["call", "sleep"] * n_calls
        assert all(e == ("sleep", 0.25) for e in log[1::2])

    WRITE = ("upload_workout", "schedule_workout", "unschedule_workout", "delete_workout",
             "get_workout_by_id", "get_scheduled_workout_by_id", "get_workouts",
             "get_scheduled_workouts")

    def test_remove_paces_its_four_calls(self, events):
        api = self._spy(WriteApi(library={5: {"workoutName": "a [GD-p-]"}},
                                 schedules={9: {"workoutId": 5}}), events, *self.WRITE[:6])
        assert GarminClient(api=api, athlete_id=1).remove_workout(5, 9, required_tag="[GD-p-") is True
        self._paced(events, 4)

    def test_refused_remove_sends_nothing_and_still_pauses(self, events):
        api = self._spy(WriteApi(library={5: {"workoutName": "Séance du club"}}), events, *self.WRITE[:6])
        assert GarminClient(api=api, athlete_id=1).remove_workout(5, 9, required_tag="[GD-p-") is False
        assert events == [("call", "get_workout_by_id"), ("sleep", 0.25)]

    def test_429_in_the_middle_of_a_remove_still_pauses(self, events):
        api = WriteApi(library={5: {"workoutName": "a [GD-p-]"}}, schedules={9: {"workoutId": 5}})

        def refuse(schedule_id):
            raise RuntimeError("API Error 429 - Too Many Requests")
        api.unschedule_workout = refuse
        self._spy(api, events, "get_workout_by_id", "get_scheduled_workout_by_id", "unschedule_workout")
        with pytest.raises(RuntimeError):
            GarminClient(api=api, athlete_id=1).remove_workout(5, 9, required_tag="[GD-p-")
        self._paced(events, 3)
        assert api.deleted == []

    def test_push_and_its_rollback_are_paced(self, events):
        api = self._spy(WriteApi(), events, *self.WRITE[:4])
        GarminClient(api=api, athlete_id=1).push_workout({"workoutName": "x"}, "2026-10-01")
        self._paced(events, 2)
        events.clear()
        api.schedule_error = RuntimeError("API Error 503")
        with pytest.raises(RuntimeError):
            GarminClient(api=api, athlete_id=1).push_workout({"workoutName": "y"}, "2026-10-01")
        self._paced(events, 3)                                       # upload, schedule, delete

    def test_reconciliation_calls_are_paced(self, events):
        api = self._spy(CalendarApi(library={5: {"workoutName": "a [GD-p-]"}}), events, *self.WRITE)
        GarminClient(api=api, athlete_id=1).ensure_scheduled(5, "2026-09-30")
        self._paced(events, 3)                                       # 2 mois lus + planification
        events.clear()
        GarminClient(api=api, athlete_id=1).get_workout(5)
        self._paced(events, 1)

    @pytest.mark.parametrize("n,pages", [(0, 1), (100, 2), (230, 3)])
    def test_list_workouts_paces_every_page(self, events, n, pages):
        api = self._spy(WriteApi(library={i: {"workoutName": f"w{i}"} for i in range(n)}),
                        events, "get_workouts")
        assert len(GarminClient(api=api, athlete_id=1).list_workouts(page_size=100)) == n
        self._paced(events, pages)



def test_mcp_tokenstore_is_purged_of_garth_tokens_too(tmp_path, monkeypatch):
    """Lot G : le tokenstore du MCP gardait le secret OAuth1 longue durée de garth."""
    import garmin_client as gcm
    store = tmp_path / "mcp-tokens"
    store.mkdir()
    for name in ("oauth1_token.json", "oauth2_token.json", "garmin_tokens.json"):
        (store / name).write_text("{}")
    monkeypatch.setenv("GARMIN_TOKENSTORE", str(tmp_path / "dashboard"))   # autre dossier
    gcm._purge_legacy_tokens(str(store))
    assert sorted(p.name for p in store.iterdir()) == ["garmin_tokens.json"]


def test_strict_stream_failure_still_pauses(tmp_path, monkeypatch):
    """Revue : en strict, l'erreur remontait avant toute pause — des refus en rafale."""
    import garmin_client as gcm
    monkeypatch.setattr(gcm, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(gcm, "API_COOLDOWN_S", 0.4)
    sleeps = []
    monkeypatch.setattr(gcm.time, "sleep", lambda s: sleeps.append(s))

    class Api:
        def get_activity_details(self, *a, **k):
            raise RuntimeError("API Error 401 - Unauthorized")

    with pytest.raises(RuntimeError):
        gcm.GarminClient(api=Api(), athlete_id=1).get_streams(5, strict=True)
    assert sleeps == [0.4]


@pytest.mark.parametrize("status,skip", [(400, False), (404, True), (410, True), (401, False), (403, False),
                                         (408, False), (429, False), (500, False), (None, False)])
def test_skippable_activity_error(status, skip):
    import garmin_client as gcm
    exc = RuntimeError(f"API Error {status}") if status else ConnectionError("réseau")
    assert gcm.skippable_activity_error(exc) is skip


def test_training_plans_strict_with_cache(tmp_path, monkeypatch):
    """strict relève l'erreur ; use_cache=True sert quand même une vraie réponse en cache."""
    import garmin_client as gcm
    monkeypatch.setattr(gcm, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(gcm, "API_COOLDOWN_S", 0)

    class Api:
        calls, fail = 0, False
        def get_training_plans(self):
            Api.calls += 1
            if Api.fail:
                raise RuntimeError("API Error 503")
            return {"trainingPlanList": []}

    c = gcm.GarminClient(api=Api(), athlete_id=1)
    assert c.get_training_plans(strict=True, use_cache=True) == {"trainingPlanList": []}
    assert c.get_training_plans(strict=True, use_cache=True) == {"trainingPlanList": []}
    assert Api.calls == 1                                    # servi par le cache


def test_training_plans_guard_is_always_fresh_and_raises(tmp_path, monkeypatch):
    import garmin_client as gcm
    monkeypatch.setattr(gcm, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(gcm, "API_COOLDOWN_S", 0)

    class Api:
        calls = 0
        def get_training_plans(self):
            Api.calls += 1
            if Api.calls > 1:
                raise RuntimeError("API Error 503")
            return {"trainingPlanList": []}

    c = gcm.GarminClient(api=Api(), athlete_id=1)
    c.get_training_plans()
    with pytest.raises(RuntimeError):
        c.get_training_plans(strict=True)                    # pas de cache pour la garde d'écriture
    assert Api.calls == 2


def _gc_errors():
    from garminconnect import GarminConnectAuthenticationError, GarminConnectConnectionError
    import requests
    return [
        # La panne la plus courante en vrai : garminconnect enveloppe la coupure, sans code HTTP
        (GarminConnectConnectionError("Connection error: HTTPSConnectionPool(host='connect.garmin.com')"), True),
        (GarminConnectAuthenticationError("Not authenticated"), True),
        (requests.exceptions.ConnectionError("x"), True),
    ]


@pytest.mark.parametrize("exc,failure", _gc_errors() + [
    (RuntimeError("API Error 503 - Service Unavailable"), True),
    (ConnectionError("réseau coupé"), True),
    (TimeoutError("délai"), True),
    (KeyError("taskList"), False),                          # bug de lecture : doit remonter
    (TypeError("NoneType"), False),
])
def test_is_garmin_failure(exc, failure):
    import garmin_client as gcm
    assert gcm.is_garmin_failure(exc) is failure


def test_logout_during_the_librarys_own_dump_leaves_no_token_file(session_env, monkeypatch):
    """Contre-validation : déconnexion pendant que garminconnect est DANS dump() (chemin déjà lu)
    — le fichier était réécrit après l'effacement du tokenstore."""
    import base64
    import json
    import threading
    import time as _t
    from garminconnect import Garmin
    gcm, store = session_env
    monkeypatch.setattr(gcm, "resolve_athlete_id", lambda api: (42, True))

    def jwt(exp):
        b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
        return f"{b({'alg': 'none'})}.{b({'exp': exp, 'client_id': 'cid'})}.sig"

    store.mkdir(parents=True, exist_ok=True)
    (store / "garmin_tokens.json").write_text(json.dumps(
        {"di_token": jwt(_t.time() - 10), "di_refresh_token": "r", "di_client_id": "cid"}))
    api = Garmin()
    api.client.load(str(store))
    api.client._refresh_di_token = lambda: setattr(api.client, "di_token", jwt(_t.time() + 3600))
    real_dump = type(api.client).dump

    def slow_dump(path):                      # élargit la fenêtre entre lecture du chemin et écriture
        _t.sleep(0.3)
        real_dump(api.client, path)
    api.client.dump = slow_dump
    gcm.adopt_session(api)
    tab = threading.Thread(target=api.client._refresh_session)
    tab.start()
    _t.sleep(0.1)
    gcm.end_session()
    tab.join()
    assert not (store / "garmin_tokens.json").exists()


def test_a_new_login_during_a_stale_refresh_is_not_wiped(session_env, monkeypatch):
    """Le nettoyage au retour ne supprime que le fichier de l'ANCIENNE session : déconnexion
    puis reconnexion pendant qu'un vieux rafraîchissement est en vol."""
    import json
    import threading
    import time as _t
    gcm, store = session_env

    class Slow(_StubClient):
        def _refresh_session(self):
            _t.sleep(0.3)
            self.di_refresh_token = "OLD-FRESH"

    old = _StubApi(Slow(store))
    gcm.adopt_session(old)
    tab = threading.Thread(target=old.client._refresh_session)
    tab.start()
    _t.sleep(0.05)
    gcm.end_session()
    store.mkdir(parents=True, exist_ok=True)
    (store / "garmin_tokens.json").write_text(json.dumps({"di_refresh_token": "NEW-LOGIN"}))
    tab.join()
    assert json.loads((store / "garmin_tokens.json").read_text()) == {"di_refresh_token": "NEW-LOGIN"}
    # Et si c'est bien l'ancien jeton qui a été écrit, il disparaît
    (store / "garmin_tokens.json").write_text(json.dumps({"di_refresh_token": "refresh"}))
    gcm._remove_tokens_of(str(store), {"refresh"})
    assert not (store / "garmin_tokens.json").exists()


def test_remove_tokens_of_edge_cases(session_env, monkeypatch, caplog):
    import json
    gcm, store = session_env
    gcm._remove_tokens_of(None, {"x"})                          # pas de chemin : rien
    gcm._remove_tokens_of(str(store), {"x"})                    # pas de fichier : rien, sans erreur
    store.mkdir(parents=True, exist_ok=True)
    f = store / "garmin_tokens.json"
    f.write_text("{pas du json")
    gcm._remove_tokens_of(str(store), {"x"})
    assert f.exists()                                           # illisible : on ne supprime pas à l'aveugle
    f.write_text(json.dumps({"di_refresh_token": "x"}))
    gcm._remove_tokens_of(str(f), {"x"})                        # chemin du fichier lui-même
    assert not f.exists()
    f.write_text(json.dumps({"di_refresh_token": None}))
    monkeypatch.setattr(type(f), "unlink", lambda self, **k: (_ for _ in ()).throw(OSError("verrouillé")))
    gcm._remove_tokens_of(str(store), {"x"})                    # suppression impossible : journalisé
    assert "non supprimés" in caplog.text
