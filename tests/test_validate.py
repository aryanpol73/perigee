from datetime import timedelta

import pandas as pd
import pytest

from src.ingest.parse import parse_gp, parse_satcat, parse_space_weather
from src.ingest.validate import validate_gp, validate_satcat, validate_space_weather
from tests.conftest import NOW


def _validate(df, **overrides):
    kwargs = {
        "expected_groups": ["stations"],
        "now": NOW,
        "min_objects": 10,
        "previous_count": None,
        "max_drop": 0.2,
        "max_bad_fraction": 0.1,
    }
    kwargs.update(overrides)
    return validate_gp(df, **kwargs)


@pytest.fixture
def gp(gp_bytes):
    return parse_gp({"stations": gp_bytes})


def test_good_gp_passes(gp):
    clean, report = _validate(gp)
    assert report.ok, report.errors
    assert report.rows_out == len(clean) == 20
    assert clean["norad_id"].is_monotonic_increasing


def test_future_epoch_row_is_excluded_and_counted(gp):
    gp.loc[0, "epoch"] = pd.Timestamp(NOW + timedelta(days=2))
    clean, report = _validate(gp)
    assert report.ok
    assert report.rejected_rows == {"future_epoch": 1}
    assert len(clean) == 19


@pytest.mark.parametrize(
    ("column", "value", "reason"),
    [
        ("eccentricity", 1.0, "eccentricity_out_of_range"),
        ("eccentricity", -0.1, "eccentricity_out_of_range"),
        ("mean_motion", 0.0, "mean_motion_not_positive"),
        ("mean_motion", float("nan"), "mean_motion_not_positive"),
        ("inclination", 181.0, "inclination_out_of_range"),
    ],
)
def test_impossible_orbit_values_are_excluded(gp, column, value, reason):
    gp.loc[3, column] = value
    clean, report = _validate(gp)
    assert report.rejected_rows == {reason: 1}
    assert len(clean) == 19


def test_too_many_bad_rows_rejects_the_whole_file(gp):
    gp.loc[0, "eccentricity"] = 2.0
    _, report = _validate(gp, max_bad_fraction=0.01)
    assert not report.ok
    assert "failed checks" in report.errors[0]


def test_duplicate_catalog_number_within_group_is_rejected(gp):
    doubled = pd.concat([gp, gp.iloc[[0]]], ignore_index=True)
    _, report = _validate(doubled)
    assert not report.ok
    assert "duplicated" in report.errors[0]


def test_same_object_in_two_groups_keeps_freshest_record(gp):
    other = gp.iloc[[0]].copy()
    other["source_group"] = "active"
    other["epoch"] = other["epoch"] + pd.Timedelta(hours=1)
    clean, report = _validate(pd.concat([gp, other]), expected_groups=["stations", "active"])
    assert report.ok, report.errors
    assert report.deduplicated == 1
    kept = clean.set_index("norad_id").loc[gp.loc[0, "norad_id"]]
    assert kept["source_group"] == "active"


def test_empty_group_is_rejected(gp):
    _, report = _validate(gp, expected_groups=["stations", "active"])
    assert not report.ok
    assert "'active' returned no rows" in report.errors[0]


def test_too_few_objects_is_rejected(gp):
    _, report = _validate(gp, min_objects=1000)
    assert not report.ok


def test_sudden_count_drop_is_rejected(gp):
    _, report = _validate(gp.iloc[:12], previous_count=20)
    assert not report.ok
    assert "fell from 20 to 12" in report.errors[0]


def test_small_count_drop_is_allowed(gp):
    _, report = _validate(gp.iloc[:18], previous_count=20)
    assert report.ok


def test_satcat_duplicates_rejected(satcat_bytes):
    df = parse_satcat(satcat_bytes)
    _, ok_report = validate_satcat(
        df, min_objects=10, previous_count=None, max_drop=0.2, max_bad_fraction=0.01
    )
    assert ok_report.ok
    doubled = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    _, report = validate_satcat(
        doubled, min_objects=10, previous_count=None, max_drop=0.2, max_bad_fraction=0.01
    )
    assert not report.ok


def test_space_weather_checks(sw_bytes):
    df = parse_space_weather(sw_bytes)
    clean, report = validate_space_weather(df, min_days=10, max_bad_fraction=0.1)
    assert report.ok and len(clean) == 14

    df.loc[2, "f107_obs"] = -1.0
    clean, report = validate_space_weather(df, min_days=10, max_bad_fraction=0.1)
    assert report.rejected_rows == {"observed_f107_not_positive": 1}

    doubled = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    _, report = validate_space_weather(doubled, min_days=10, max_bad_fraction=0.1)
    assert not report.ok
