"""Veille santé : concordance des signaux, robustesse au bruit, honnêteté sur les manques."""

import numpy as np
import pandas as pd

from illness_logic import daily_health_frame, health_watch

TODAY = pd.Timestamp("2026-09-25")


def _frame(nights=35, rhr=48.0, hrv=60.0, resp=14.5, spo2=96.0, seed=1, last=None):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(end=TODAY - pd.Timedelta(days=1), periods=nights, freq="D")
    df = pd.DataFrame({"rhr": rhr + rng.normal(0, 1.0, nights),
                       "hrv": hrv + rng.normal(0, 4.0, nights),
                       "resp": resp + rng.normal(0, 0.3, nights),
                       "spo2": spo2 + rng.normal(0, 0.6, nights)}, index=idx)
    for key, value in (last or {}).items():
        df.iloc[-1, df.columns.get_loc(key)] = value
    return df


def test_normal_night_is_calm():
    w = health_watch(_frame(), TODAY)
    assert w.level == 0 and w.status == "good" and not w.flagged


def test_one_signal_is_only_a_watch():
    w = health_watch(_frame(last={"rhr": 58}), TODAY)          # écart très net (z ≥ 3)
    assert w.level == 1 and [s.key for s in w.flagged] == ["rhr"]
    assert "Un seul signal" in w.message


def test_concordant_signals_raise_the_alert():
    w = health_watch(_frame(last={"rhr": 56, "resp": 16.5}), TODAY)
    assert w.level == 2 and w.status == "serious"
    assert {s.key for s in w.flagged} == {"rhr", "resp"}


def test_direction_matters():
    """Une FC plus basse ou une HRV plus haute n'est pas un signe de maladie."""
    w = health_watch(_frame(last={"rhr": 40, "hrv": 90}), TODAY)
    assert w.level == 0


def test_statistical_but_tiny_deviation_does_not_count():
    """Respiration très stable : +0,6/min fait un gros z mais reste sous le seuil physiologique."""
    df = _frame()
    df["resp"] = 14.5
    df.iloc[-1, df.columns.get_loc("resp")] = 15.1
    assert health_watch(df, TODAY).level == 0


def test_persistence_is_reported():
    df = _frame()
    df.iloc[-2, df.columns.get_loc("rhr")] = 55
    df.iloc[-1, df.columns.get_loc("rhr")] = 56
    s = health_watch(df, TODAY).flagged[0]
    assert s.persistent


def test_missing_and_learning_signals_are_said_not_invented():
    df = _frame()
    df["spo2"] = np.nan                        # pas de SpO2
    df.loc[df.index[:-8], "hrv"] = np.nan      # HRV depuis 8 nuits seulement
    w = health_watch(df, TODAY)
    by = {s.key: s for s in w.signals}
    assert by["spo2"].status == "missing" and by["hrv"].status == "learning"
    assert "norme en construction" in by["hrv"].note
    assert w.level == 0 and "2 signal" in w.message


def test_stale_or_empty_data_gives_nothing():
    old = _frame()
    old.index = old.index - pd.Timedelta(days=5)
    assert health_watch(old, TODAY) is None
    assert health_watch(pd.DataFrame(), TODAY) is None


def test_frame_from_garmin_payloads():
    sleep = [{"calendarDate": "2026-09-24", "averageRespirationValue": 14.2, "averageSpO2Value": 95},
             {"calendarDate": "2026-09-23", "averageRespirationValue": 0}]      # 0 = pas de mesure
    hrv = [{"calendarDate": "2026-09-24", "lastNightAvg": 58}]
    rhr = [{"calendarDate": "2026-09-24", "restingHR": 47}, {"calendarDate": "2026-09-23", "restingHR": 49}]
    f = daily_health_frame(sleep, hrv, rhr)
    assert list(f.columns) == ["rhr", "hrv", "resp", "spo2"]
    assert f.loc["2026-09-24"].tolist() == [47, 58, 14.2, 95]
    assert np.isnan(f.loc["2026-09-23", "resp"])


def test_single_mild_one_night_deviation_is_ignored():
    """Calibrage sur données réelles : une FC de repos +5 bpm une seule nuit (grosse
    séance la veille) ne doit pas allumer la carte."""
    df = _frame()
    df.iloc[-1, df.columns.get_loc("rhr")] = df["rhr"].iloc[:-3].median() + 5
    w = health_watch(df, TODAY)
    if w.signals[0].z < 3:
        assert w.level == 0 and "ignoré" in w.signals[0].note


def test_isolated_signal_is_cleared_not_only_hidden():
    """Revue #2 : niveau 0 « rien » mais la carte (et le MCP) listaient encore le signal."""
    from home_logic import health_signal
    w = health_watch(_frame(last={"rhr": 52.3}), TODAY)       # +4,3 bpm, z ≈ 2,9 : isolé, sous 3 σ
    assert w.level == 0 and w.flagged == []
    rhr = next(s for s in w.signals if s.key == "rhr")
    assert rhr.status == "ignored" and not rhr.flagged and rhr.z >= 2
    body = health_signal(w)["body"]
    assert not body.startswith("FC de repos")
    assert "FC de repos : écart isolé" in body



def test_only_signal_measured_and_ignored_reads_well():
    """Revue : « 0 signal(aux) dans ta norme () » quand le seul signal mesuré était ignoré."""
    df = _frame(last={"rhr": 52.3})
    df[["hrv", "resp", "spo2"]] = np.nan                     # montre sans HRV ni SpO2 : la FC seule
    w = health_watch(df, TODAY)
    assert w.level == 0 and "0 signal" not in w.message and "()" not in w.message
    assert "écart isolé" in w.message
