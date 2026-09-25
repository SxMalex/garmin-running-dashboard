"""Page Activités : intensité (IF), zones, charge, polarisation."""

from datetime import datetime, timedelta

import pandas as pd

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
