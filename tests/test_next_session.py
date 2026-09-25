"""
Tests des fonctions de recommandation de séance et de génération GPX.
"""

import xml.etree.ElementTree as ET
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from datetime import date, datetime, timedelta

import next_session_logic as logic
from next_session_logic import (
    compute_tsb as _compute_tsb,
    compute_pmc_series as _compute_pmc_series,
    cross_training_factor as _cross_training_factor,
    daily_tss as _daily_tss,
    recommend_session as _recommend_session,
    build_gpx as _build_gpx,
    parse_ors_route as _parse_ors_route,
    suggest_next_date as _suggest_next_date,
    format_date_fr as _format_date_fr,
    reference_threshold_sec as _reference_threshold_sec,
    THRESHOLD_SLIDER_MAX,
    THRESHOLD_SLIDER_MIN,
    THRESHOLD_SLIDER_STEP,
    CROSS_TRAINING_FALLBACK_K,
    CROSS_TRAINING_K_MAX,
    CROSS_TRAINING_K_MIN,
)


# ===========================================================================
# _parse_ors_route
# ===========================================================================

class TestParseOrsRoute:
    def test_valid_response(self, sample_ors_geojson):
        result = _parse_ors_route(sample_ors_geojson)
        assert result is not None
        assert result["distance_km"] == pytest.approx(5.20, abs=0.01)
        assert result["ascent_m"] == 45
        assert len(result["lats"]) == 4
        assert len(result["lons"]) == 4
        assert len(result["elevations"]) == 4

    def test_elevations_extracted(self, sample_ors_geojson):
        result = _parse_ors_route(sample_ors_geojson)
        assert result["elevations"][0] == pytest.approx(120.0)

    def test_2d_coords_no_elevation(self, sample_ors_geojson):
        geojson = sample_ors_geojson.copy()
        geojson["features"][0]["geometry"]["coordinates"] = [
            [2.35, 48.85],
            [2.36, 48.86],
        ]
        result = _parse_ors_route(geojson)
        assert result["elevations"] == []

    def test_missing_ascent_defaults_zero(self, sample_ors_geojson):
        geojson = sample_ors_geojson.copy()
        del geojson["features"][0]["properties"]["ascent"]
        result = _parse_ors_route(geojson)
        assert result["ascent_m"] == 0

    def test_none_ascent_defaults_zero(self, sample_ors_geojson):
        geojson = sample_ors_geojson.copy()
        geojson["features"][0]["properties"]["ascent"] = None
        result = _parse_ors_route(geojson)
        assert result["ascent_m"] == 0

    def test_missing_features_returns_none(self):
        assert _parse_ors_route({}) is None

    def test_empty_features_returns_none(self):
        assert _parse_ors_route({"features": []}) is None

    def test_missing_summary_returns_none(self, sample_ors_geojson):
        geojson = sample_ors_geojson.copy()
        del geojson["features"][0]["properties"]["summary"]
        assert _parse_ors_route(geojson) is None

    def test_empty_coords_returns_none(self):
        assert _parse_ors_route({
            "features": [{
                "geometry": {"coordinates": []},
                "properties": {
                    "summary": {"distance": 5000.0, "duration": 1800.0},
                    "ascent": 10,
                },
            }]
        }) is None

    def test_missing_geometry_returns_none(self):
        assert _parse_ors_route({
            "features": [{
                "properties": {
                    "summary": {"distance": 5000.0, "duration": 1800.0},
                }
            }]
        }) is None


# ===========================================================================
# _build_gpx
# ===========================================================================

class TestBuildGpx:
    def test_valid_xml(self, sample_route):
        gpx = _build_gpx(sample_route, "Endurance", "5:30/km")
        ET.fromstring(gpx)  # lève une exception si XML invalide

    def test_starts_with_xml_declaration(self, sample_route):
        gpx = _build_gpx(sample_route, "Endurance", "5:30/km")
        assert gpx.startswith("<?xml")

    def test_contains_trkpt(self, sample_route):
        gpx = _build_gpx(sample_route, "Endurance", "5:30/km")
        assert "<trkpt" in gpx

    def test_trkpt_count_matches_coords(self, sample_route):
        gpx = _build_gpx(sample_route, "Endurance", "5:30/km")
        assert gpx.count("<trkpt") == len(sample_route["lats"])

    def test_elevation_tags_present(self, sample_route):
        gpx = _build_gpx(sample_route, "Endurance", "5:30/km")
        assert "<ele>" in gpx
        assert "120.0" in gpx

    def test_no_elevation_no_ele_tag(self, sample_route):
        route = {**sample_route, "elevations": []}
        gpx = _build_gpx(route, "Endurance", "5:30/km")
        assert "<ele>" not in gpx

    def test_session_label_in_output(self, sample_route):
        gpx = _build_gpx(sample_route, "Sortie longue", "5:00/km")
        assert "Sortie longue" in gpx

    def test_pace_in_description(self, sample_route):
        gpx = _build_gpx(sample_route, "Tempo", "4:45/km")
        assert "4:45/km" in gpx

    def test_session_label_with_ampersand_is_escaped(self, sample_route):
        """Un nom de séance avec un caractère XML spécial ne doit pas casser le
        GPX (ex. « Tempo & Strides » depuis parse_workout_target)."""
        gpx = _build_gpx(sample_route, "Tempo & Strides", "4:45/km")
        assert "Tempo & Strides" not in gpx  # non échappé tel quel
        assert "Tempo &amp; Strides" in gpx
        ET.fromstring(gpx)  # toujours un XML bien formé

    def test_ors_attribution_in_metadata(self, sample_route):
        """Conformité CGU ORS / licence ODbL OpenStreetMap : attribution dans
        les métadonnées du GPX exporté (le parcours vient toujours d'ORS)."""
        ns = {"gpx": "http://www.topografix.com/GPX/1/1"}
        gpx = _build_gpx(sample_route, "Endurance", "5:30/km")
        root = ET.fromstring(gpx)
        metadata = root.find("gpx:metadata", ns)
        assert metadata is not None
        copyright_el = metadata.find("gpx:copyright", ns)
        assert copyright_el is not None
        assert copyright_el.attrib["author"] == "OpenStreetMap contributors"
        desc = metadata.find("gpx:desc", ns)
        assert desc is not None
        assert "openrouteservice.org" in desc.text
        assert "OpenStreetMap" in desc.text


