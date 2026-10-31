import pandas as pd
import pytest

from src.ingest.parse import SchemaError, parse_gp, parse_satcat, parse_space_weather


def test_gp_is_typed(gp_bytes):
    df = parse_gp({"stations": gp_bytes})
    assert len(df) == 20
    assert str(df["norad_id"].dtype) == "Int64"
    assert str(df["epoch"].dt.tz) == "UTC"
    assert (df["source_group"] == "stations").all()
    iss = df.set_index("norad_id").loc[25544]
    assert iss["object_name"] == "ISS (ZARYA)"
    assert iss["bstar"] == pytest.approx(1.0764701e-4)
    assert iss["epoch"] == pd.Timestamp("2026-10-29T13:31:42.192192", tz="UTC")


def test_gp_keeps_six_digit_catalog_numbers(gp_bytes):
    df = parse_gp({"stations": gp_bytes})
    assert (df["norad_id"] > 99_999).sum() >= 1


def test_gp_unparseable_values_become_null(gp_bytes):
    broken = gp_bytes.replace(b"15.49100842", b"not-a-number", 1)
    df = parse_gp({"stations": broken})
    assert df["mean_motion"].isna().sum() == 1


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"<html><body>Invalid query</body></html>",
        b"No GP data found\n",
        b"OBJECT_NAME,EPOCH\nISS,2026-10-29T00:00:00\n",
    ],
)
def test_gp_rejects_wrong_format(body):
    with pytest.raises(SchemaError):
        parse_gp({"stations": body})


def test_satcat_is_typed_with_size_class(satcat_bytes):
    df = parse_satcat(satcat_bytes).set_index("norad_id")
    assert df.loc[1, "object_type"] == "R/B"
    assert df.loc[1, "rcs_size"] == "LARGE"  # 20.42 m^2
    assert df.loc[3, "rcs_size"] == "SMALL"  # 0.08 m^2
    assert pd.isna(df.loc[2, "rcs_size"])  # no RCS published
    assert df.loc[1, "launch_date"] == pd.Timestamp("1957-10-04")
    assert pd.isna(df.loc[5, "decay_date"])  # Vanguard 1 is still in orbit


def test_space_weather_columns_are_renamed(sw_bytes):
    df = parse_space_weather(sw_bytes)
    assert {"date", "kp_sum", "ap_avg", "f107_obs", "f107_adj", "f107_data_type"} <= set(df.columns)
    assert df.loc[0, "date"] == pd.Timestamp("2021-01-01")
    assert df.loc[0, "f107_obs"] == pytest.approx(80.4)
    assert df.loc[0, "f107_data_type"] == "OBS"
