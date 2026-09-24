"""Rendu des blocs physio avec des streams synthétiques."""

from streamlit.testing.v1 import AppTest


def _app_quality(kind):
    import sys
    sys.path.insert(0, "tests")
    from test_physio_logic import make_streams
    from physio_ui import render_signal_quality

    if kind == "lock":
        streams = make_streams(3600, hr=lambda t: 178.0 if 1200 <= t < 1500 else 145.0)
    elif kind == "drift":
        streams = make_streams(3600, hr=lambda t: 140.0 if t < 2100 else 154.0)
    else:
        streams = make_streams(1500)
    render_signal_quality(streams)


def _run(kind):
    at = AppTest.from_function(_app_quality, args=(kind,), default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_lock_warning_shown():
    at = _run("lock")
    assert any("FC probablement fausse" in w.value for w in at.warning)
    assert any("km" in w.value for w in at.warning)


def test_drift_rendered():
    at = _run("drift")
    assert any("À consolider" in m.value for m in at.markdown)
    assert any("Signal cardio cohérent" in s.value for s in at.success)


def test_short_run_explained():
    at = _run("short")
    assert any("non mesurable" in i.value for i in at.info)


def _app_progress(fail_at):
    """Section Progression avec un client qui refuse à la `fail_at`-ième sortie."""
    import sys
    from datetime import datetime, timedelta
    sys.path.insert(0, "tests")
    import pandas as pd
    import physio_ui
    from garminconnect import GarminConnectTooManyRequestsError
    from test_physio_logic import make_streams

    class Client:
        calls = 0

        def get_streams(self, activity_id, strict=False):
            Client.calls += 1
            if fail_at and Client.calls >= fail_at:
                raise GarminConnectTooManyRequestsError("429")
            return make_streams(3600, hr=lambda t: 140.0 if t < 2100 else 150.0)

    physio_ui.get_garmin_client = lambda: Client()
    now = datetime.now()
    df = pd.DataFrame([{
        "activityId": i, "startTimeLocal": now - timedelta(days=3 * i),
        "activityType": "running", "activityName": f"Sortie {i}",
        "duration_min": 60.0, "avgSpeed_ms": 3.0, "avgHR": 145.0,
        "avgCadence": 176.0, "workoutType": "training", "avgPace_sec": 333.0,
    } for i in range(6)])
    physio_ui.render_aerobic_progress(df, 7)


def test_progress_renders_trend_and_drift():
    at = AppTest.from_function(_app_progress, args=(0,), default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]
    assert [m.label for m in at.metric] == ["Efficacité actuelle"]
    assert any("6 sortie(s) mesurable(s)" in c.value for c in at.caption)


def test_progress_stops_at_first_refusal():
    at = AppTest.from_function(_app_progress, args=(3,), default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]
    note = next(c.value for c in at.caption if "mesurable" in c.value)
    assert "limite le nombre d'appels" in note and "2/6 analysées" in note