# ===========================================================================
# build_gpx — round-trip coordinate fidelity
# ===========================================================================

class TestBuildGpxRoundtrip:
    NS = {"gpx": "http://www.topografix.com/GPX/1/1"}

    def test_lat_lon_survive_roundtrip(self, sample_route):
        gpx = _build_gpx(sample_route, "Endurance", "5:30/km")
        root = ET.fromstring(gpx)
        trkpts = root.findall(".//gpx:trkpt", self.NS)
        assert len(trkpts) == len(sample_route["lats"])
        for i, pt in enumerate(trkpts):
            assert float(pt.attrib["lat"]) == pytest.approx(sample_route["lats"][i], abs=1e-5)
            assert float(pt.attrib["lon"]) == pytest.approx(sample_route["lons"][i], abs=1e-5)

    def test_elevations_survive_roundtrip(self, sample_route):
        gpx = _build_gpx(sample_route, "Endurance", "5:30/km")
        root = ET.fromstring(gpx)
        ele_tags = root.findall(".//gpx:trkpt/gpx:ele", self.NS)
        assert len(ele_tags) == len(sample_route["elevations"])
        for i, ele in enumerate(ele_tags):
            assert float(ele.text) == pytest.approx(sample_route["elevations"][i], abs=0.1)

    def test_no_elevations_produces_no_ele_tags(self, sample_route):
        route = {**sample_route, "elevations": []}
        gpx = _build_gpx(route, "Endurance", "5:30/km")
        root = ET.fromstring(gpx)
        assert root.findall(".//gpx:trkpt/gpx:ele", self.NS) == []


# ===========================================================================
# reference_threshold_sec — seuil unique partagé par toute l'application
# ===========================================================================

class TestReferenceThresholdSec:
    def test_percentile_des_sorties_longues(self, make_running_df):
        # Toutes à 340 s/km sur 10 km → le percentile tombe sur 340, qui est
        # déjà sur la grille de 5 s.
        df = make_running_df(n=10, pace_sec=340.0, distance_km=10.0)
        assert _reference_threshold_sec(df) == 340

    def test_arrondi_sur_la_grille_du_curseur(self, make_running_df):
        df = make_running_df(n=10, pace_sec=338.0, distance_km=10.0)
        seuil = _reference_threshold_sec(df)
        assert seuil == 340
        assert (seuil - THRESHOLD_SLIDER_MIN) % THRESHOLD_SLIDER_STEP == 0

    def test_arrondi_vers_le_bas(self, make_running_df):
        df = make_running_df(n=10, pace_sec=336.0, distance_km=10.0)
        assert _reference_threshold_sec(df) == 335

    def test_borne_dans_la_plage_du_curseur(self, make_running_df):
        rapide = make_running_df(n=10, pace_sec=120.0, distance_km=10.0)
        lent = make_running_df(n=10, pace_sec=900.0, distance_km=10.0)
        assert _reference_threshold_sec(rapide) == THRESHOLD_SLIDER_MIN
        assert _reference_threshold_sec(lent) == THRESHOLD_SLIDER_MAX

    def test_valeur_par_defaut_sans_sortie_longue(self, make_running_df):
        df = make_running_df(n=10, distance_km=5.0)
        assert _reference_threshold_sec(df) == 330

    def test_df_vide_ou_sans_colonne(self):
        assert _reference_threshold_sec(pd.DataFrame()) == 330
        assert _reference_threshold_sec(pd.DataFrame({"distance_km": [10.0]})) == 330

    def test_compute_tsb_utilise_ce_seuil(self, make_running_df):
        """Le CTL de compute_tsb doit être celui du seuil de référence."""
        df = make_running_df(n=20, days_apart=2, pace_sec=338.0, distance_km=10.0)
        seuil = _reference_threshold_sec(df)
        ctl_direct = _compute_pmc_series(df, seuil).iloc[-1]["ctl"]
        ctl_tsb = _compute_tsb(df)[0]
        assert round(ctl_direct, 1) == ctl_tsb


# ===========================================================================
# _compute_tsb
# ===========================================================================

class TestComputeTsb:
    def test_empty_df_returns_zeros(self):
        ctl, atl, tsb = _compute_tsb(pd.DataFrame())
        assert (ctl, atl, tsb) == (0.0, 0.0, 0.0)

    def test_zero_pace_returns_zeros(self, make_running_df):
        df = make_running_df(days_apart=4, pace_sec=0.0, with_location=False)
        ctl, atl, tsb = _compute_tsb(df)
        assert (ctl, atl, tsb) == (0.0, 0.0, 0.0)

    def test_returns_floats(self, make_running_df):
        df = make_running_df(days_apart=4, with_location=False)
        ctl, atl, tsb = _compute_tsb(df)
        assert all(isinstance(v, float) for v in (ctl, atl, tsb))

    def test_ctl_positive_with_training(self, make_running_df):
        df = make_running_df(n=15, days_apart=4, with_location=False)
        ctl, atl, tsb = _compute_tsb(df)
        assert ctl > 0

    def test_tsb_close_to_ctl_minus_atl(self, make_running_df):
        df = make_running_df(days_apart=4, with_location=False)
        ctl, atl, tsb = _compute_tsb(df)
        # TSB peut différer de ctl-atl de ±0.2 à cause des arrondis indépendants
        assert abs(tsb - (ctl - atl)) < 0.2

    def test_heavy_recent_load_gives_negative_tsb(self, make_running_df):
        # Toutes les sorties dans les 3 derniers jours → ATL > CTL
        df = make_running_df(n=10, days_apart=0, with_location=False)
        ctl, atl, tsb = _compute_tsb(df)
        assert atl > ctl
        assert tsb < 0

    def test_single_activity(self, make_running_df):
        df = make_running_df(n=1, days_apart=4, with_location=False)
        ctl, atl, tsb = _compute_tsb(df)
        assert ctl > 0 or atl > 0  # au moins un non-nul


