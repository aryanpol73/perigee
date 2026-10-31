"""Parse raw CelesTrak CSV downloads into typed tables.

Values that cannot be parsed become nulls here; validate.py decides whether
the rows (or the whole download) are acceptable.
"""

from __future__ import annotations

import io

import numpy as np
import pandas as pd


class SchemaError(ValueError):
    """The download is not the CSV we expected (wrong columns, HTML, empty)."""


# CSV column -> (output column, kind)
GP_SCHEMA: dict[str, tuple[str, str]] = {
    "NORAD_CAT_ID": ("norad_id", "int"),
    "OBJECT_NAME": ("object_name", "str"),
    "OBJECT_ID": ("object_id", "str"),
    "EPOCH": ("epoch", "datetime_utc"),
    "MEAN_MOTION": ("mean_motion", "float"),
    "ECCENTRICITY": ("eccentricity", "float"),
    "INCLINATION": ("inclination", "float"),
    "RA_OF_ASC_NODE": ("ra_of_asc_node", "float"),
    "ARG_OF_PERICENTER": ("arg_of_pericenter", "float"),
    "MEAN_ANOMALY": ("mean_anomaly", "float"),
    "EPHEMERIS_TYPE": ("ephemeris_type", "int"),
    "CLASSIFICATION_TYPE": ("classification_type", "str"),
    "ELEMENT_SET_NO": ("element_set_no", "int"),
    "REV_AT_EPOCH": ("rev_at_epoch", "int"),
    "BSTAR": ("bstar", "float"),
    "MEAN_MOTION_DOT": ("mean_motion_dot", "float"),
    "MEAN_MOTION_DDOT": ("mean_motion_ddot", "float"),
}

SATCAT_SCHEMA: dict[str, tuple[str, str]] = {
    "NORAD_CAT_ID": ("norad_id", "int"),
    "OBJECT_NAME": ("object_name", "str"),
    "OBJECT_ID": ("object_id", "str"),
    "OBJECT_TYPE": ("object_type", "str"),
    "OPS_STATUS_CODE": ("ops_status_code", "str"),
    "OWNER": ("owner", "str"),
    "LAUNCH_DATE": ("launch_date", "date"),
    "LAUNCH_SITE": ("launch_site", "str"),
    "DECAY_DATE": ("decay_date", "date"),
    "PERIOD": ("period_min", "float"),
    "INCLINATION": ("inclination", "float"),
    "APOGEE": ("apogee_km", "float"),
    "PERIGEE": ("perigee_km", "float"),
    "RCS": ("rcs_m2", "float"),
    "DATA_STATUS_CODE": ("data_status_code", "str"),
    "ORBIT_CENTER": ("orbit_center", "str"),
    "ORBIT_TYPE": ("orbit_type", "str"),
}

# Space weather has ~30 columns; these must be present, the rest pass through as floats.
SPACE_WEATHER_REQUIRED = (
    "DATE",
    "KP_SUM",
    "AP_AVG",
    "F10.7_OBS",
    "F10.7_ADJ",
    "F10.7_DATA_TYPE",
    "F10.7_OBS_CENTER81",
)
SPACE_WEATHER_STR_COLUMNS = {"F10.7_DATA_TYPE"}

# Radar cross-section size classes, as used by Space-Track.
RCS_SMALL_MAX_M2 = 0.1
RCS_MEDIUM_MAX_M2 = 1.0


def _read_csv(data: bytes, required: tuple[str, ...] | list[str], what: str) -> pd.DataFrame:
    if not data.strip():
        raise SchemaError(f"{what}: empty download")
    try:
        df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, na_values=[""])
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
        raise SchemaError(f"{what}: not a readable CSV ({exc})") from exc
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise SchemaError(f"{what}: missing columns {missing}")
    return df


def _convert(series: pd.Series, kind: str) -> pd.Series:
    if kind == "str":
        return series
    if kind == "float":
        return pd.to_numeric(series, errors="coerce").astype("float64")
    if kind == "int":
        numbers = pd.to_numeric(series, errors="coerce")
        return numbers.where(numbers == numbers.round()).astype("Int64")
    if kind == "datetime_utc":
        return pd.to_datetime(series, utc=True, errors="coerce", format="ISO8601")
    if kind == "date":
        return pd.to_datetime(series, errors="coerce", format="ISO8601")
    raise ValueError(f"unknown kind {kind}")


def _apply_schema(df: pd.DataFrame, schema: dict[str, tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame({out: _convert(df[col], kind) for col, (out, kind) in schema.items()})


def parse_gp(files: dict[str, bytes]) -> pd.DataFrame:
    """Parse one GP download per CelesTrak group into a single table."""
    frames = []
    for group, data in files.items():
        raw = _read_csv(data, list(GP_SCHEMA), f"GP group {group}")
        typed = _apply_schema(raw, GP_SCHEMA)
        typed["source_group"] = group
        frames.append(typed)
    if not frames:
        raise SchemaError("GP: no groups downloaded")
    return pd.concat(frames, ignore_index=True)


def rcs_size(rcs_m2: pd.Series) -> pd.Series:
    labels = np.select(
        [rcs_m2 < RCS_SMALL_MAX_M2, rcs_m2 < RCS_MEDIUM_MAX_M2, rcs_m2 >= RCS_MEDIUM_MAX_M2],
        ["SMALL", "MEDIUM", "LARGE"],
        default="",
    )
    return pd.Series(labels, index=rcs_m2.index).replace("", None)


def parse_satcat(data: bytes) -> pd.DataFrame:
    raw = _read_csv(data, list(SATCAT_SCHEMA), "SATCAT")
    typed = _apply_schema(raw, SATCAT_SCHEMA)
    typed["rcs_size"] = rcs_size(typed["rcs_m2"])
    return typed


def _space_weather_name(column: str) -> str:
    return column.lower().replace("f10.7", "f107")


def parse_space_weather(data: bytes) -> pd.DataFrame:
    raw = _read_csv(data, SPACE_WEATHER_REQUIRED, "space weather")
    out = {}
    for col in raw.columns:
        if col == "DATE":
            kind = "date"
        elif col in SPACE_WEATHER_STR_COLUMNS:
            kind = "str"
        else:
            kind = "float"
        out[_space_weather_name(col)] = _convert(raw[col], kind)
    return pd.DataFrame(out)
