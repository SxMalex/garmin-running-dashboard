"""Page Activités : intensité (IF), zones, charge, polarisation."""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from activities_logic import enrich, intensity_distribution, intensity_zone, polarization
from next_session_logic import compute_pmc_series


def _acts(rows):
    base = datetime(2026, 9, 1, 8)
    out = []
    for i, (kind, pace, minutes) in enumerate(rows):
        out.append({"activityId": i, "startTimeLocal": base + timedelta(days=i), "activityType": kind,
                    "avgPace_sec": pace, "duration_min": minutes, "distance_km": minutes * 60 / pace if pace else 0,
                    "trainingLoad": 50.0, "avgHR": 140})
    return pd.DataFrame(out)


def test_zones_follow_intensity_factor():
    assert intensity_zone(0.70) == "recup"
    assert intensity_zone(0.85) == "endurance"
    assert intensity_zone(0.90) == "tempo"
    assert intensity_zone(1.00) == "seuil"
    assert intensity_zone(1.10) == "vma"
    assert intensity_zone(None) == "autre" and intensity_zone(float("nan")) == "autre"


def test_enrich_uses_the_pmc_formula():
    df = _acts([("running", 300, 60), ("running", 360, 45), ("cycling", 0, 90)])
    out = enrich(df, 300)
    assert out.loc[0, "intensity_pct"] == 100 and out.loc[0, "zone"] == "seuil"
    assert round(out.loc[0, "tss"]) == 100                       # 1 h au seuil = 100
    assert out.loc[2, "zone"] == "autre" and out.loc[2, "tss"] > 0   # sport croisé noté
    pmc = compute_pmc_series(df, 300)
    assert round(float(pmc["tss"].sum()), 3) == round(float(out["tss"].sum()), 3)


def test_polarization_verdicts():
    easy = enrich(_acts([("running", 400, 60)] * 8 + [("running", 300, 30)]), 300)
    assert polarization(easy)["status"] == "good" and polarization(easy)["easy"] > 0.8
    grey = enrich(_acts([("running", 330, 60)] * 5 + [("running", 400, 30)]), 300)
    p = polarization(grey)
    assert p["status"] == "warning" and "zone grise" in p["verdict"]
    assert polarization(enrich(_acts([("cycling", 0, 60)]), 300)) is None


def test_weekly_distribution_excludes_cross_training():
    out = intensity_distribution(enrich(_acts([("running", 400, 60), ("cycling", 0, 90),
                                                ("running", 290, 30)]), 300))
    assert set(out["zone"]) == {"recup", "vma"} and out["minutes"].sum() == 90


def test_empty_frame():
    out = enrich(pd.DataFrame(columns=["activityType", "avgPace_sec", "duration_min", "startTimeLocal"]), 300)
    assert out.empty and "zone" in out
    assert intensity_distribution(out).empty


def _mixed_history():
    """20 courses (charge Garmin 50, TSS d'allure 100 → facteur ≈ 2), + wing, + une course sans allure."""
    rows = [("running", 300, 60)] * 20 + [("kitesurfing", 0, 120), ("kitesurfing", 0, 240), ("running", 0, 30)]
    df = _acts(rows)
    df.loc[20, "trainingLoad"] = 100.0
    df.loc[21, "trainingLoad"] = 450.0            # 4 h : plafond PMC à 400
    return df


def test_filtered_view_shows_the_pmc_tss():
    """Revue #1 : wing à 100 = 200 TSS dans le PMC, mais 50 sur la page filtrée « wing »."""
    from next_session_logic import daily_tss
    df = _mixed_history()
    wing_only = df[df["activityType"] == "kitesurfing"]
    enriched = enrich(wing_only, 300, history=df)
    daily = daily_tss(df, 300).set_index("day")
    for idx, row in enriched.iterrows():
        day = pd.Timestamp(row["startTimeLocal"]).normalize()
        assert row["tss"] == pytest.approx(daily.loc[day, "tss"])
    assert enriched.loc[21, "tss"] == 400                     # même plafond que le PMC
    # Sans historique, l'ancienne dérive reste visible (facteur de repli) : d'où history=df
    assert enrich(wing_only, 300).loc[20, "tss"] != pytest.approx(enriched.loc[20, "tss"])


def test_run_without_pace_has_no_tss_like_the_pmc():
    df = _mixed_history()
    assert pd.isna(enrich(df, 300).loc[22, "tss"])


def test_sum_of_activity_tss_equals_the_pmc_every_day():
    from next_session_logic import daily_tss
    df = _mixed_history()
    per_day = enrich(df, 300, history=df).groupby(
        pd.to_datetime(df["startTimeLocal"]).dt.normalize())["tss"].sum()
    daily = daily_tss(df, 300).set_index("day")["tss"]
    assert per_day[per_day > 0].to_dict() == pytest.approx(daily[daily > 0].to_dict())


@pytest.mark.parametrize("index", [
    [0, 0, 1] + list(range(2, 22)),                    # doublon en tête (levait ValueError)
    [0, 1, 1] + list(range(2, 22)),                    # course et vélo sous le même libellé (faux, en silence)
    list(range(22, -1, -1)),                           # désordonné
    [f"a{i}" for i in range(23)],                      # texte
])
def test_tss_does_not_depend_on_the_index(index):
    """Contre-revue : activity_tss / daily_tss rangeaient par libellé d'index."""
    from next_session_logic import activity_tss, daily_tss
    df = _mixed_history()
    ref_act = activity_tss(df, 300, calibration_df=df)
    ref_day = daily_tss(df, 300)
    odd = df.copy()
    odd.index = index
    got = activity_tss(odd, 300, calibration_df=odd)
    assert list(got.index) == index
    assert got.to_numpy() == pytest.approx(ref_act.to_numpy(), nan_ok=True)
    pd.testing.assert_frame_equal(daily_tss(odd, 300), ref_day)


def test_activity_tss_of_nothing():
    from next_session_logic import activity_tss, daily_tss
    assert activity_tss(None, 300).empty
    assert activity_tss(pd.DataFrame(), 300).empty
    assert daily_tss(None, 300).empty and daily_tss(pd.DataFrame(), 300).empty