# ===========================================================================
# _recommend_session
# ===========================================================================

class TestRecommendSession:
    def test_return_keys(self, make_running_df):
        df = make_running_df()
        result = _recommend_session(df)
        for key in ["session_key", "session", "ctl", "atl", "tsb",
                    "target_dist_km", "target_pace_sec", "target_pace_str",
                    "target_elev", "duration_min", "avg_dist",
                    "suggested_date", "suggested_date_str"]:
            assert key in result

    def test_suggested_date_is_date_object(self, make_running_df):
        df = make_running_df()
        result = _recommend_session(df)
        assert isinstance(result["suggested_date"], date)

    def test_suggested_date_str_is_string(self, make_running_df):
        df = make_running_df()
        result = _recommend_session(df)
        assert isinstance(result["suggested_date_str"], str)
        assert len(result["suggested_date_str"]) > 0

    def test_tempo_when_fresh_and_recent_long(self):
        # TSB > 10 et sortie longue il y a 3 jours (< 6) → tempo
        now = datetime.now()
        # Une sortie longue récente + sorties standard
        rows = [{"startTimeLocal": now - timedelta(days=3), "distance_km": 18.0,
                 "duration_min": 100.0, "avgPace_sec": 330.0, "avgHR": 148.0,
                 "elevationGain": 80.0, "activityName": "Long", "startLat": 48.85,
                 "startLon": 2.35, "activityId": 0, "activityType": "running"}]
        rows += [
            {"startTimeLocal": now - timedelta(days=5 + i * 2), "distance_km": 10.0,
             "duration_min": 55.0, "avgPace_sec": 330.0, "avgHR": 148.0,
             "elevationGain": 60.0, "activityName": f"Run {i}", "startLat": 48.85,
             "startLon": 2.35, "activityId": i + 1, "activityType": "running"}
            for i in range(9)
        ]
        df = pd.DataFrame(rows)
        df["startTimeLocal"] = pd.to_datetime(df["startTimeLocal"])
        with patch.object(logic, "compute_tsb", return_value=(50.0, 38.0, 12.0)):
            result = _recommend_session(df)
        assert result["session_key"] == "tempo"

    def test_target_dist_minimum(self, make_running_df):
        # Même avec un facteur faible le minimum est 3 km
        df = make_running_df()
        with patch.object(logic, "compute_tsb", return_value=(-30.0, 10.0, -40.0)):
            result = _recommend_session(df)
        assert result["target_dist_km"] >= 3.0

    def test_recuperation_when_fatigue(self, make_running_df):
        df = make_running_df()
        with patch.object(logic, "compute_tsb", return_value=(80.0, 110.0, -30.0)):
            result = _recommend_session(df)
        assert result["session_key"] == "recuperation"

    def test_sortie_longue_when_fresh_and_no_recent_long(self, make_running_df):
        df = make_running_df(days_apart=7)  # espacement → pas de sortie longue récente
        with patch.object(logic, "compute_tsb", return_value=(50.0, 38.0, 12.0)):
            result = _recommend_session(df)
        assert result["session_key"] in ("sortie_longue", "tempo")

    def test_endurance_when_days_since_high(self):
        # Dernière sortie il y a 6 jours → override vers endurance
        now = datetime.now()
        rows = [
            {
                "startTimeLocal": now - timedelta(days=6),
                "activityType": "running",
                "distance_km": 10.0,
                "duration_min": 55.0,
                "avgPace_sec": 330.0,
                "avgHR": 148.0,
                "elevationGain": 60.0,
                "activityName": "Old run",
                "startLat": 48.85,
                "startLon": 2.35,
                "activityId": 1,
            }
        ]
        df = pd.DataFrame(rows)
        df["startTimeLocal"] = pd.to_datetime(df["startTimeLocal"])
        with patch.object(logic, "compute_tsb", return_value=(50.0, 38.0, 12.0)):
            result = _recommend_session(df)
        assert result["session_key"] == "endurance"

    def test_days_since_is_calendar_based_not_24h_blocks(self):
        """Repro fuzz repro_days_since : une sortie d'hier doit compter « il y a
        1 jour » qu'on la consulte tôt le matin ou tard le soir — days_since se
        calcule en jours calendaires, pas en blocs de 24 h."""
        today = date.today()
        yesterday_19h = (
            datetime.combine(today - timedelta(days=1), datetime.min.time())
            + timedelta(hours=19)
        )
        rows = [{
            "startTimeLocal": yesterday_19h, "activityType": "running",
            "distance_km": 10.0, "duration_min": 55.0, "avgPace_sec": 330.0,
            "avgHR": 148.0, "elevationGain": 60.0, "activityName": "Run",
            "startLat": 48.85, "startLon": 2.35, "activityId": 1,
        }]
        df = pd.DataFrame(rows)
        df["startTimeLocal"] = pd.to_datetime(df["startTimeLocal"])
        morning = datetime.combine(today, datetime.min.time()) + timedelta(hours=8)
        evening = datetime.combine(today, datetime.min.time()) + timedelta(hours=20)
        with patch.object(logic, "compute_tsb", return_value=(50.0, 38.0, 0.0)):
            seen_morning = _recommend_session(df, now=morning)
            seen_evening = _recommend_session(df, now=evening)
        assert seen_morning["days_since"] == 1
        assert seen_evening["days_since"] == 1

    def test_endurance_normal_tsb(self, make_running_df):
        df = make_running_df()
        with patch.object(logic, "compute_tsb", return_value=(40.0, 42.0, -2.0)):
            result = _recommend_session(df)
        assert result["session_key"] == "endurance"


# ===========================================================================
# format_date_fr
# ===========================================================================

