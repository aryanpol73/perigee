import numpy as np
import pandas as pd
import pytest

from src.orbits.frames import (
    WGS84_A_KM,
    WGS84_F,
    ecef_to_geodetic,
    geodetic_to_ecef,
    gmst_rad,
    julian_dates,
    teme_to_ecef,
)


def test_julian_date_of_j2000():
    jd, fr = julian_dates(pd.DatetimeIndex(["2000-01-01T12:00:00"], tz="UTC"))
    assert jd[0] + fr[0] == pytest.approx(2_451_545.0, abs=1e-9)


def test_julian_date_keeps_sub_millisecond_precision():
    t = pd.DatetimeIndex(["2026-10-30T12:00:00.000000", "2026-10-30T12:00:00.000100"], tz="UTC")
    jd, fr = julian_dates(t)
    seconds = ((jd[1] - jd[0]) + (fr[1] - fr[0])) * 86_400
    assert seconds == pytest.approx(1e-4, abs=1e-7)


def test_gmst_at_j2000_matches_published_value():
    # GMST at 2000-01-01 12:00 UT1 is 280.46061837 degrees (IAU-82).
    assert np.rad2deg(gmst_rad(2_451_545.0)) == pytest.approx(280.46061837, abs=1e-6)


def test_gmst_advances_one_sidereal_rotation_per_sidereal_day():
    sidereal_day = 0.99726956633
    a, b = gmst_rad(2_460_000.5), gmst_rad(2_460_000.5 + sidereal_day)
    assert (b - a + np.pi) % (2 * np.pi) - np.pi == pytest.approx(0.0, abs=1e-7)


def test_teme_to_ecef_preserves_length_and_z():
    rng = np.random.default_rng(0)
    r = rng.normal(size=(50, 3)) * 7_000
    out = teme_to_ecef(r, rng.uniform(0, 2 * np.pi, size=50))
    np.testing.assert_allclose(np.linalg.norm(out, axis=1), np.linalg.norm(r, axis=1))
    np.testing.assert_allclose(out[:, 2], r[:, 2])


def test_teme_to_ecef_at_zero_gmst_is_identity():
    r = np.array([[7000.0, -1200.0, 300.0]])
    np.testing.assert_allclose(teme_to_ecef(r, 0.0), r)


@pytest.mark.parametrize(
    ("xyz", "lat", "lon", "h"),
    [
        ((WGS84_A_KM, 0.0, 0.0), 0.0, 0.0, 0.0),
        ((0.0, WGS84_A_KM + 400.0, 0.0), 0.0, 90.0, 400.0),
        ((0.0, 0.0, WGS84_A_KM * (1 - WGS84_F)), 90.0, 0.0, 0.0),
        ((0.0, 0.0, -(WGS84_A_KM * (1 - WGS84_F) + 35_786.0)), -90.0, 0.0, 35_786.0),
    ],
)
def test_geodetic_known_points(xyz, lat, lon, h):
    got_lat, got_lon, got_h = ecef_to_geodetic(np.array(xyz))
    assert got_lat == pytest.approx(lat, abs=1e-9)
    assert got_lon == pytest.approx(lon, abs=1e-9)
    assert got_h == pytest.approx(h, abs=1e-6)


def test_geodetic_round_trip_from_ground_to_geo():
    rng = np.random.default_rng(1)
    lat = rng.uniform(-90, 90, 500)
    lon = rng.uniform(-180, 180, 500)
    h = rng.uniform(0, 40_000, 500)
    got_lat, got_lon, got_h = ecef_to_geodetic(geodetic_to_ecef(lat, lon, h))
    np.testing.assert_allclose(got_lat, lat, atol=1e-9)
    np.testing.assert_allclose(got_lon, lon, atol=1e-9)
    np.testing.assert_allclose(got_h, h, atol=1e-6)  # 1 mm


def test_frames_agree_with_astropy():
    """Our simplified TEME -> Earth-fixed rotation vs astropy's full TEME -> ITRS."""
    try:
        from astropy import units as u
        from astropy.coordinates import ITRS, TEME, CartesianRepresentation
        from astropy.time import Time
        from astropy.utils import iers
    except ImportError as exc:  # dev dependency; also fails to load from very long paths on Windows
        pytest.skip(f"astropy unavailable: {exc}")
    iers.conf.auto_download = False

    times = pd.date_range("2026-10-30T12:00", periods=24, freq="h", tz="UTC")
    r = np.tile([6_800.0, 0.0, 0.0], (24, 1))
    r[:, 1] = np.linspace(-3_000, 3_000, 24)
    jd, fr = julian_dates(times)
    ours = teme_to_ecef(r, gmst_rad(jd, fr))
    obstime = Time(times.to_pydatetime(), scale="utc")
    teme = TEME(CartesianRepresentation(r.T * u.km), obstime=obstime, representation_type="cartesian")
    theirs = np.asarray(teme.transform_to(ITRS(obstime=obstime)).cartesian.xyz.to_value(u.km)).T
    assert np.linalg.norm(ours - theirs, axis=1).max() < 0.1  # under 100 m
