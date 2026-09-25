"""Tests des records personnels, estimations Riegel et formats de temps."""

import pandas as pd

from progression_logic import (
    RACE_TARGETS,
    fmt_race_pace,
    fmt_race_time,
    parse_personal_records,
    predictions_history_df,
    riegel_estimates,
)


class TestFormats:
    def test_fmt_race_time_minutes(self):
        assert fmt_race_time(1432) == "23'52\""

    def test_fmt_race_time_hours(self):
        assert fmt_race_time(6973) == "1h56'13\""

    def test_fmt_race_pace(self):
        assert fmt_race_pace(1500, 5.0) == "5:00/km"


class TestParsePersonalRecords:
    def _pr(self, type_id, value, **kw):
        return {
            "typeId": type_id,
            "value": value,
            "activityName": kw.get("name", "Sortie test"),
            "activityId": kw.get("aid", 42),
            "actStartDateTimeInGMTFormatted": kw.get("date", "2026-01-27T11:17:57.0"),
        }

    def test_time_record(self):
        rows = parse_personal_records([self._pr(3, 1437.8)])
        assert len(rows) == 1
        r = rows[0]
        assert r["group"] == "course"
        assert r["label"] == "5 km"
        assert r["value_str"] == "23'57\""
        assert r["date_str"] == "27/01/2026"
        assert r["activity_name"] == "Sortie test"

    def test_distance_record_km(self):
        rows = parse_personal_records([self._pr(7, 42123.8)])
        assert rows[0]["value_str"] == "42.1 km"

    def test_steps_record(self):
        rows = parse_personal_records([self._pr(12, 61445.0)])
        assert rows[0]["value_str"] == "61 445"
        assert rows[0]["group"] == "quotidien"

    def test_unknown_type_ignored(self):
        assert parse_personal_records([self._pr(99, 123.0)]) == []

    def test_zero_or_missing_value_ignored(self):
        assert parse_personal_records([self._pr(3, 0)]) == []
        assert parse_personal_records([{"typeId": 3}]) == []

    def test_groups_ordered(self):
        rows = parse_personal_records([
            self._pr(17, 3075.0),   # natation
            self._pr(3, 1437.0),    # course
            self._pr(8, 52544.0),   # vélo
        ])
        assert [r["group"] for r in rows] == ["course", "velo", "natation"]

    def test_empty_input(self):
        assert parse_personal_records(None) == []
        assert parse_personal_records([]) == []

    def test_course_records_ordered_by_distance(self):
        """type_order (typeId Garmin, ordre de distance croissante) doit primer
        sur le tri alphabétique du label, qui donnait ['1 km', '10 km', '5 km',
        'Marathon', 'Semi-marathon']."""
        rows = parse_personal_records([
            self._pr(6, 12000.0),   # Marathon
            self._pr(4, 2400.0),    # 10 km
            self._pr(1, 200.0),     # 1 km
            self._pr(3, 1100.0),    # 5 km
            self._pr(5, 5500.0),    # Semi-marathon
        ])
        assert [r["label"] for r in rows] == [
            "1 km", "5 km", "10 km", "Semi-marathon", "Marathon",
        ]


class TestRiegelEstimates:
    def test_estimates_from_runs(self, sample_running_df):
        est = riegel_estimates(sample_running_df)
        assert set(est) == {label for label, _km, _key in RACE_TARGETS}
        # Cohérence : le temps croît avec la distance
        assert est["5 km"] < est["10 km"] < est["Semi"] < est["Marathon"]

    def test_empty(self, empty_df):
        assert riegel_estimates(empty_df) == {}

    def test_no_valid_pace(self, sample_running_df):
        df = sample_running_df.copy()
        df["avgPace_sec"] = 0.0
        assert riegel_estimates(df) == {}


class TestPredictionsHistoryDf:
    def test_long_format(self):
        raw = [
            {"calendarDate": "2026-07-01", "time5K": 1400, "time10K": 3000,
             "timeHalfMarathon": 6800, "timeMarathon": 15000},
            {"calendarDate": "2026-07-02", "time5K": 1395, "time10K": 2995,
             "timeHalfMarathon": 6790, "timeMarathon": 14980},
        ]
        df = predictions_history_df(raw)
        assert len(df) == 8
        assert set(df["distance"]) == {"5 km", "10 km", "Semi", "Marathon"}
        assert pd.api.types.is_datetime64_any_dtype(df["date"])

    def test_missing_values_skipped(self):
        df = predictions_history_df([{"calendarDate": "2026-07-01", "time5K": 1400}])
        assert len(df) == 1

    def test_empty(self):
        assert predictions_history_df(None).empty
        assert predictions_history_df([]).empty