class TestFormatDateFr:
    def test_today(self):
        assert _format_date_fr(date.today()) == "Aujourd'hui"

    def test_tomorrow(self):
        assert _format_date_fr(date.today() + timedelta(days=1)) == "Demain"

    def test_future_contains_day_name(self):
        d = date.today() + timedelta(days=7)
        result = _format_date_fr(d)
        assert any(j in result for j in logic._JOURS_FR)

    def test_future_contains_month_name(self):
        d = date.today() + timedelta(days=7)
        result = _format_date_fr(d)
        assert any(m in result for m in logic._MOIS_FR)

    def test_future_contains_day_number(self):
        d = date.today() + timedelta(days=7)
        result = _format_date_fr(d)
        assert str(d.day) in result

    def test_known_date(self):
        # 7 mai 2025 est un mercredi
        with patch.object(logic, "date") as mock_date:
            mock_date.today.return_value = date(2025, 5, 5)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            result = _format_date_fr(date(2025, 5, 7))
        assert result == "Mercredi 7 mai"


# ===========================================================================
# suggest_next_date
# ===========================================================================

class TestSuggestNextDate:
    def _days_until(self, df, session_key, days_since, tsb):
        result = _suggest_next_date(df, session_key, days_since, tsb)
        return (result - date.today()).days

    def test_returns_date_object(self, make_running_df):
        df = make_running_df(days_apart=2)
        result = _suggest_next_date(df, "endurance", 1, 0.0)
        assert isinstance(result, date)

    def test_never_in_past(self, make_running_df):
        # Même très reposé avec beaucoup de jours écoulés → au pire aujourd'hui
        df = make_running_df(days_apart=2)
        assert self._days_until(df, "endurance", 10, 15.0) == 0

    def test_typical_gap_respected(self, make_running_df):
        # 10 sorties tous les 3j → typical_gap=3, TSB neutre, endurance → 3j d'attente
        df = make_running_df(days_apart=3)
        assert self._days_until(df, "endurance", 0, 0.0) == 3

    def test_high_fatigue_delays(self, make_running_df):
        # TSB < -20 → +1j par rapport à TSB neutre
        df = make_running_df(days_apart=2)
        normal = self._days_until(df, "endurance", 0, 0.0)
        fatigued = self._days_until(df, "endurance", 0, -25.0)
        assert fatigued > normal

    def test_fresh_brings_forward(self, make_running_df):
        # TSB > 10 → -1j par rapport à TSB neutre
        df = make_running_df(days_apart=2)
        normal = self._days_until(df, "endurance", 0, 0.0)
        fresh = self._days_until(df, "endurance", 0, 15.0)
        assert fresh < normal

    def test_tempo_later_than_endurance(self, make_running_df):
        df = make_running_df(days_apart=2)
        assert self._days_until(df, "tempo", 0, 0.0) > self._days_until(df, "endurance", 0, 0.0)

    def test_sortie_longue_later_than_endurance(self, make_running_df):
        df = make_running_df(days_apart=2)
        assert self._days_until(df, "sortie_longue", 0, 0.0) > self._days_until(df, "endurance", 0, 0.0)

    def test_recuperation_earlier_than_endurance(self, make_running_df):
        df = make_running_df(days_apart=2)
        assert self._days_until(df, "recuperation", 0, 0.0) < self._days_until(df, "endurance", 0, 0.0)

    def test_already_rested_enough_returns_today(self, make_running_df):
        # days_since=5 avec typical_gap=2 → déjà assez reposé → aujourd'hui
        df = make_running_df(days_apart=2)
        assert self._days_until(df, "endurance", 5, 0.0) == 0

    def test_days_since_reduces_wait(self, make_running_df):
        # days_since=1 → 1j de moins que days_since=0
        df = make_running_df(days_apart=3)
        wait_0 = self._days_until(df, "endurance", 0, 0.0)
        wait_1 = self._days_until(df, "endurance", 1, 0.0)
        assert wait_1 == wait_0 - 1

    def test_fallback_few_runs(self, make_running_df):
        # Moins de 3 sorties → pas d'erreur, retourne une date valide
        df = make_running_df(n=2, days_apart=2)
        result = _suggest_next_date(df, "endurance", 0, 0.0)
        assert isinstance(result, date)
        assert result >= date.today()

    def test_target_gap_minimum_one(self, make_running_df):
        # récupération + frais → target_gap = max(1, 2-1-1) = max(1,0) = 1 → jamais 0j d'attente
        df = make_running_df(days_apart=2)
        assert self._days_until(df, "recuperation", 0, 15.0) >= 0


# ===========================================================================
# compute_pmc_series — modèle PMC (CTL/ATL/TSB) en série quotidienne
# ===========================================================================

