"""Chaleur, GPX, allure à effort égal et ravitaillement."""

import numpy as np
import pandas as pd
import pytest

from raceday_logic import (
    GpxError,
    cool_equivalent_pace,
    fueling_plan,
    heat_slowdown,
    km_profile,
    minetti_cost,
    pacing_plan,
    parse_gpx,
    weather_from_garmin,
)


def _gpx(n=421, climb_at=(3000, 4000), climb_m=80):
    """Parcours plat vers l'est (~10,5 km), avec une montée entre 3 et 4 km."""
    pts = []
    for k in range(n):
        dist = k * 25.0
        lon = 1.44 + dist / (111320 * np.cos(np.radians(43.6)))
        ele = 150.0
        if climb_at[0] <= dist <= climb_at[1]:
            ele += climb_m * (dist - climb_at[0]) / (climb_at[1] - climb_at[0])
        elif dist > climb_at[1]:
            ele += climb_m
        pts.append(f'<trkpt lat="43.6" lon="{lon:.6f}"><ele>{ele:.1f}</ele></trkpt>')
    return ('<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
            f"<trk><trkseg>{''.join(pts)}</trkseg></trk></gpx>").encode()


def test_heat_rule_of_thumb():
    assert heat_slowdown(12, 5)["mid"] == 0                     # frais
    hot = heat_slowdown(30, 20)                                  # 86 + 68 = 154 °F
    assert 4 <= hot["mid"] <= 6 and not hot["hard"]
    assert heat_slowdown(38, 28)["hard"]
    assert heat_slowdown(None, 10) is None


def test_cool_equivalent_pace_is_faster():
    heat = heat_slowdown(30, 20)
    assert cool_equivalent_pace(340, heat) < 340
    assert cool_equivalent_pace(340, None) is None


def test_garmin_weather_is_converted_from_fahrenheit():
    w = weather_from_garmin({"temp": 86, "dewPoint": 68, "relativeHumidity": 55, "windSpeed": 10,
                             "weatherTypeDTO": {"desc": "Ensoleillé"}})
    assert round(w["temp_c"]) == 30 and round(w["dewpoint_c"]) == 20 and round(w["wind_kmh"]) == 16
    assert weather_from_garmin({}) is None


def test_gpx_parsing_and_km_profile():
    track = parse_gpx(_gpx())
    assert 10400 < track["dist"].iloc[-1] < 10600
    prof = km_profile(track)
    assert len(prof) == 11 and prof["km"].tolist()[0] == 1
    assert prof.loc[prof["km"] == 4, "grade"].iloc[0] > 0.05      # la montée est au km 4
    assert abs(prof["gain"].sum() - 80) < 8                      # dénivelé non gonflé par le lissage


def test_gpx_security_guards():
    with pytest.raises(GpxError, match="DOCTYPE"):
        parse_gpx(b'<?xml version="1.0"?><!DOCTYPE lol [<!ENTITY a "aaaa">]><gpx>&a;</gpx>')
    with pytest.raises(GpxError, match="trop gros"):
        parse_gpx(b"<gpx>" + b" " * (5 * 1024 * 1024 + 1) + b"</gpx>")
    with pytest.raises(GpxError, match="Aucun tracé"):
        parse_gpx(b'<gpx xmlns="http://www.topografix.com/GPX/1/1"></gpx>')
    with pytest.raises(GpxError, match="illisible"):
        parse_gpx(b"<gpx><trk>")


def test_equal_effort_pacing_hits_the_target_and_slows_uphill():
    plan = pacing_plan(km_profile(parse_gpx(_gpx())), target_time_s=50 * 60)
    assert abs(plan["elapsed_s"].iloc[-1] - 3000) < 1
    climb = plan.loc[plan["km"] == 4, "pace_s"].iloc[0]
    flat = plan.loc[plan["km"] == 2, "pace_s"].iloc[0]
    assert climb > flat * 1.2
    assert minetti_cost(-0.2) < minetti_cost(0) < minetti_cost(0.1)


def test_downhill_gain_is_capped():
    prof = pd.DataFrame({"km": [1, 2], "length_m": [1000, 1000], "gain": [0, 0], "loss": [0, 200],
                         "grade": [0.0, -0.2], "ele_end": [0, -200]})
    plan = pacing_plan(prof, 600)
    assert plan["pace_s"].iloc[1] >= plan["pace_s"].iloc[0] * 0.88 - 1e-9


def test_fueling_follows_duration():
    prof = km_profile(parse_gpx(_gpx()))
    short = fueling_plan(pacing_plan(prof, 50 * 60))
    assert short["events"] == [] and short["carbs_g_per_h"] == (0, 0)
    long = fueling_plan(pacing_plan(prof, 2 * 3600))
    assert long["carbs_g_per_h"] == (30, 60) and len(long["events"]) >= 3
    assert all(e["at_s"] < long["total_s"] - 600 for e in long["events"])
    assert fueling_plan(pacing_plan(prof, 3 * 3600))["carbs_g_per_h"] == (60, 90)
