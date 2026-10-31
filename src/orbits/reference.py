"""Readers for independent reference ephemerides used to validate SGP4.

NASA publishes the ISS trajectory from its flight-dynamics team (JSC FOD/TOPO)
as a CCSDS Orbit Ephemeris Message (OEM): state vectors in the EME2000 inertial
frame, independent of the public TLE/GP data.
"""

from __future__ import annotations

import pandas as pd

ISS_OEM_URL = "https://nasa-public-data.s3.amazonaws.com/iss-coords/current/ISS_OEM/ISS.OEM_J2K_EPH.txt"

OEM_COLUMNS = ["x_km", "y_km", "z_km", "vx_km_s", "vy_km_s", "vz_km_s"]


class OemError(ValueError):
    pass


def parse_oem(text: str) -> pd.DataFrame:
    """Parse a KVN-format OEM into a time-indexed table of EME2000 states.

    Only UTC, Earth-centred, EME2000 segments are accepted; anything else
    raises, because silently mixing frames would corrupt the comparison.
    """
    rows: list[list[float]] = []
    times: list[str] = []
    meta: dict[str, str] = {}
    in_meta = False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("COMMENT"):
            continue
        if line == "META_START":
            in_meta, meta = True, {}
            continue
        if line == "META_STOP":
            in_meta = False
            _check_meta(meta)
            continue
        if in_meta or "=" in line:
            key, _, value = line.partition("=")
            meta[key.strip()] = value.strip()
            continue
        parts = line.split()
        if len(parts) >= 7:
            times.append(parts[0])
            rows.append([float(v) for v in parts[1:7]])
    if not rows:
        raise OemError("no state vectors found")
    df = pd.DataFrame(rows, columns=OEM_COLUMNS)
    df.index = pd.DatetimeIndex(pd.to_datetime(times, utc=True, format="ISO8601"), name="time")
    return df[~df.index.duplicated(keep="last")].sort_index()


def _check_meta(meta: dict[str, str]) -> None:
    expected = {"CENTER_NAME": "EARTH", "REF_FRAME": "EME2000", "TIME_SYSTEM": "UTC"}
    for key, value in expected.items():
        if meta.get(key, "").upper() != value:
            raise OemError(f"unsupported OEM segment: {key}={meta.get(key)!r}, expected {value}")