class TestComputePmcSeries:
    """
    Couvre la dynamique exponentielle du PMC. Ces tests fixent le comportement
    des constantes de temps (k_ctl = exp(-1/42), k_atl = exp(-1/7)) afin qu'une
    inversion accidentelle des deux casse au moins un test.
    """

    def test_empty_df_returns_empty_series(self):
        result = _compute_pmc_series(pd.DataFrame(), threshold_sec=330.0)
        assert result.empty
        assert list(result.columns) == [
            "date", "tss", "tss_run", "tss_cross", "ctl", "atl", "tsb",
        ]

    def test_zero_pace_only_returns_empty(self, make_running_df):
        df = make_running_df(pace_sec=0.0, with_location=False)
        result = _compute_pmc_series(df, threshold_sec=330.0)
        assert result.empty

    def test_missing_avgpace_sec_column_returns_empty(self):
        # Cas robuste : DataFrame sans la colonne avgPace_sec
        df = pd.DataFrame([{"startTimeLocal": datetime.now(), "duration_min": 30.0}])
        result = _compute_pmc_series(df, threshold_sec=330.0)
        assert result.empty

    def test_single_activity_ctl_atl_start_at_zero_then_grow(self):
        """
        Une seule activité aujourd'hui :
        - le PMC démarre la série au jour de l'activité (récit borné par .min())
        - première ligne : ctl_v et atl_v incrémentés depuis 0 par la TSS du jour
        - TSB de la première ligne = ctl - atl, donc négatif (ATL monte plus vite)
        """
        df = pd.DataFrame([{
            "startTimeLocal": datetime.now(),
            "activityType": "running",
            "distance_km": 10.0,
            "duration_min": 60.0,
            "avgPace_sec": 330.0,
            "avgHR": 148.0,
            "elevationGain": 60.0,
        }])
        df["startTimeLocal"] = pd.to_datetime(df["startTimeLocal"])
        result = _compute_pmc_series(df, threshold_sec=330.0)
        assert not result.empty
        # IF=1, duration_h=1 → tss=100
        assert result.iloc[0]["tss"] == pytest.approx(100.0)
        # TSB = CTL - ATL sur la même ligne : négatif le jour d'une séance
        assert result.iloc[0]["tsb"] == pytest.approx(
            result.iloc[0]["ctl"] - result.iloc[0]["atl"]
        )
        assert result.iloc[0]["tsb"] < 0
        # Après la TSS, ATL et CTL sont strictement positifs
        assert result.iloc[0]["ctl"] > 0
        assert result.iloc[0]["atl"] > 0
        # ATL réagit plus vite que CTL (k_atl < k_ctl → pondération nouvelle TSS plus forte)
        assert result.iloc[0]["atl"] > result.iloc[0]["ctl"]

    def test_future_dated_activity_not_dropped(self):
        """
        Fuzz f_next2 : une activité datée de demain (montre en avance, fuseau
        horaire) ne doit pas disparaître de la réindexation (qui s'arrêtait à
        aujourd'hui). Elle doit rester la dernière ligne de la série, avec
        l'invariant TSB = CTL - ATL comme toutes les autres.
        """
        tomorrow = (
            datetime.combine(date.today() + timedelta(days=1), datetime.min.time())
            + timedelta(hours=8)
        )
        df = pd.DataFrame([{
            "startTimeLocal": tomorrow,
            "activityType": "running",
            "distance_km": 10.0,
            "duration_min": 55.0,
            "avgPace_sec": 330.0,
            "avgHR": 148.0,
            "elevationGain": 60.0,
        }])
        df["startTimeLocal"] = pd.to_datetime(df["startTimeLocal"])
        result = _compute_pmc_series(df, threshold_sec=330.0)
        assert not result.empty
        assert result.iloc[-1]["date"] == pd.Timestamp(tomorrow).normalize()
        assert result.iloc[-1]["tss"] > 0
        assert result.iloc[-1]["tsb"] == pytest.approx(
            result.iloc[-1]["ctl"] - result.iloc[-1]["atl"]
        )

    def test_tsb_est_toujours_ctl_moins_atl(self, make_running_df):
        """
        Invariant de la seule définition du TSB dans l'app : sur CHAQUE jour de
        la série, tsb == ctl - atl. C'est ce qui garantit un chiffre unique
        entre la métrique du haut de la page Forme, celle de tab_charge, la
        courbe PMC et la page Comparatif — et que le TSB tracé soit bien
        l'écart vertical entre les courbes CTL et ATL.
        """
        df = make_running_df(n=25, days_apart=2, with_location=False)
        result = _compute_pmc_series(df, threshold_sec=330.0)
        assert not result.empty
        assert ((result["tsb"] - (result["ctl"] - result["atl"])).abs() < 1e-9).all()

    def test_compute_tsb_reprend_le_tsb_de_la_serie(self, make_running_df):
        """compute_tsb ne doit pas recalculer sa propre fraîcheur."""
        df = make_running_df(n=25, days_apart=2, with_location=False)
        seuil = _reference_threshold_sec(df)
        tsb_serie = _compute_pmc_series(df, seuil).iloc[-1]["tsb"]
        assert _compute_tsb(df)[2] == round(float(tsb_serie), 1)

    def test_ctl_decays_exponentially_after_last_activity(self):
        """
        Après une seule activité, sur les jours suivants sans TSS, CTL doit
        décroître exactement selon k_ctl = exp(-1/42).
        """
        # On force une activité 10 jours avant aujourd'hui pour avoir 10 jours
        # de décroissance derrière
        now = datetime.now()
        df = pd.DataFrame([{
            "startTimeLocal": now - timedelta(days=10),
            "activityType": "running",
            "distance_km": 10.0,
            "duration_min": 60.0,
            "avgPace_sec": 330.0,
            "avgHR": 148.0,
            "elevationGain": 60.0,
        }])
        df["startTimeLocal"] = pd.to_datetime(df["startTimeLocal"])
        result = _compute_pmc_series(df, threshold_sec=330.0)
        # Au moins 11 jours dans la série (jour de l'activité + jours suivants jusqu'à aujourd'hui)
        assert len(result) >= 2
        # Vérifie le ratio entre deux jours successifs sans TSS = k_ctl
        k_ctl_expected = float(np.exp(-1 / 42))
        # On prend le ratio entre jour 1 (lendemain de l'activité, tss=0) et jour 0
        ratio = result.iloc[1]["ctl"] / result.iloc[0]["ctl"]
        assert ratio == pytest.approx(k_ctl_expected, abs=1e-6)

    def test_atl_decays_faster_than_ctl(self):
        """
        Vérifie k_atl < k_ctl : sur un jour sans TSS, ATL chute plus
        que CTL en proportion. Ce test échouerait si on avait inversé les deux
        constantes.
        """
        now = datetime.now()
        df = pd.DataFrame([{
            "startTimeLocal": now - timedelta(days=10),
            "activityType": "running",
            "distance_km": 10.0,
            "duration_min": 60.0,
            "avgPace_sec": 330.0,
            "avgHR": 148.0,
            "elevationGain": 60.0,
        }])
        df["startTimeLocal"] = pd.to_datetime(df["startTimeLocal"])
        result = _compute_pmc_series(df, threshold_sec=330.0)
        ratio_ctl = result.iloc[1]["ctl"] / result.iloc[0]["ctl"]
        ratio_atl = result.iloc[1]["atl"] / result.iloc[0]["atl"]
        # ATL décroît plus vite ⇒ son ratio j+1/j est plus petit
        assert ratio_atl < ratio_ctl

    def test_constants_discriminant(self):
        """
        Test discriminant : si k_ctl et k_atl étaient inversés, ce ratio
        serait celui d'ATL (~0.866) au lieu de celui de CTL (~0.976).
        """
        now = datetime.now()
        df = pd.DataFrame([{
            "startTimeLocal": now - timedelta(days=5),
            "activityType": "running",
            "distance_km": 10.0,
            "duration_min": 60.0,
            "avgPace_sec": 330.0,
            "avgHR": 148.0,
            "elevationGain": 60.0,
        }])
        df["startTimeLocal"] = pd.to_datetime(df["startTimeLocal"])
        result = _compute_pmc_series(df, threshold_sec=330.0)
        k_ctl_expected = float(np.exp(-1 / 42))  # ≈ 0.9764
        k_atl_expected = float(np.exp(-1 / 7))   # ≈ 0.8669
        ratio_ctl = result.iloc[1]["ctl"] / result.iloc[0]["ctl"]
        # Doit matcher k_ctl, pas k_atl — gap nettement plus grand que la tolérance
        assert ratio_ctl == pytest.approx(k_ctl_expected, abs=1e-6)
        assert abs(ratio_ctl - k_atl_expected) > 0.05

    def test_repeated_training_makes_ctl_converge_toward_tss(self):
        """
        Avec une TSS constante quotidienne, CTL doit converger vers cette TSS
        (limite de la suite récurrente x_{n+1} = k*x_n + (1-k)*TSS).
        """
        now = datetime.now()
        # 200 jours de runs quotidiens identiques pour atteindre l'équilibre CTL
        rows = [
            {
                "startTimeLocal": now - timedelta(days=i),
                "activityType": "running",
                "distance_km": 10.0,
                "duration_min": 60.0,
                "avgPace_sec": 330.0,  # IF=1 → tss=100
                "avgHR": 148.0,
                "elevationGain": 60.0,
            }
            for i in range(200)
        ]
        df = pd.DataFrame(rows)
        df["startTimeLocal"] = pd.to_datetime(df["startTimeLocal"])
        result = _compute_pmc_series(df, threshold_sec=330.0)
        # À l'équilibre, CTL doit être proche de 100 (la TSS quotidienne)
        assert result.iloc[-1]["ctl"] == pytest.approx(100.0, abs=2.0)
        assert result.iloc[-1]["atl"] == pytest.approx(100.0, abs=2.0)


