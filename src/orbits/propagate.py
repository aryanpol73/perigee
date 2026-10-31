"""SGP4 propagation of catalog rows, vectorised over objects and times.

Records are built from the typed catalog columns exactly as sgp4's own OMM
reader does (WGS72 constants, Kozai mean motion), so results match satellite.js
fed the same OMM fields.

Memory: positions and velocities take 48 bytes per object per time step, so
20,000 objects x 1,440 steps is ~1.4 GB. Callers must chunk long time grids.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from math import pi

import numpy as np
import numpy.typing as npt
import pandas as pd
from sgp4.api import SGP4_ERRORS, WGS72, Satrec, SatrecArray

from src.orbits.frames import ecef_to_geodetic, gmst_rad, julian_dates, teme_to_ecef

log = logging.getLogger("perigee.orbits.propagate")

SGP4_EPOCH0 = pd.Timestamp("1949-12-31T00:00:00", tz="UTC")
NDOT_UNITS = 1_036_800.0 / pi  # rev/day^2 -> rad/min^2 (see SGP4.cpp)
NDDOT_UNITS = 2_985_984_000.0 / 2.0 / pi  # rev/day^3 -> rad/min^3
DEG = pi / 180.0
INIT_FAILED = 99  # our code for a record sgp4init itself rejected

ERROR_LABELS: dict[int, str] = {
    **{code: text for code, text in SGP4_ERRORS.items() if code != 5},
    INIT_FAILED: "element set rejected by sgp4init",
}

REQUIRED_COLUMNS = (
    "norad_id",
    "epoch",
    "mean_motion",
    "eccentricity",
    "inclination",
    "ra_of_asc_node",
    "arg_of_pericenter",
    "mean_anomaly",
    "bstar",
    "mean_motion_dot",
    "mean_motion_ddot",
)


def satrec_from_elements(
    norad_id: int,
    epoch: pd.Timestamp,
    mean_motion: float,
    eccentricity: float,
    inclination: float,
    ra_of_asc_node: float,
    arg_of_pericenter: float,
    mean_anomaly: float,
    bstar: float,
    mean_motion_dot: float,
    mean_motion_ddot: float,
) -> Satrec:
    """Build an SGP4 record from OMM mean elements (degrees, rev/day)."""
    epoch_days = (pd.Timestamp(epoch) - SGP4_EPOCH0) / pd.Timedelta(days=1)
    sat = Satrec()
    # satnum only labels the record; values above the old 5-digit/alpha-5 range
    # are not representable, and they do not affect the propagation.
    satnum = int(norad_id) if int(norad_id) <= 339_999 else 0
    sat.sgp4init(
        WGS72,
        "i",
        satnum,
        epoch_days,
        float(bstar),
        float(mean_motion_dot) / NDOT_UNITS,
        float(mean_motion_ddot) / NDDOT_UNITS,
        float(eccentricity),
        float(arg_of_pericenter) * DEG,
        float(inclination) * DEG,
        float(mean_anomaly) * DEG,
        float(mean_motion) / 720.0 * pi,
        float(ra_of_asc_node) * DEG,
    )
    return sat


def satrecs_from_catalog(catalog: pd.DataFrame) -> tuple[list[Satrec], npt.NDArray[np.bool_]]:
    """One record per row, plus a mask of rows whose initialisation failed."""
    missing = [c for c in REQUIRED_COLUMNS if c not in catalog.columns]
    if missing:
        raise KeyError(f"catalog is missing columns {missing}")
    sats = []
    init_failed = np.zeros(len(catalog), dtype=bool)
    rows = catalog.loc[:, list(REQUIRED_COLUMNS)].itertuples(index=False, name=None)
    for i, row in enumerate(rows):
        sat = satrec_from_elements(*row)
        init_failed[i] = sat.error != 0
        sats.append(sat)
    return sats, init_failed


@dataclass
class Propagation:
    norad_id: npt.NDArray[np.int64]
    times: pd.DatetimeIndex
    position_teme_km: npt.NDArray[np.float64]  # (objects, times, 3)
    velocity_teme_km_s: npt.NDArray[np.float64]  # (objects, times, 3)
    error_code: npt.NDArray[np.uint8]  # (objects, times); 0 = ok

    @property
    def ok(self) -> npt.NDArray[np.bool_]:
        return self.error_code == 0

    def failed_objects(self) -> npt.NDArray[np.bool_]:
        """Objects with an error at any requested time."""
        return np.asarray((self.error_code != 0).any(axis=1))

    def error_counts(self) -> dict[int, int]:
        """Number of objects per error code (an object's first error wins)."""
        failed = self.error_code != 0
        first = failed.argmax(axis=1)
        codes = self.error_code[np.arange(len(first)), first][failed.any(axis=1)]
        values, counts = np.unique(codes, return_counts=True)
        return {int(v): int(c) for v, c in zip(values, counts, strict=True)}

    def ecef_km(self) -> npt.NDArray[np.float64]:
        jd, fr = julian_dates(self.times)
        return teme_to_ecef(self.position_teme_km, gmst_rad(jd, fr)[np.newaxis, :])

    def geodetic(self) -> tuple[npt.NDArray[np.float64], ...]:
        """(latitude deg, longitude deg, height km), each (objects, times)."""
        return ecef_to_geodetic(self.ecef_km())


def propagate_catalog(
    catalog: pd.DataFrame, times: pd.DatetimeIndex | pd.Series | list[pd.Timestamp]
) -> Propagation:
    """Propagate every catalog row to every time. Failures are flagged, never dropped."""
    when = pd.DatetimeIndex(pd.to_datetime(times, utc=True))
    sats, init_failed = satrecs_from_catalog(catalog)
    n, t = len(sats), len(when)
    if n == 0:
        empty = np.zeros((0, t, 3))
        return Propagation(np.zeros(0, dtype=np.int64), when, empty, empty.copy(), np.zeros((0, t), np.uint8))

    jd, fr = julian_dates(when)
    error, position, velocity = SatrecArray(sats).sgp4(jd, fr)
    error = error.astype(np.uint8)
    error[init_failed, :] = INIT_FAILED

    result = Propagation(
        norad_id=catalog["norad_id"].to_numpy(dtype=np.int64),
        times=when,
        position_teme_km=position,
        velocity_teme_km_s=velocity,
        error_code=error,
    )
    if counts := result.error_counts():
        log.warning(
            "objects failed to propagate",
            extra={
                "objects": n,
                "times": t,
                "failed": int(result.failed_objects().sum()),
                "by_error": {ERROR_LABELS.get(k, str(k)): v for k, v in counts.items()},
            },
        )
    return result
