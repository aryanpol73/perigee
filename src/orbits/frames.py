"""Coordinate frames: TEME (what SGP4 outputs) -> Earth-fixed -> geodetic.

SGP4 positions are in TEME, an Earth-centred inertial frame. To plot them on a
globe they must be rotated by Greenwich sidereal time into an Earth-fixed frame,
then converted to latitude/longitude/height on the WGS84 ellipsoid.

Simplifications (each worth well under 100 m at LEO, far below public TLE
error of ~1 km): UTC is used for UT1, and polar motion is ignored. These match
what satellite.js does in the browser, so both sides agree.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pandas as pd

FloatArray = npt.NDArray[np.float64]

WGS84_A_KM = 6_378.137
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)

UNIX_EPOCH_JD = 2_440_587.5
J2000_JD = 2_451_545.0
SECONDS_PER_DAY = 86_400.0


def julian_dates(times: pd.DatetimeIndex | pd.Series | npt.ArrayLike) -> tuple[FloatArray, FloatArray]:
    """UTC times -> (whole, fraction) Julian dates, split to keep full precision."""
    stamps = pd.DatetimeIndex(pd.to_datetime(times, utc=True))
    unix_days = stamps.as_unit("ns").asi8 / (SECONDS_PER_DAY * 1e9)
    whole = np.floor(unix_days)
    return whole + UNIX_EPOCH_JD, unix_days - whole


def gmst_rad(jd: npt.ArrayLike, fraction: npt.ArrayLike = 0.0) -> FloatArray:
    """Greenwich mean sidereal time, IAU-82 (Vallado's gstime, as used with SGP4)."""
    t_ut1 = ((np.asarray(jd, dtype=float) - J2000_JD) + np.asarray(fraction, dtype=float)) / 36_525.0
    seconds = (
        -6.2e-6 * t_ut1**3
        + 0.093104 * t_ut1**2
        + (876_600.0 * 3_600.0 + 8_640_184.812866) * t_ut1
        + 67_310.54841
    )
    return np.mod(np.deg2rad(seconds / 240.0), 2.0 * np.pi)


def teme_to_ecef(position_teme_km: npt.ArrayLike, gmst: npt.ArrayLike) -> FloatArray:
    """Rotate TEME vectors (..., 3) about the z axis by GMST (broadcast over leading axes)."""
    r = np.asarray(position_teme_km, dtype=float)
    theta = np.asarray(gmst, dtype=float)
    cos_t, sin_t = np.cos(theta), np.sin(theta)
    x, y, z = r[..., 0], r[..., 1], r[..., 2]
    return np.stack([cos_t * x + sin_t * y, -sin_t * x + cos_t * y, z], axis=-1)


def ecef_to_geodetic(position_ecef_km: npt.ArrayLike) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Earth-fixed (..., 3) -> WGS84 (latitude deg, longitude deg, height km)."""
    r = np.asarray(position_ecef_km, dtype=float)
    x, y, z = r[..., 0], r[..., 1], r[..., 2]
    lon = np.arctan2(y, x)
    p = np.hypot(x, y)
    lat = np.arctan2(z, p * (1.0 - WGS84_E2))
    for _ in range(6):  # converges to sub-millimetre in 3-4 iterations at any altitude
        sin_lat = np.sin(lat)
        n = WGS84_A_KM / np.sqrt(1.0 - WGS84_E2 * sin_lat**2)
        lat = np.arctan2(z + WGS84_E2 * n * sin_lat, p)
    sin_lat, cos_lat = np.sin(lat), np.cos(lat)
    n = WGS84_A_KM / np.sqrt(1.0 - WGS84_E2 * sin_lat**2)
    # This form of the height stays accurate near the poles, where p -> 0.
    height = p * cos_lat + z * sin_lat - WGS84_A_KM**2 / n
    return np.rad2deg(lat), np.rad2deg(lon), height


def geodetic_to_ecef(
    lat_deg: npt.ArrayLike, lon_deg: npt.ArrayLike, height_km: npt.ArrayLike
) -> FloatArray:
    lat = np.deg2rad(np.asarray(lat_deg, dtype=float))
    lon = np.deg2rad(np.asarray(lon_deg, dtype=float))
    h = np.asarray(height_km, dtype=float)
    n = WGS84_A_KM / np.sqrt(1.0 - WGS84_E2 * np.sin(lat) ** 2)
    return np.stack(
        [
            (n + h) * np.cos(lat) * np.cos(lon),
            (n + h) * np.cos(lat) * np.sin(lon),
            (n * (1.0 - WGS84_E2) + h) * np.sin(lat),
        ],
        axis=-1,
    )