# ===========================================================================
# Sport croisé — toutes les activités entrent dans la charge
# ===========================================================================

def _cross_row(when, load, activity_type="windsurfing_v2", duration_min=60.0):
    """Ligne d'activité hors course : pas d'allure, une charge Garmin."""
    return {
        "startTimeLocal": when,
        "activityType": activity_type,
        "distance_km": 8.0,
        "duration_min": duration_min,
        "avgPace_sec": 0.0,
        "avgHR": 145.0,
        "elevationGain": 0.0,
        "trainingLoad": load,
    }


def _with_cross(runs_df, rows):
    """Concatène des activités hors course à un DataFrame de courses."""
    df = pd.concat([runs_df, pd.DataFrame(rows)], ignore_index=True)
    df["startTimeLocal"] = pd.to_datetime(df["startTimeLocal"])
    return df


class TestCrossTrainingFactor:
    """
    Le facteur ramène la charge Garmin sur l'échelle du TSS d'allure. Sans lui,
    le CTL changerait d'unité selon la part de sport croisé de la semaine.
    """

    def test_sans_colonne_training_load_on_retombe_sur_le_defaut(self, make_running_df):
        df = make_running_df(n=20)
        assert _cross_training_factor(df, 330.0) == CROSS_TRAINING_FALLBACK_K

    def test_df_vide_retombe_sur_le_defaut(self):
        assert _cross_training_factor(pd.DataFrame(), 330.0) == CROSS_TRAINING_FALLBACK_K

    def test_historique_trop_court_retombe_sur_le_defaut(self, make_running_df):
        df = make_running_df(n=5)
        df["trainingLoad"] = 100.0
        assert _cross_training_factor(df, 330.0) == CROSS_TRAINING_FALLBACK_K

    def test_calibration_sur_les_courses(self, make_running_df):
        """1 h pile à l'allure seuil = 100 TSS ; face à 200 de charge → k = 0.5."""
        df = make_running_df(n=20, pace_sec=330.0, duration_min=60.0)
        df["trainingLoad"] = 200.0
        assert _cross_training_factor(df, 330.0) == pytest.approx(0.5)

    def test_facteur_borne(self, make_running_df):
        faible = make_running_df(n=20, duration_min=60.0)
        faible["trainingLoad"] = 100_000.0
        assert _cross_training_factor(faible, 330.0) == CROSS_TRAINING_K_MIN
        fort = make_running_df(n=20, duration_min=60.0)
        fort["trainingLoad"] = 0.01
        assert _cross_training_factor(fort, 330.0) == CROSS_TRAINING_K_MAX

    def test_suit_le_curseur_dallure_seuil(self, make_running_df):
        """Un seuil plus rapide gonfle le TSS de course, donc aussi le facteur."""
        df = make_running_df(n=20, pace_sec=330.0, duration_min=60.0)
        df["trainingLoad"] = 200.0
        assert _cross_training_factor(df, 400.0) > _cross_training_factor(df, 300.0)


