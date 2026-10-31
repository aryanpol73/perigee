"""Keplerian quantities derived from mean elements.

These are approximations good enough for sorting objects into altitude bands.
Precise positions come from SGP4 (Phase 2).
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

MU_EARTH_KM3_S2 = 398_600.4418
EARTH_RADIUS_KM = 6_378.137
SECONDS_PER_DAY = 86_400.0

LEO_MAX_ALT_KM = 2_000.0
GEO_ALT_KM = 35_786.0
GEO_BAND_KM = 1_000.0
HEO_MIN_ECC = 0.25

FloatArray = npt.NDArray[np.float64]


def semi_major_axis_km(mean_motion_rev_per_day: npt.ArrayLike) -> FloatArray:
    """Semi-major axis from mean motion via Kepler's third law."""
    n = np.asarray(mean_motion_rev_per_day, dtype=float) * 2.0 * np.pi / SECONDS_PER_DAY
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.cbrt(MU_EARTH_KM3_S2 / n**2)


def perigee_altitude_km(mean_motion: npt.ArrayLike, eccentricity: npt.ArrayLike) -> FloatArray:
    a = semi_major_axis_km(mean_motion)
    return a * (1.0 - np.asarray(eccentricity, dtype=float)) - EARTH_RADIUS_KM


def apogee_altitude_km(mean_motion: npt.ArrayLike, eccentricity: npt.ArrayLike) -> FloatArray:
    a = semi_major_axis_km(mean_motion)
    return a * (1.0 + np.asarray(eccentricity, dtype=float)) - EARTH_RADIUS_KM


def orbit_regime(
    perigee_km: npt.ArrayLike, apogee_km: npt.ArrayLike, eccentricity: npt.ArrayLike
) -> npt.NDArray[np.str_]:
    """Label each orbit LEO, MEO, GEO or HEO (highly elliptical).

    Near-circular orbits whose perigee is in LEO count as LEO even if the apogee
    is a little above 2,000 km: they pass through the crowded LEO shells every orbit.
    """
    perigee = np.asarray(perigee_km, dtype=float)
    apogee = np.asarray(apogee_km, dtype=float)
    ecc = np.asarray(eccentricity, dtype=float)
    mean_alt = (perigee + apogee) / 2.0
    return np.select(
        [
            ecc >= HEO_MIN_ECC,
            perigee <= LEO_MAX_ALT_KM,
            np.abs(mean_alt - GEO_ALT_KM) <= GEO_BAND_KM,
        ],
        ["HEO", "LEO", "GEO"],
        default="MEO",
    )
