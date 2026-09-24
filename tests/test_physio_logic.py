"""
Tests de physio_logic : FC calée sur la cadence, dérive cardiaque, facteur
d'efficacité. Streams synthétiques au format de `build_streams`.
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from physio_logic import (
    aerobic_decoupling,
    decoupling_history,
    decoupling_level,
    efficiency_change,
    efficiency_trend,
    hr_cadence_lock,
    summary_lock_suspect,
)


def make_streams(duration_s, dt=5.0, hr=lambda t: 145.0, speed=lambda t: 3.0,
                 cad=lambda t: 176.0, alt=lambda t: 100.0, gap_at=None, gap_s=0.0):
    """Streams réguliers ; `gap_at` insère une pause de `gap_s` secondes."""
    times, t = [], 0.0
    while t <= duration_s:
        times.append(t)
        t += dt
        if gap_at is not None and times[-1] < gap_at <= t:
            t += gap_s
    dist, d = [], 0.0
    for i, tt in enumerate(times):
        if i:
            d += speed(tt) * min(times[i] - times[i - 1], 30.0)
        dist.append(d)
    return {
        "time": times,
        "distance": dist,
        "heartrate": [hr(t) for t in times],
        "velocity_smooth": [speed(t) for t in times],
        "cadence": [cad(t) for t in times],
        "altitude": [alt(t) for t in times],
    }


# ---------------------------------------------------------------------------
# hr_cadence_lock
# ---------------------------------------------------------------------------

class TestHrCadenceLock:
    def test_mid_run_lock_detected(self):
        streams = make_streams(
            3600, hr=lambda t: 178.0 if 1200 <= t < 1500 else 145.0,
            cad=lambda t: 177.0,
        )
        res = hr_cadence_lock(streams)
        assert res["detected"]
        (start, end), = res["segments"]
        assert start == pytest.approx(1200, abs=10)
        assert end == pytest.approx(1500, abs=10)
        assert res["locked_s"] == pytest.approx(300, abs=15)
        assert res["share"] == pytest.approx(300 / 3600, abs=0.01)
        assert len(res["mask"]) == len(streams["time"])

    def test_short_natural_crossing_ignored(self):
        """FC et cadence se croisent 60 s pendant un effort : pas un lock."""
        streams = make_streams(
            3600, hr=lambda t: 177.0 if 1800 <= t < 1860 else 150.0,
        )
        assert not hr_cadence_lock(streams)["detected"]

    def test_progressive_finish_is_not_a_lock(self):
        """Cas réel : la FC monte avec l'allure jusqu'au niveau de la cadence."""
        def hr(t):
            return 150.0 + 30.0 * min(max((t - 2400) / 600, 0), 1)  # 150 → 180 en 10 min

        def cad(t):
            return 176.0 + 5.0 * min(max((t - 2400) / 600, 0), 1)

        streams = make_streams(3600, hr=hr, cad=cad)
        assert not hr_cadence_lock(streams)["detected"]

    def test_lock_at_start_detected(self):
        """FC calée dès le départ : plus haute que tout le reste de la sortie."""
        streams = make_streams(3000, hr=lambda t: 176.0 if t < 300 else 140.0)
        res = hr_cadence_lock(streams)
        assert res["detected"]
        assert res["segments"][0][0] == pytest.approx(0, abs=1)

    def test_walking_cadence_ignored(self):
        streams = make_streams(1800, hr=lambda t: 112.0, cad=lambda t: 112.0)
        assert not hr_cadence_lock(streams)["detected"]

    def test_missing_streams(self):
        res = hr_cadence_lock({"time": [0, 5, 10], "heartrate": [140, 141, 142]})
        assert not res["detected"]
        assert res["mask"] == [False, False, False]
        assert hr_cadence_lock({})["segments"] == []

    def test_none_values_tolerated(self):
        streams = make_streams(3600, hr=lambda t: 178.0 if 1200 <= t < 1500 else 145.0)
        streams["heartrate"][10] = None
        streams["cadence"][20] = None
        assert hr_cadence_lock(streams)["detected"]

    def test_duration_is_time_based_not_sample_based(self):
        """90 s de lock échantillonné à 1 s = 90 points, mais < 120 s : ignoré."""
        streams = make_streams(
            3600, dt=1.0, hr=lambda t: 178.0 if 1200 <= t < 1290 else 145.0,
        )
        assert not hr_cadence_lock(streams)["detected"]


# ---------------------------------------------------------------------------
# aerobic_decoupling
# ---------------------------------------------------------------------------

class TestAerobicDecoupling:
    def test_steady_run_no_drift(self):
        res = aerobic_decoupling(make_streams(3600))
        assert res["valid"]
        assert res["decoupling_pct"] == pytest.approx(0, abs=0.1)
        assert decoupling_level(res["decoupling_pct"]) == "solide"

    def test_hr_drift_measured(self):
        """FC +10 % en 2de moitié à vitesse égale → dérive ≈ 9 %."""
        streams = make_streams(3600, hr=lambda t: 140.0 if t < 2100 else 154.0)
        res = aerobic_decoupling(streams)
        assert res["valid"]
        assert res["decoupling_pct"] == pytest.approx((1 - 140 / 154) * 100, abs=0.5)
        assert decoupling_level(res["decoupling_pct"]) == "a_consolider"

    def test_too_short(self):
        res = aerobic_decoupling(make_streams(1800))
        assert not res["valid"]
        assert "trop courte" in res["reason"]
        assert res["decoupling_pct"] is None

    def test_intervals_rejected_but_pct_kept(self):
        streams = make_streams(3600, speed=lambda t: 4.2 if int(t // 120) % 2 else 2.4)
        res = aerobic_decoupling(streams)
        assert not res["valid"]
        assert "irrégulière" in res["reason"]
        assert res["speed_cv"] > 0.15
        assert res["decoupling_pct"] is not None

    def test_warmup_excluded(self):
        """FC basse pendant l'échauffement : sans exclusion, fausse dérive."""
        streams = make_streams(3600, hr=lambda t: 120.0 if t < 600 else 145.0)
        assert aerobic_decoupling(streams)["decoupling_pct"] == pytest.approx(0, abs=0.1)
        biased = aerobic_decoupling(streams, warmup_s=0)
        assert biased["decoupling_pct"] > 3

    def test_pause_not_counted_as_effort(self):
        # 3400 s au chrono dont 900 s de pause → 2500 s d'effort
        streams = make_streams(3400, gap_at=1200, gap_s=900)
        res = aerobic_decoupling(streams)
        assert res["moving_min"] == pytest.approx(2500 / 60, abs=0.5)

    def test_excluded_hr_invalidates(self):
        streams = make_streams(3600)
        n = len(streams["time"])
        mask = [i < n * 0.2 for i in range(n)]
        res = aerobic_decoupling(streams, exclude_mask=mask)
        assert not res["valid"]
        assert "FC suspecte" in res["reason"]

    def test_hilly_flag(self):
        streams = make_streams(3600, alt=lambda t: 100 + 40 * np.sin(t / 120))
        res = aerobic_decoupling(streams)
        assert res["hilly"]
        assert res["climb_m_per_km"] > 20

    def test_progressive_run_rejected(self):
        """Accélérer en 2de moitié masque la dérive : non comparable."""
        streams = make_streams(
            3600, speed=lambda t: 2.8 if t < 2100 else 3.2,
            hr=lambda t: 140.0 if t < 2100 else 160.0,
        )
        res = aerobic_decoupling(streams)
        assert not res["valid"]
        assert "accéléré" in res["reason"]
        assert res["half_speed_diff"] == pytest.approx(0.143, abs=0.01)

    def test_climb_concentrated_in_one_half_rejected(self):
        streams = make_streams(
            3600, alt=lambda t: 100 + (20 * np.sin(t / 60) if t > 2100 else 0),
        )
        res = aerobic_decoupling(streams)
        assert not res["valid"]
        assert "Dénivelé inégal" in res["reason"]

    def test_threshold_intervals_not_flagged_as_lock(self):
        """Revue : répétitions au seuil, FC en cinétique réelle (τ 30 s) jusqu'à la cadence."""
        import math

        def hr(t):
            k, x = divmod(t, 480)             # 5 min d'effort, 3 min de récup
            if k >= 5:
                return 140.0
            if x < 300:
                return 140 + 37 * (1 - math.exp(-x / 30)) + 0.01 * x
            return 140 + 37 * math.exp(-(x - 300) / 40)

        streams = make_streams(3000, hr=hr,
                               cad=lambda t: 178.0 if (t % 480) < 300 and t < 2400 else 160.0)
        assert not hr_cadence_lock(streams)["detected"]

    def test_pause_after_rejected_segment_leaves_no_phantom(self):
        def hr(t):
            return 150.0 + 30.0 * min(max((t - 2400) / 600, 0), 1)
        streams = make_streams(3600, hr=hr, cad=lambda t: 181.0, gap_at=3300, gap_s=200)
        res = hr_cadence_lock(streams)
        assert not res["detected"] and res["segments"] == []

    def test_race_pace_near_cadence_not_flagged_as_lock(self):
        """Seuil/course : FC 180-185 ≈ cadence pendant 30 min, montée progressive."""
        streams = make_streams(
            3000, hr=lambda t: min(150 + t / 20, 183.0), cad=lambda t: 184.0,
        )
        assert not hr_cadence_lock(streams)["detected"]

    def test_slowdown_at_constant_hr_is_drift(self):
        """Séance guidée à la FC (Run Coach) : ralentir à FC tenue = dérive."""
        streams = make_streams(3600, hr=lambda t: 147.0,
                               speed=lambda t: 3.0 if t < 2100 else 2.82)
        res = aerobic_decoupling(streams)
        assert res["valid"], res["reason"]
        assert res["decoupling_pct"] == pytest.approx(6.0, abs=0.3)

    def test_eased_effort_rejected(self):
        """Ralentir ET baisser la FC : fin de sortie relâchée, pas comparable."""
        streams = make_streams(3600, hr=lambda t: 150.0 if t < 2100 else 138.0,
                               speed=lambda t: 3.0 if t < 2100 else 2.7)
        res = aerobic_decoupling(streams)
        assert not res["valid"] and "relâché" in res["reason"]

    def test_flat_route_with_altimeter_noise_not_hilly(self):
        rng = np.random.default_rng(0)
        noise = rng.uniform(-1.0, 1.0, 2000)
        streams = make_streams(3600, alt=lambda t: 100 + noise[int(t // 5) % 2000])
        res = aerobic_decoupling(streams)
        assert res["climb_m_per_km"] < 3
        assert not res["hilly"]

    def test_gps_altitude_spike_ignored(self):
        streams = make_streams(3600, alt=lambda t: 58.0 if 1800 <= t < 1805 else 100.0)
        assert aerobic_decoupling(streams)["climb_m_per_km"] < 1

    def test_missing_hr_reported(self):
        streams = make_streams(3600)
        n = len(streams["time"])
        streams["heartrate"] = [h if i < n * 0.3 else None for i, h in enumerate(streams["heartrate"])]
        res = aerobic_decoupling(streams)
        assert not res["valid"] and "FC absente" in res["reason"]

    def test_missing_velocity(self):
        streams = make_streams(3600)
        del streams["velocity_smooth"]
        res = aerobic_decoupling(streams)
        assert not res["valid"]
        assert res["reason"]

    def test_walking_samples_not_counted(self):
        streams = make_streams(3600, speed=lambda t: 1.0 if t < 1800 else 3.0)
        assert aerobic_decoupling(streams)["moving_min"] == pytest.approx(30, abs=0.5)


@pytest.mark.parametrize("pct,level", [
    (-4.0, "solide"), (4.99, "solide"), (5.0, "a_consolider"),
    (9.9, "a_consolider"), (10.0, "marquee"), (25.0, "marquee"),
    (None, None), (float("nan"), None),
])
def test_decoupling_level(pct, level):
    assert decoupling_level(pct) == level


@pytest.mark.parametrize("hr,cad,expected", [
    (176, 177, True), (150, 177, False), (112, 112, False), (None, 170, False),
    ("x", 170, False),
])
def test_summary_lock_suspect(hr, cad, expected):
    assert summary_lock_suspect(hr, cad) is expected


# ---------------------------------------------------------------------------
# efficiency_trend / efficiency_change
# ---------------------------------------------------------------------------

def _runs(n=30, start=datetime(2026, 1, 1), ef_growth=0.0):
    rows = []
    for i in range(n):
        hr = 145.0
        speed = 3.0 * (1 + ef_growth * i)
        rows.append({
            "activityId": i, "startTimeLocal": start + timedelta(days=3 * i),
            "activityType": "running", "avgSpeed_ms": speed, "avgHR": hr,
            "duration_min": 50.0, "avgCadence": 176.0, "workoutType": "training",
            "avgPace_sec": 1000 / speed,
        })
    return pd.DataFrame(rows)


class TestEfficiencyTrend:
    def test_ef_formula(self):
        trend = efficiency_trend(_runs(5))
        assert trend["ef"].iloc[0] == pytest.approx(3.0 * 60 / 145)

    def test_filters(self):
        df = _runs(10)
        df.loc[0, "workoutType"] = "race"
        df.loc[1, "duration_min"] = 10
        df.loc[2, "activityType"] = "cycling"
        df.loc[3, ["avgHR", "avgCadence"]] = [176.0, 177.0]  # lock probable
        df.loc[4, "avgHR"] = None
        trend = efficiency_trend(df, max_hr_quantile=1.0)
        assert set(trend["activityId"]) == set(range(5, 10))

    def test_intense_runs_dropped(self):
        df = _runs(10)
        df.loc[9, "avgHR"] = 175.0
        assert 9 not in set(efficiency_trend(df)["activityId"])

    def test_empty(self):
        assert efficiency_trend(pd.DataFrame()).empty
        assert efficiency_trend(None).empty
        assert efficiency_trend(_runs(3).assign(activityType="cycling")).empty

    def test_change_positive_when_improving(self):
        trend = efficiency_trend(_runs(60, ef_growth=0.003))
        change = efficiency_change(trend, days=90)
        assert change is not None and change > 0

    def test_change_none_when_reference_too_old(self):
        df = _runs(10)
        df.loc[9, "startTimeLocal"] = df.loc[8, "startTimeLocal"] + timedelta(days=200)
        assert efficiency_change(efficiency_trend(df, max_hr_quantile=1.0), days=90) is None

    def test_change_none_without_history(self):
        assert efficiency_change(efficiency_trend(_runs(5)), days=90) is None
        assert efficiency_change(pd.DataFrame(), days=90) is None


def test_decoupling_history_keeps_valid_sorted():
    long_run = make_streams(3600, hr=lambda t: 140.0 if t < 2100 else 154.0)
    short_run = make_streams(1200)
    items = [
        ({"activityId": 2, "startTimeLocal": "2026-03-02", "activityName": "b"}, long_run),
        ({"activityId": 1, "startTimeLocal": "2026-03-01", "activityName": "a"}, make_streams(3600)),
        ({"activityId": 3, "startTimeLocal": "2026-03-03"}, short_run),
        ({"activityId": 4, "startTimeLocal": "2026-03-04"}, {}),
    ]
    hist = decoupling_history(items)
    assert list(hist["activityId"]) == [1, 2]
    assert list(hist["level"]) == ["solide", "a_consolider"]
    assert decoupling_history([]).empty


def test_decoupling_candidates_bounded_recent_long_runs():
    from physio_logic import decoupling_candidates
    today = datetime(2026, 6, 1)
    rows = []
    for i in range(40):
        rows.append({
            "activityId": i, "startTimeLocal": today - timedelta(days=2 * i),
            "activityType": "running", "duration_min": 60.0 if i % 2 else 30.0,
            "workoutType": "race" if i == 1 else "training",
        })
    rows.append({"activityId": 99, "startTimeLocal": today, "activityType": "cycling",
                 "duration_min": 120.0, "workoutType": "training"})
    picked = decoupling_candidates(pd.DataFrame(rows), today=today, max_runs=5)
    ids = [r["activityId"] for r in picked]
    assert ids == [3, 5, 7, 9, 11]          # longues, hors course/vélo, récentes d'abord
    old = decoupling_candidates(pd.DataFrame(rows), today=today, weeks=1)
    assert all(r["startTimeLocal"] >= today - timedelta(weeks=1) for r in old)
    assert decoupling_candidates(pd.DataFrame(), today=today) == []