class TestDailyTss:
    """Décomposition course / hors course du stress quotidien."""

    def test_colonnes_et_vide(self):
        result = _daily_tss(pd.DataFrame(), 330.0)
        assert result.empty
        assert list(result.columns) == ["day", "tss_run", "tss_cross", "tss"]

    def test_activite_hors_course_seule_compte(self):
        now = datetime.now()
        df = pd.DataFrame([_cross_row(now, 200.0)])
        df["startTimeLocal"] = pd.to_datetime(df["startTimeLocal"])
        result = _daily_tss(df, 330.0)
        assert len(result) == 1
        # Aucune course pour calibrer → facteur par défaut
        assert result.iloc[0]["tss_cross"] == pytest.approx(200.0 * CROSS_TRAINING_FALLBACK_K)
        assert result.iloc[0]["tss_run"] == 0.0
        assert result.iloc[0]["tss"] == result.iloc[0]["tss_cross"]

    def test_course_et_hors_course_le_meme_jour_sadditionnent(self, make_running_df):
        runs = make_running_df(n=20, days_apart=3, duration_min=60.0, with_location=False)
        runs["trainingLoad"] = 200.0
        wing_day = runs["startTimeLocal"].iloc[0]
        df = _with_cross(runs, [_cross_row(wing_day, 400.0)])
        result = _daily_tss(df, 330.0)
        row = result[result["day"] == pd.Timestamp(wing_day).normalize()].iloc[0]
        assert row["tss_run"] > 0
        assert row["tss_cross"] == pytest.approx(400.0 * 0.5)
        assert row["tss"] == pytest.approx(row["tss_run"] + row["tss_cross"])

    def test_charge_absente_ou_nulle_ignoree(self, make_running_df):
        runs = make_running_df(n=20, with_location=False)
        runs["trainingLoad"] = 200.0
        df = _with_cross(runs, [
            _cross_row(datetime.now(), 0.0),
            _cross_row(datetime.now() - timedelta(days=1), None),
        ])
        assert _daily_tss(df, 330.0)["tss_cross"].sum() == 0.0

    def test_tss_par_activite_plafonne(self, make_running_df):
        runs = make_running_df(n=20, duration_min=60.0, with_location=False)
        runs["trainingLoad"] = 100.0          # k = 1.0
        df = _with_cross(runs, [_cross_row(datetime.now(), 5_000.0)])
        assert _daily_tss(df, 330.0)["tss_cross"].max() == 400.0


class TestPmcAvecSportCroise:
    """
    Le PMC voit toutes les activités : c'est ce qui empêche le dashboard
    d'annoncer « bien reposé » au lendemain de la plus grosse séance de la
    semaine quand celle-ci n'est pas une course.
    """

    def test_le_sport_croise_augmente_le_ctl(self, make_running_df):
        runs = make_running_df(n=20, days_apart=3, duration_min=60.0, with_location=False)
        runs["trainingLoad"] = 200.0
        ctl_course_seule = _compute_tsb(runs)[0]
        df = _with_cross(runs, [
            _cross_row(datetime.now() - timedelta(days=i * 3 + 1), 300.0)
            for i in range(20)
        ])
        assert _compute_tsb(df)[0] > ctl_course_seule

    def test_le_sport_croise_degrade_le_tsb_du_lendemain(self, make_running_df):
        """Une grosse session hier doit se voir dans la fraîcheur d'aujourd'hui."""
        runs = make_running_df(n=20, days_apart=3, duration_min=60.0, with_location=False)
        runs["trainingLoad"] = 200.0
        tsb_sans = _compute_tsb(runs)[2]
        df = _with_cross(runs, [_cross_row(datetime.now() - timedelta(days=1), 500.0)])
        assert _compute_tsb(df)[2] < tsb_sans

    def test_serie_decomposee_et_coherente(self, make_running_df):
        runs = make_running_df(n=20, days_apart=3, duration_min=60.0, with_location=False)
        runs["trainingLoad"] = 200.0
        df = _with_cross(runs, [_cross_row(datetime.now() - timedelta(days=1), 300.0)])
        pmc = _compute_pmc_series(df, 330.0)
        assert (pmc["tss"] == pmc["tss_run"] + pmc["tss_cross"]).all()
        assert pmc["tss_cross"].sum() > 0
        # Le TSB reste l'écart vertical exact entre CTL et ATL
        assert (pmc["tsb"] - (pmc["ctl"] - pmc["atl"])).abs().max() < 1e-9

    def test_seuil_de_reference_ignore_les_autres_sports(self, make_running_df):
        """Une sortie vélo de 8 km ne doit pas déplacer l'allure seuil."""
        runs = make_running_df(n=20, pace_sec=340.0, with_location=False)
        seuil_course = _reference_threshold_sec(runs)
        velo = _cross_row(datetime.now(), 200.0, activity_type="cycling")
        velo["avgPace_sec"] = 120.0            # 2:00/km — hors de portée à pied
        df = _with_cross(runs, [velo])
        assert _reference_threshold_sec(df) == seuil_course

    def test_recommend_session_lit_la_charge_globale(self, make_running_df):
        """`load_df` change le TSB qui choisit la séance, pas les cibles."""
        runs = make_running_df(n=20, days_apart=3, duration_min=60.0, with_location=False)
        runs["trainingLoad"] = 200.0
        df = _with_cross(runs, [
            _cross_row(datetime.now() - timedelta(days=i), 500.0) for i in range(1, 8)
        ])
        rec_course = _recommend_session(runs)
        rec_global = _recommend_session(runs, load_df=df)
        assert rec_global["tsb"] < rec_course["tsb"]
        assert rec_global["avg_dist"] == rec_course["avg_dist"]

    def test_recommend_session_sans_load_df_inchange(self, make_running_df):
        """Contrat historique : sans `load_df`, le comportement est identique."""
        runs = make_running_df(n=20, days_apart=3, with_location=False)
        assert _recommend_session(runs)["tsb"] == _recommend_session(runs, load_df=runs)["tsb"]


# ---------------------------------------------------------------------------
# load_risk (ACWR, monotonie)
# ---------------------------------------------------------------------------

def _pmc(daily_values):
    import pandas as _pd
    days = _pd.date_range("2026-01-01", periods=len(daily_values), freq="D")
    return _pd.DataFrame({"date": days, "tss": daily_values})


def test_load_risk_steady_is_optimal():
    from next_session_logic import load_risk
    r = load_risk(_pmc([50, 0] * 14))
    assert r["acwr"] == pytest.approx(150 / 7 / 25, abs=0.01)   # 3 séances sur les 7 derniers jours
    assert r["acwr_zone"] == "optimal"
    assert r["monotony"] is not None and not r["monotony_high"]


