"""Tests du verdict de forme et de la rétrogradation de séance."""

import pytest

from forme_logic import (
    compute_forme_verdict,
    downgrade_session,
    forme_downgrade,
    hrv_is_degraded,
    sleep_quality,
)


class TestHrvIsDegraded:
    @pytest.mark.parametrize("status", ["UNBALANCED", "LOW", "POOR", "low", "unbalanced"])
    def test_degraded(self, status):
        assert hrv_is_degraded(status) is True

    @pytest.mark.parametrize("status", ["BALANCED", "balanced", None, ""])
    def test_ok(self, status):
        assert hrv_is_degraded(status) is False


class TestSleepQuality:
    @pytest.mark.parametrize("score,expected", [
        (90, "good"), (75, "good"), (74, "medium"), (60, "medium"),
        (59, "poor"), (0, "poor"), (None, None),
    ])
    def test_bands(self, score, expected):
        assert sleep_quality(score) == expected


class TestComputeFormeVerdict:
    def test_fresh_and_recovered(self):
        v = compute_forme_verdict(tsb=8.0, hrv_status="BALANCED", sleep_score=85)
        assert v["level"] == 2
        assert v["key"] == "performance"
        assert len(v["reasons"]) == 3

    def test_normal(self):
        v = compute_forme_verdict(tsb=-5.0, hrv_status="BALANCED", sleep_score=80)
        assert v["level"] == 1

    def test_fatigued(self):
        v = compute_forme_verdict(tsb=-25.0, hrv_status="BALANCED", sleep_score=85)
        assert v["level"] == 0
        assert v["key"] == "recuperation"

    def test_fresh_but_bad_hrv(self):
        v = compute_forme_verdict(tsb=8.0, hrv_status="LOW", sleep_score=85)
        assert v["level"] == 1

    def test_fresh_but_bad_hrv_and_sleep(self):
        v = compute_forme_verdict(tsb=8.0, hrv_status="LOW", sleep_score=40)
        assert v["level"] == 0

    def test_level_floor_at_zero(self):
        v = compute_forme_verdict(tsb=-30.0, hrv_status="POOR", sleep_score=30)
        assert v["level"] == 0

    def test_missing_data(self):
        v = compute_forme_verdict(tsb=None, hrv_status=None, sleep_score=None)
        assert v["level"] == 1
        assert v["reasons"] == []


class TestFormeDowngrade:
    def test_all_good(self):
        assert forme_downgrade("BALANCED", 85) == 0

    def test_hrv_only(self):
        assert forme_downgrade("UNBALANCED", 85) == 1

    def test_sleep_only(self):
        assert forme_downgrade("BALANCED", 45) == 1

    def test_both(self):
        assert forme_downgrade("POOR", 45) == 2

    def test_missing_data(self):
        assert forme_downgrade(None, None) == 0


class TestDowngradeSession:
    @pytest.mark.parametrize("key,expected", [
        ("sortie_longue", "endurance"),
        ("tempo", "endurance"),
        ("endurance", "recuperation"),
        ("recuperation", "recuperation"),
    ])
    def test_one_step(self, key, expected):
        assert downgrade_session(key) == expected

    def test_two_steps(self):
        assert downgrade_session("tempo", 2) == "recuperation"
        assert downgrade_session("sortie_longue", 2) == "recuperation"

    def test_zero_steps(self):
        assert downgrade_session("tempo", 0) == "tempo"

    def test_unknown_key_passthrough(self):
        assert downgrade_session("inconnu") == "inconnu"


class TestRecommendSessionDowngrade:
    """Intégration : recommend_session applique la rétrogradation."""

    def test_downgrade_applied(self, make_running_df):
        from next_session_logic import recommend_session
        df = make_running_df(n=10, days_apart=3)
        rec_normal = recommend_session(df)
        rec_down = recommend_session(df, downgrade=1)
        if rec_normal["session_key"] == "recuperation":
            assert rec_down["session_key"] == "recuperation"
            assert rec_down["downgraded_from"] is None
        else:
            assert rec_down["session_key"] != rec_normal["session_key"]
            assert rec_down["downgraded_from"] == rec_normal["session_key"]

    def test_no_downgrade_by_default(self, make_running_df):
        from next_session_logic import recommend_session
        rec = recommend_session(make_running_df(n=10, days_apart=3))
        assert rec["downgraded_from"] is None


# ---------------------------------------------------------------------------
# parse_recovery — lecture unique des payloads HRV / sommeil / stats
# ---------------------------------------------------------------------------

def test_parse_recovery_dicts_and_lists():
    from forme_logic import parse_recovery
    r = parse_recovery(
        [{"hrvSummary": {"status": "BALANCED", "lastNightAvg": 52}}],
        {"dailySleepDTO": {"sleepTimeSeconds": 27000, "sleepScores": {"overall": {"value": 81}}}},
        [{"restingHeartRate": 48}],
    )
    assert (r["hrv_status"], r["hrv_last"], r["sleep_sec"], r["sleep_score"]) == ("BALANCED", 52, 27000, 81)
    assert r["daily"]["restingHeartRate"] == 48


def test_parse_recovery_empty_or_garbage():
    from forme_logic import parse_recovery
    for raw in (None, {}, [], "x", [None]):
        r = parse_recovery(raw, raw, raw)
        assert r["hrv_status"] is None and r["sleep_score"] is None and r["daily"] == {}


# ---------------------------------------------------------------------------
# Statut HRV « NONE » (données réelles, septembre 2026)
# ---------------------------------------------------------------------------
def test_hrv_none_status_is_no_status():
    """Garmin renvoie "NONE" sans baseline : ce n'est pas « HRV dans ta baseline »."""
    from forme_logic import parse_recovery
    rec = parse_recovery({"hrvSummary": {"status": "NONE", "lastNightAvg": 50}}, {})
    assert rec["hrv_status"] is None and rec["hrv_last"] == 50
    v = compute_forme_verdict(tsb=-5.0, hrv_status=rec["hrv_status"], sleep_score=None)
    assert not any("HRV" in r for r in v["reasons"])
    assert parse_recovery({"hrvSummary": {"status": "BALANCED"}}, {})["hrv_status"] == "BALANCED"


def test_hrv_labels_are_french():
    from forme_logic import hrv_label
    assert hrv_label("BALANCED") == "équilibrée" and hrv_label("unbalanced") == "déséquilibrée"
    assert hrv_label(None) is None and hrv_label("") is None
    v = compute_forme_verdict(tsb=0.0, hrv_status="LOW", sleep_score=None)
    assert "HRV basse" in v["reasons"][1]


def test_tsb_label_is_the_same_everywhere_at_the_boundaries():
    """Revue #1 : trois jeux de seuils en dur (Forme, onglet Charge, Accueil) donnaient trois verdicts."""
    from forme_logic import TSB_FATIGUE, TSB_FRESH, tsb_metric_delta
    assert tsb_metric_delta(TSB_FRESH + 0.1) == ("Bien reposé", "normal")
    assert tsb_metric_delta(TSB_FRESH) == ("Charge normale", "off")
    assert tsb_metric_delta(TSB_FATIGUE) == ("Charge normale", "off")
    assert tsb_metric_delta(TSB_FATIGUE - 0.1) == ("Récupération nécessaire", "inverse")
    assert tsb_metric_delta(30)[0] == "Bien reposé"            # plus de « Sous-entraîné » d'un seul onglet
