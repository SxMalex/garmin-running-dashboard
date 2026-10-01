"""Tests de la logique pure du comparatif annuel."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from comparatif_logic import (
    HRV_FIELDS,
    RESTING_HR_FIELDS,
    SLEEP_FIELDS,
    VO2MAX_FIELDS,
    add_year_doy,
    aligned_doy,
    cumulative_by_year,
    daily_sum_by_year,
    day_comparison,
    records_to_df,
    smooth_by_year,
    snapshot_at_doy,
    split_at_doy,
    year_summary,
)


# ---------------------------------------------------------------------------
# Alignement des années
# ---------------------------------------------------------------------------

def test_aligned_doy_premier_janvier_vaut_un():
    assert aligned_doy(pd.Series([pd.Timestamp("2026-01-01")])).iloc[0] == 1


def test_aligned_doy_aligne_le_1er_mars_sur_60_en_annee_bissextile():
    # 2024 est bissextile, 2025 non : le 1er mars doit tomber au même endroit.
    dates = pd.Series([pd.Timestamp("2024-03-01"), pd.Timestamp("2025-03-01")])
    assert aligned_doy(dates).tolist() == [60, 60]


def test_aligned_doy_29_fevrier_retombe_sur_le_28():
    dates = pd.Series([pd.Timestamp("2024-02-28"), pd.Timestamp("2024-02-29")])
    assert aligned_doy(dates).tolist() == [59, 59]


def test_aligned_doy_fin_dannee_alignee():
    dates = pd.Series([pd.Timestamp("2024-12-31"), pd.Timestamp("2025-12-31")])
    assert aligned_doy(dates).tolist() == [365, 365]


def test_add_year_doy_sur_df_vide_cree_les_colonnes():
    out = add_year_doy(pd.DataFrame(columns=["date"]))
    assert list(out.columns) == ["date", "year", "doy"]
    assert out.empty


# ---------------------------------------------------------------------------
# Conversion des réponses Garmin
# ---------------------------------------------------------------------------

def test_records_to_df_mappe_les_champs_et_ajoute_year_doy():
    records = [
        {"calendarDate": "2026-01-02", "sleepTimeSeconds": 28800, "sleepScoreValue": 90},
        {"calendarDate": "2026-01-03", "sleepTimeSeconds": 25200, "sleepScoreValue": 71},
    ]
    df = records_to_df(records, SLEEP_FIELDS)
    assert df["sleep_sec"].tolist() == [28800, 25200]
    assert df["sleep_score"].tolist() == [90, 71]
    assert df["year"].tolist() == [2026, 2026]
    assert df["doy"].tolist() == [2, 3]


def test_records_to_df_ignore_les_entrees_sans_date_ni_dict():
    records = [{"sleepTimeSeconds": 1}, None, "bruit", {"calendarDate": "2026-05-01"}]
    df = records_to_df(records, SLEEP_FIELDS)
    assert len(df) == 1
    assert pd.isna(df["sleep_sec"].iloc[0])


def test_records_to_df_force_le_numerique():
    df = records_to_df(
        [{"calendarDate": "2026-05-01", "restingHR": "52"},
         {"calendarDate": "2026-05-02", "restingHR": "n/a"}],
        RESTING_HR_FIELDS,
    )
    assert df["resting_hr"].iloc[0] == 52
    assert pd.isna(df["resting_hr"].iloc[1])


def test_records_to_df_deduplique_sur_la_derniere_valeur():
    df = records_to_df(
        [{"calendarDate": "2026-05-01", "lastNightAvg": 50},
         {"calendarDate": "2026-05-01", "lastNightAvg": 61}],
        HRV_FIELDS,
    )
    assert len(df) == 1
    assert df["hrv"].iloc[0] == 61


def test_records_to_df_vide_expose_les_colonnes_attendues():
    df = records_to_df([], VO2MAX_FIELDS)
    assert df.empty
    assert "vo2max" in df.columns and "doy" in df.columns


# ---------------------------------------------------------------------------
# Lissage
# ---------------------------------------------------------------------------

def test_smooth_by_year_moyenne_glissante_par_annee():
    df = pd.DataFrame({
        "year": [2025, 2025, 2025, 2026, 2026],
        "doy": [1, 2, 3, 1, 2],
        "v": [10.0, 20.0, 30.0, 100.0, 200.0],
    })
    out = smooth_by_year(df, "v", window=2)
    # Chaque année repart de zéro : 2026 ne récupère pas la fin de 2025.
    assert out.loc[out["year"] == 2026, "v_smooth"].tolist() == [100.0, 150.0]
    assert out.loc[out["year"] == 2025, "v_smooth"].tolist() == [10.0, 15.0, 25.0]


def test_smooth_by_year_premiers_jours_non_nuls():
    df = pd.DataFrame({"year": [2026], "doy": [1], "v": [42.0]})
    assert smooth_by_year(df, "v", window=7)["v_smooth"].iloc[0] == 42.0


def test_smooth_by_year_out_col_personnalise():
    df = pd.DataFrame({"year": [2026, 2026], "doy": [1, 2], "v": [1.0, 3.0]})
    out = smooth_by_year(df, "v", window=2, out_col="pred")
    assert out["pred"].tolist() == [1.0, 2.0]


# ---------------------------------------------------------------------------
# Cumuls
# ---------------------------------------------------------------------------

def _runs(dates: list[str], km: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "startTimeLocal": pd.to_datetime(dates),
        "distance_km": km,
        "duration_min": [k * 6 for k in km],
        "elevationGain": [10.0] * len(km),
        "avgHR": [140.0] * len(km),
        "avgPace_sec": [360.0] * len(km),
    })


def test_daily_sum_by_year_agrege_les_sorties_du_meme_jour():
    df = _runs(["2026-01-05 08:00", "2026-01-05 18:00", "2026-01-06 09:00"], [5.0, 3.0, 10.0])
    out = daily_sum_by_year(df, "distance_km")
    assert out["distance_km"].tolist() == [8.0, 10.0]
    assert out["doy"].tolist() == [5, 6]


def test_cumulative_by_year_remplit_les_jours_sans_activite():
    daily = daily_sum_by_year(_runs(["2026-01-01", "2026-01-04"], [10.0, 5.0]), "distance_km")
    cumul = cumulative_by_year(daily, "distance_km")
    assert cumul["doy"].tolist() == [1, 2, 3, 4]
    assert cumul["cumul"].tolist() == [10.0, 10.0, 10.0, 15.0]


def test_cumulative_by_year_last_doy_prolonge_la_courbe():
    daily = daily_sum_by_year(_runs(["2026-01-01"], [10.0]), "distance_km")
    cumul = cumulative_by_year(daily, "distance_km", last_doy={2026: 5})
    assert cumul["doy"].tolist() == [1, 2, 3, 4, 5]
    assert cumul["cumul"].iloc[-1] == 10.0


def test_cumulative_by_year_ne_tronque_jamais_une_activite():
    # last_doy plus court que la dernière sortie : on garde quand même la sortie.
    daily = daily_sum_by_year(_runs(["2026-01-10"], [7.0]), "distance_km")
    cumul = cumulative_by_year(daily, "distance_km", last_doy={2026: 3})
    assert cumul["doy"].max() == 10
    assert cumul["cumul"].iloc[-1] == 7.0


def test_cumulative_by_year_separe_les_annees():
    daily = daily_sum_by_year(_runs(["2025-01-01", "2026-01-01"], [4.0, 9.0]), "distance_km")
    cumul = cumulative_by_year(daily, "distance_km")
    assert cumul.loc[cumul["year"] == 2025, "cumul"].tolist() == [4.0]
    assert cumul.loc[cumul["year"] == 2026, "cumul"].tolist() == [9.0]


def test_cumulative_by_year_vide():
    assert cumulative_by_year(pd.DataFrame(), "distance_km").empty


# ---------------------------------------------------------------------------
# Instantané « à la même date »
# ---------------------------------------------------------------------------

def _series() -> pd.DataFrame:
    return pd.DataFrame({
        "year": [2025, 2025, 2026, 2026],
        "doy": [100, 110, 100, 105],
        "v": [40.0, 44.0, 48.0, 49.0],
    })


def test_snapshot_at_doy_prend_la_derniere_valeur_de_chaque_annee():
    assert snapshot_at_doy(_series(), "v", 110) == {2025: 44.0, 2026: 49.0}


def test_snapshot_at_doy_respecte_la_tolerance():
    # À doy 108 avec 2 jours de tolérance : 2026 (doy 105) sort de la fenêtre.
    assert snapshot_at_doy(_series(), "v", 108, tolerance=2) == {}


def test_snapshot_at_doy_ignore_les_nan():
    df = pd.DataFrame({"year": [2026, 2026], "doy": [10, 11], "v": [5.0, np.nan]})
    assert snapshot_at_doy(df, "v", 11) == {2026: 5.0}


def test_snapshot_at_doy_colonne_absente_ou_vide():
    assert snapshot_at_doy(_series(), "inconnue", 100) == {}
    assert snapshot_at_doy(pd.DataFrame(), "v", 100) == {}


# ---------------------------------------------------------------------------
# Bilan par année
# ---------------------------------------------------------------------------

def test_year_summary_tronque_au_meme_jour_de_lannee():
    df = _runs(
        ["2025-01-05", "2025-06-01", "2026-01-05"],
        [10.0, 20.0, 12.0],
    )
    summary = year_summary(df, doy_limit=10)
    assert summary["year"].tolist() == [2026, 2025]
    # La sortie de juin 2025 est hors fenêtre : 2025 ne compte que ses 10 km.
    assert summary.loc[summary["year"] == 2025, "km"].iloc[0] == 10.0


def test_year_summary_allure_ponderee_par_la_distance():
    df = pd.DataFrame({
        "startTimeLocal": pd.to_datetime(["2026-01-01", "2026-01-02"]),
        "distance_km": [10.0, 5.0],
        "duration_min": [60.0, 25.0],
        "elevationGain": [0.0, 0.0],
        "avgHR": [140.0, 150.0],
    })
    summary = year_summary(df, doy_limit=10)
    # 85 min pour 15 km = 340 s/km, et non la moyenne des deux allures (325).
    assert summary["pace_sec"].iloc[0] == pytest.approx(340.0)
    assert summary["heures"].iloc[0] == pytest.approx(1.4, abs=0.05)


def test_year_summary_tolere_les_champs_manquants():
    df = pd.DataFrame({
        "startTimeLocal": pd.to_datetime(["2026-02-01"]),
        "distance_km": [8.0],
        "duration_min": [48.0],
        "elevationGain": [np.nan],
        "avgHR": [np.nan],
    })
    summary = year_summary(df, doy_limit=60)
    assert summary["denivele"].iloc[0] == 0.0
    assert pd.isna(summary["fc_moy"].iloc[0])


def test_year_summary_vide_ou_hors_fenetre():
    assert year_summary(pd.DataFrame(), 100).empty
    assert year_summary(_runs(["2026-06-01"], [10.0]), doy_limit=10).empty


# ---------------------------------------------------------------------------
# Découpe passé / futur
# ---------------------------------------------------------------------------

def test_split_at_doy_courbe_jointive():
    df = pd.DataFrame({"year": [2025] * 4, "doy": [1, 2, 3, 4], "v": [1.0, 2.0, 3.0, 4.0]})
    past, future = split_at_doy(df, 2)
    assert past["doy"].tolist() == [1, 2]
    # Le point de coupe appartient aux deux morceaux : pas de trou à l'écran.
    assert future["doy"].tolist() == [2, 3, 4]


def test_split_at_doy_vide():
    past, future = split_at_doy(pd.DataFrame(), 100)
    assert past.empty and future.empty


# ---------------------------------------------------------------------------
# Comparaison de la sortie du jour
# ---------------------------------------------------------------------------

def _day_runs(rows: list[dict]) -> pd.DataFrame:
    """Construit un DataFrame de sorties depuis (datetime, km, minutes, …)."""
    df = pd.DataFrame(rows)
    df["startTimeLocal"] = pd.to_datetime(df["startTimeLocal"])
    return df


def test_day_comparison_une_sortie_par_annee():
    df = _day_runs([
        {"startTimeLocal": "2026-08-13 07:00", "distance_km": 6.0, "duration_min": 42.0,
         "avgHR": 140.0, "maxHR": 170.0, "elevationGain": 70.0, "avgCadence": 165.0,
         "trainingLoad": 50.0, "calories": 470, "activityName": "Sortie A"},
        {"startTimeLocal": "2025-08-13 20:00", "distance_km": 3.0, "duration_min": 22.0,
         "avgHR": 140.0, "maxHR": 155.0, "elevationGain": 12.0, "avgCadence": 153.0,
         "trainingLoad": 26.0, "calories": 216, "activityName": "Sortie B"},
    ])
    out = day_comparison(df, date(2026, 8, 13))
    assert out["year"].tolist() == [2026, 2025]
    assert out.loc[0, "km"] == 6.0
    assert out.loc[0, "start_time"] == "07:00"
    assert out.loc[0, "names"] == ["Sortie A"]
    assert out.loc[1, "start_time"] == "20:00"


def test_day_comparison_agrege_plusieurs_sorties_du_meme_jour():
    df = _day_runs([
        {"startTimeLocal": "2026-05-01 08:00", "distance_km": 10.0, "duration_min": 60.0,
         "avgHR": 150.0, "maxHR": 170.0, "elevationGain": 50.0, "avgCadence": 170.0,
         "trainingLoad": 80.0, "calories": 600, "activityName": "Matin"},
        {"startTimeLocal": "2026-05-01 18:00", "distance_km": 5.0, "duration_min": 30.0,
         "avgHR": 130.0, "maxHR": 145.0, "elevationGain": 10.0, "avgCadence": 160.0,
         "trainingLoad": 30.0, "calories": 300, "activityName": "Soir"},
    ])
    out = day_comparison(df, date(2026, 5, 1))
    assert len(out) == 1
    assert out.loc[0, "sorties"] == 2
    assert out.loc[0, "km"] == 15.0
    assert out.loc[0, "minutes"] == 90.0
    # Allure recalculée sur les totaux : 90 min pour 15 km = 360 s/km.
    assert out.loc[0, "pace_sec"] == pytest.approx(360.0)
    # FC pondérée par la durée : (150×60 + 130×30) / 90 = 143,3.
    assert out.loc[0, "avgHR"] == pytest.approx(143.33, abs=0.01)
    assert out.loc[0, "maxHR"] == 170.0
    assert out.loc[0, "elevation"] == 60.0
    assert out.loc[0, "trainingLoad"] == 110.0
    assert out.loc[0, "names"] == ["Matin", "Soir"]


def test_day_comparison_prend_le_depart_de_la_premiere_sortie():
    df = _day_runs([
        {"startTimeLocal": "2026-05-01 18:00", "distance_km": 5.0, "duration_min": 30.0,
         "activityName": "Soir"},
        {"startTimeLocal": "2026-05-01 06:30", "distance_km": 8.0, "duration_min": 48.0,
         "activityName": "Matin"},
    ])
    out = day_comparison(df, date(2026, 5, 1))
    assert out.loc[0, "start_time"] == "06:30"
    assert out.loc[0, "names"] == ["Matin", "Soir"]


def test_day_comparison_annee_sans_sortie_absente():
    df = _day_runs([
        {"startTimeLocal": "2026-08-13 07:00", "distance_km": 6.0, "duration_min": 40.0},
        {"startTimeLocal": "2025-08-20 07:00", "distance_km": 6.0, "duration_min": 40.0},
    ])
    out = day_comparison(df, date(2026, 8, 13))
    assert out["year"].tolist() == [2026]


def test_day_comparison_aligne_les_annees_bissextiles():
    # 15 mars : doy brut 75 en 2024 (bissextile), 74 en 2025 — l'alignement doit
    # ramener les deux sur le même jour.
    df = _day_runs([
        {"startTimeLocal": "2024-03-15 09:00", "distance_km": 5.0, "duration_min": 30.0},
        {"startTimeLocal": "2025-03-15 09:00", "distance_km": 7.0, "duration_min": 42.0},
    ])
    out = day_comparison(df, date(2025, 3, 15))
    assert sorted(out["year"].tolist()) == [2024, 2025]


def test_day_comparison_28_fevrier_n_agrege_pas_le_29():
    """
    Repro fuzz repro_leap_day : `aligned_doy` fait exprès retomber le 29/02
    d'une année bissextile sur la même valeur que le 28 (pour superposer les
    courbes) — mais day_comparison doit filtrer sur le (mois, jour) RÉEL,
    sinon le 28/02 d'une année non bissextile agrège aussi le 29/02 d'une
    année bissextile, deux jours calendaires distincts.
    """
    df = _day_runs([
        {"startTimeLocal": "2024-02-28 08:00", "distance_km": 10.0, "duration_min": 55.0,
         "activityName": "28 fev bissextile"},
        {"startTimeLocal": "2024-02-29 18:00", "distance_km": 21.1, "duration_min": 110.0,
         "activityName": "29 fev bissextile"},
        {"startTimeLocal": "2025-02-28 08:00", "distance_km": 8.0, "duration_min": 45.0,
         "activityName": "28 fev non bissextile"},
    ])
    out = day_comparison(df, date(2025, 2, 28))
    assert sorted(out["year"].tolist()) == [2024, 2025]
    row_2024 = out[out["year"] == 2024].iloc[0]
    assert row_2024["sorties"] == 1
    assert row_2024["km"] == 10.0
    assert row_2024["names"] == ["28 fev bissextile"]


def _leap_runs() -> pd.DataFrame:
    """Sorties autour de la fin février, années bissextiles (2024, 2028) et non (2025)."""
    return _day_runs([
        {"startTimeLocal": "2024-02-28 23:59", "distance_km": 10.0, "duration_min": 55.0,
         "activityName": "28/02/2024"},
        {"startTimeLocal": "2024-02-29 00:00", "distance_km": 21.1, "duration_min": 110.0,
         "activityName": "29/02/2024"},
        {"startTimeLocal": "2024-03-01 07:00", "distance_km": 6.0, "duration_min": 33.0,
         "activityName": "01/03/2024"},
        {"startTimeLocal": "2025-02-28 08:00", "distance_km": 8.0, "duration_min": 45.0,
         "activityName": "28/02/2025"},
        {"startTimeLocal": "2025-03-01 08:00", "distance_km": 7.0, "duration_min": 40.0,
         "activityName": "01/03/2025"},
        {"startTimeLocal": "2028-02-28 18:00", "distance_km": 5.0, "duration_min": 28.0,
         "activityName": "28/02/2028"},
        {"startTimeLocal": "2028-02-29 07:30", "distance_km": 12.0, "duration_min": 66.0,
         "activityName": "29/02/2028"},
    ])


def test_day_comparison_29_fevrier_trouve_les_29_fevrier():
    """
    Revue PR 1 : le 29/02/2028, la page comparait le 28 février (le doy aligné
    du 29 vaut celui du 28) et excluait la sortie du jour. Le 29 ne trouve que
    les 29 — y compris la sortie de minuit pile — et jamais le 28 ni le 1er mars.
    """
    out = day_comparison(_leap_runs(), date(2028, 2, 29))
    assert out["year"].tolist() == [2028, 2024]
    assert [n for names in out["names"] for n in names] == ["29/02/2028", "29/02/2024"]
    assert out["km"].tolist() == [12.0, 21.1]


def test_day_comparison_1er_mars_ne_prend_pas_le_29_fevrier():
    out = day_comparison(_leap_runs(), date(2028, 3, 1))
    assert sorted(out["year"].tolist()) == [2024, 2025]
    assert sorted(n for names in out["names"] for n in names) == ["01/03/2024", "01/03/2025"]


def test_day_comparison_28_fevrier_bissextile_et_minuit():
    """Le 28 (année bissextile consultée) : la sortie de 23:59 compte, celle de 00:00 le 29 non."""
    out = day_comparison(_leap_runs(), date(2028, 2, 28))
    assert out["year"].tolist() == [2028, 2025, 2024]
    assert out[out["year"] == 2024].iloc[0]["names"] == ["28/02/2024"]


def test_day_comparison_31_decembre_bissextile():
    """Le 31/12 d'une année bissextile (jour 366) retrouve le 31/12 des autres années."""
    df = _day_runs([
        {"startTimeLocal": "2024-12-31 10:00", "distance_km": 10.0, "duration_min": 50.0},
        {"startTimeLocal": "2025-12-31 10:00", "distance_km": 5.0, "duration_min": 25.0},
        {"startTimeLocal": "2025-12-30 10:00", "distance_km": 9.0, "duration_min": 45.0},
    ])
    out = day_comparison(df, date(2024, 12, 31))
    assert out["year"].tolist() == [2025, 2024]
    assert out["km"].tolist() == [5.0, 10.0]


def test_day_comparison_tolere_les_colonnes_absentes():
    df = _day_runs([
        {"startTimeLocal": "2026-08-13 07:00", "distance_km": 6.0, "duration_min": 40.0},
    ])
    out = day_comparison(df, date(2026, 8, 13))
    assert pd.isna(out.loc[0, "avgHR"])
    assert out.loc[0, "elevation"] == 0.0
    assert out.loc[0, "names"] == []


def test_day_comparison_km_nul_ne_divise_pas_par_zero():
    df = _day_runs([
        {"startTimeLocal": "2026-08-13 07:00", "distance_km": 0.0, "duration_min": 30.0},
    ])
    assert pd.isna(day_comparison(df, date(2026, 8, 13)).loc[0, "pace_sec"])


def test_day_comparison_vide():
    assert day_comparison(pd.DataFrame(), date(2026, 8, 13)).empty
    assert list(day_comparison(pd.DataFrame(), date(2026, 8, 13)).columns)[:4] == [
        "year", "date", "start_time", "sorties",
    ]