def test_load_risk_spike_flagged():
    from next_session_logic import load_risk
    r = load_risk(_pmc([30] * 21 + [120] * 7))
    assert r["acwr"] > 1.5 and r["acwr_zone"] == "risque"


def test_load_risk_counts_days_not_activities():
    """Revue : 7 lignes = 7 jours ; une semaine sans séance fait baisser l'aigu."""
    from next_session_logic import load_risk
    r = load_risk(_pmc([60] * 21 + [0] * 7))
    assert r["acute"] == 0 and r["acwr_zone"] == "sous_charge"
    assert r["monotony"] is None and r["strain"] is None   # écart-type nul


def test_load_risk_short_history():
    from next_session_logic import load_risk
    assert load_risk(_pmc([50] * 10)) == {}
    assert load_risk(None) == {}


def test_load_risk_on_real_pmc_series(sample_running_df):
    from next_session_logic import compute_pmc_series, load_risk
    r = load_risk(compute_pmc_series(sample_running_df, 330))
    assert set(r) >= {"acwr", "monotony", "strain"} or r == {}


# ---------------------------------------------------------------------------
# Plan Objectif dans la séance du jour (merge_goal_plan_into_recommendation)
# ---------------------------------------------------------------------------

def _goal_sessions(first_kind="tempo", day_offset=1):
    from datetime import date as _d, timedelta as _td
    base_day = _d.today() + _td(days=day_offset)
    tempo = {"date": base_day.isoformat(), "kind": first_kind, "title": "Seuil", "distance_km": 8.0,
             "duration_min": 50, "target": "2 × 8′ à 5:00–5:10/km", "why": "Seuil",
             "steps": [{"type": "warmup", "duration_s": 900, "pace_fast": 400, "pace_slow": 420},
                       {"type": "repeat", "count": 2, "steps": [
                           {"type": "interval", "duration_s": 480, "pace_fast": 300, "pace_slow": 310},
                           {"type": "recovery", "duration_s": 120, "pace_fast": None, "pace_slow": None}]},
                       {"type": "cooldown", "duration_s": 600, "pace_fast": 400, "pace_slow": 420}]}
    strength = {"date": base_day.isoformat(), "kind": "strength", "title": "Renfo", "distance_km": 0,
                "duration_min": 40, "steps": []}
    return [strength, tempo]


def test_goal_session_uses_overall_pace_not_warmup(sample_running_df):
    from next_session_logic import todays_session
    rec = todays_session(sample_running_df, "BALANCED", 80, None, _goal_sessions())["rec"]
    assert rec["goal_session"]["title"] == "Seuil" and rec["session_key"] == "tempo"
    expected = (1500 * 410 + 960 * 305) / (1500 + 960)
    assert rec["target_pace_sec"] == pytest.approx(expected)


def test_run_coach_active_without_next_run_keeps_priority(sample_running_df):
    """Revue : Run Coach actif sans séance à venir → PAS le plan Objectif."""
    from next_session_logic import todays_session
    coach = {"plan": {"name": "Run Coach"}, "next_run": None, "phase": None, "days_to_event": None}
    rec = todays_session(sample_running_df, "BALANCED", 80, coach, _goal_sessions())["rec"]
    assert rec.get("goal_session") is None


@pytest.mark.parametrize("sessions", [None, [], [_goal_sessions()[0]]])
def test_no_goal_run_falls_back_to_internal(sample_running_df, sessions):
    from next_session_logic import recommend_session, todays_session
    rec = todays_session(sample_running_df, "BALANCED", 80, None, sessions)["rec"]
    assert rec.get("goal_session") is None
    assert rec["session_key"] == recommend_session(
        sample_running_df[sample_running_df["activityType"] == "running"],
        downgrade=0, load_df=sample_running_df)["session_key"]


def test_race_day_pace_and_alert(sample_running_df):
    from next_session_logic import todays_session
    race = [{"date": (__import__("datetime").date.today()).isoformat(), "kind": "race",
             "title": "🏁 Semi-marathon", "distance_km": 21.1, "duration_min": 110,
             "target": "5:14/km", "pace_sec": 314.0, "steps": []}]
    df = sample_running_df.copy()
    df["startTimeLocal"] = df["startTimeLocal"] - pd.Timedelta(days=1)   # pas de course aujourd'hui
    out = todays_session(df, "UNBALANCED", 40, None, race)
    assert out["rec"]["target_pace_sec"] == 314.0
    assert "jour de la course" in out["alert"] and "décaler" not in out["alert"]


def test_todays_plan_session_skipped_once_run(sample_running_df):
    from next_session_logic import todays_session
    df = sample_running_df.copy()
    df.loc[df.index[0], "startTimeLocal"] = pd.Timestamp.now().normalize() + pd.Timedelta(hours=7)
    sessions = _goal_sessions(day_offset=0) + [dict(_goal_sessions(day_offset=2)[1], title="Plus tard")]
    rec = todays_session(df, "BALANCED", 80, None, sessions)["rec"]
    assert rec["goal_session"]["title"] == "Plus tard"


def test_validated_sessions_only_when_current(tmp_path, monkeypatch):
    from datetime import date as _d, timedelta as _td
    import goal_store
    from race_plan_logic import athlete_baseline, build_race_plan
    from workout_export import plan_id_of
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    goal = {"distance": "10 km", "race_date": (_d.today() + _td(weeks=6)).isoformat()}
    prefs = {"runs_per_week": 4}
    plan = build_race_plan(_d.fromisoformat(goal["race_date"]), "10 km",
                           athlete_baseline(None, _d.today()), _d.today())
    goal_store.save_goal(1, goal, prefs)
    assert goal_store.validated_sessions(1) is None                    # pas validé
    goal_store.validate_plan(1, plan_id_of(goal, prefs), plan)
    assert goal_store.validated_sessions(1)                             # validé et à jour
    goal_store.save_goal(1, goal, {"runs_per_week": 5})
    assert goal_store.validated_sessions(1) is None                    # préférences changées
    goal_store.save_goal(1, dict(goal, race_date="2020-01-01"), prefs)
    assert goal_store.validated_sessions(1) is None                    # course passée
