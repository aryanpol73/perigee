import numpy as np
import pytest

from src.orbits.elements import (
    apogee_altitude_km,
    orbit_regime,
    perigee_altitude_km,
    semi_major_axis_km,
)


def test_geostationary_semi_major_axis():
    # One sidereal day per revolution -> the well-known 42,164 km GEO radius.
    assert semi_major_axis_km(1.00273791) == pytest.approx(42_164, abs=2)


def test_iss_altitude_is_in_low_earth_orbit():
    perigee = perigee_altitude_km(15.49100842, 0.00070497)
    apogee = apogee_altitude_km(15.49100842, 0.00070497)
    assert 380 < perigee < apogee < 440


def test_altitudes_are_never_below_perigee_ordering():
    n = np.array([15.5, 14.2, 2.0, 1.0027])
    e = np.array([0.0, 0.01, 0.7, 0.0002])
    assert np.all(perigee_altitude_km(n, e) <= apogee_altitude_km(n, e))


def test_orbit_regimes():
    n = np.array([15.49, 2.0056, 2.0057, 1.00273791])
    e = np.array([0.0007, 0.01, 0.72, 0.0002])
    perigee = perigee_altitude_km(n, e)
    apogee = apogee_altitude_km(n, e)
    # ISS, GPS, Molniya, GEO
    assert list(orbit_regime(perigee, apogee, e)) == ["LEO", "MEO", "HEO", "GEO"]


def test_leo_crossing_debris_counts_as_leo():
    # FREGAT DEB 49271: perigee ~748 km, apogee ~2,218 km.
    n, e = 12.455563, 0.093488
    perigee, apogee = perigee_altitude_km(n, e), apogee_altitude_km(n, e)
    assert apogee > 2_000
    assert orbit_regime(perigee, apogee, e)[()] == "LEO"
