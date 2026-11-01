import numpy as np
import pandas as pd
import pytest
from sgp4.api import WGS72, Satrec

from src.ingest.parse import parse_gp
from src.orbits.propagate import (
    ERROR_LABELS,
    INIT_FAILED,
    propagate_catalog,
    satrec_from_elements,
)

# Vallado et al. (2006) "Revisiting Spacetrack Report #3", test satellite 00005.
VALLADO_L1 = "1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753"
VALLADO_L2 = "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667"
VALLADO_EPOCH = pd.Timestamp("2000-01-01", tz="UTC") + pd.Timedelta(days=179.78495062 - 1)
VALLADO_ELEMENTS = {
    "norad_id": 5,
    "epoch": VALLADO_EPOCH,
    "mean_motion": 10.82419157,
    "eccentricity": 0.1859667,
    "inclination": 34.2682,
    "ra_of_asc_node": 348.7242,
    "arg_of_pericenter": 331.7664,
    "mean_anomaly": 19.3264,
    "bstar": 0.28098e-4,
    "mean_motion_dot": 0.00000023,
    "mean_motion_ddot": 0.0,
}


def test_vallado_known_answer_at_epoch():
    sat = satrec_from_elements(**VALLADO_ELEMENTS)
    error, r, v = sat.sgp4_tsince(0.0)
    assert error == 0
    np.testing.assert_allclose(r, [7022.46529266, -1400.08296755, 0.03995155], atol=1e-6)
    np.testing.assert_allclose(v, [1.893841015, 6.405893759, 4.534807250], atol=1e-8)


@pytest.mark.parametrize("minutes", [0.0, 360.0, 1440.0, 4320.0])
def test_elements_route_matches_tle_route(minutes):
    """Building from OMM fields must equal sgp4's own TLE parser, bit for bit."""
    ref = Satrec.twoline2rv(VALLADO_L1, VALLADO_L2, WGS72)
    ours = satrec_from_elements(**VALLADO_ELEMENTS)
    np.testing.assert_allclose(ours.sgp4_tsince(minutes)[1], ref.sgp4_tsince(minutes)[1], atol=1e-9)


def test_catalog_propagation_shapes_and_sanity(gp_bytes):
    catalog = parse_gp({"stations": gp_bytes})
    times = pd.date_range("2026-10-30T18:00", periods=5, freq="30min", tz="UTC")
    prop = propagate_catalog(catalog, times)

    assert prop.position_teme_km.shape == (20, 5, 3)
    assert prop.error_code.shape == (20, 5)
    assert prop.ok.all()
    radius = np.linalg.norm(prop.position_teme_km, axis=-1)
    assert ((radius > 6_378) & (radius < 9_000)).all()  # every station object is in LEO
    lat, _lon, height = prop.geodetic()
    assert (np.abs(lat) <= 52.0).all()  # ISS / CSS inclinations bound latitude
    assert ((height > 150) & (height < 2_500)).all()


def test_iss_height_matches_its_elements(gp_bytes):
    catalog = parse_gp({"stations": gp_bytes})
    iss = catalog[catalog["norad_id"] == 25544]
    times = pd.date_range("2026-10-29T13:31:42", periods=96, freq="1min", tz="UTC")
    _, _, height = propagate_catalog(iss, times).geodetic()
    assert 390 < height.min() < height.max() < 440


def test_six_and_nine_digit_catalog_numbers_propagate(gp_bytes):
    catalog = parse_gp({"stations": gp_bytes}).head(2).copy()
    catalog["norad_id"] = pd.array([100057, 799_500_001], dtype="Int64")
    prop = propagate_catalog(catalog, [pd.Timestamp("2026-10-30T18:00", tz="UTC")])
    assert prop.ok.all()
    assert list(prop.norad_id) == [100057, 799_500_001]


def test_failures_are_flagged_and_counted_not_dropped(gp_bytes):
    catalog = parse_gp({"stations": gp_bytes}).head(3).copy()
    catalog.loc[1, "mean_motion"] = 17.5  # orbit below the Earth's surface
    prop = propagate_catalog(catalog, pd.date_range("2026-10-30T18:00", periods=2, freq="h", tz="UTC"))

    assert len(prop.norad_id) == 3
    assert list(prop.failed_objects()) == [False, True, False]
    assert prop.error_counts() == {INIT_FAILED: 1}
    assert INIT_FAILED in ERROR_LABELS


def test_decayed_object_reports_sgp4_error(gp_bytes):
    catalog = parse_gp({"stations": gp_bytes}).head(1).copy()
    catalog.loc[0, "bstar"] = 0.5  # enormous drag: decays within days
    times = pd.DatetimeIndex([catalog.loc[0, "epoch"] + pd.Timedelta(days=d) for d in (0, 30)])
    prop = propagate_catalog(catalog, times)
    assert prop.error_code[0, 0] == 0
    assert prop.error_code[0, 1] != 0
    assert prop.error_counts() == {int(prop.error_code[0, 1]): 1}


def test_empty_catalog(gp_bytes):
    catalog = parse_gp({"stations": gp_bytes}).head(0)
    prop = propagate_catalog(catalog, [pd.Timestamp("2026-10-30", tz="UTC")])
    assert prop.position_teme_km.shape == (0, 1, 3)
    assert prop.error_counts() == {}
