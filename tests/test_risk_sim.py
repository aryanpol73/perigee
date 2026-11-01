"""Shell risk index, breakup simulator and the two-body/J2 helpers they share."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.orbits.elements import EARTH_RADIUS_KM
from src.orbits.kepler import elements_to_state, j2_secular_rates, propagate_j2, state_to_elements
from src.risk.shells import ShellConfig, assign_sizes, band_occupancy, prepare_objects, shell_index, what_if
from src.sim.breakup import (
    LABEL,
    Collision,
    as_catalog_objects,
    fragment_count,
    simulate,
    specific_energy_j_per_g,
    spread,
)


def _objects(alt_km, inc_deg, e=0.0, kind="PAY", size="LARGE") -> pd.DataFrame:
    alt = np.atleast_1d(np.asarray(alt_km, dtype=float))
    n = len(alt)
    df = pd.DataFrame(
        {
            "norad_id": np.arange(n),
            "a_km": EARTH_RADIUS_KM + alt,
            "e": np.broadcast_to(e, n).astype(float),
            "inc_rad": np.radians(np.broadcast_to(inc_deg, n).astype(float)),
            "object_type": kind,
            "rcs_size": size,
        }
    )
    return assign_sizes(df)


# --- Two-body / J2 -------------------------------------------------------------


def test_state_elements_round_trip():
    rng = np.random.default_rng(0)
    el = {
        "a": rng.uniform(6_700, 8_000, 50),
        "e": rng.uniform(0.001, 0.2, 50),
        "inc": rng.uniform(0.1, 3.0, 50),
        "raan": rng.uniform(0, 2 * np.pi, 50),
        "argp": rng.uniform(0, 2 * np.pi, 50),
        "mean_anomaly": rng.uniform(0, 2 * np.pi, 50),
    }
    r, v = elements_to_state(el)
    r2, v2 = elements_to_state(state_to_elements(r, v))
    assert np.allclose(r, r2, atol=1e-6) and np.allclose(v, v2, atol=1e-9)


def test_j2_sun_synchronous_precession():
    # 700 km at 98.19 deg precesses about 360 deg per year (0.9856 deg/day).
    raan_dot, _, _ = j2_secular_rates(np.array([EARTH_RADIUS_KM + 700]), np.array([0.0]), np.radians([98.19]))
    assert np.degrees(raan_dot[0]) * 86_400 == pytest.approx(0.9856, abs=0.01)


def test_propagate_forward_then_back_returns_to_start():
    el = {k: np.array([v]) for k, v in dict(a=7_000.0, e=0.01, inc=1.0, raan=0.5, argp=0.3, mean_anomaly=1.0).items()}
    back = propagate_j2(propagate_j2(el, 86_400 * 10), -86_400 * 10)
    r0, _ = elements_to_state(el)
    r1, _ = elements_to_state(back)
    assert np.linalg.norm(r0 - r1) < 1e-6


# --- Shell index ---------------------------------------------------------------


def test_band_totals_match_catalog_and_densities_are_non_negative():
    rng = np.random.default_rng(1)
    objs = _objects(rng.uniform(300, 1_900, 500), rng.uniform(0, 180, 500), e=rng.uniform(0, 0.05, 500))
    res = shell_index(objs, ShellConfig(lo_km=0, hi_km=40_000, band_km=25))
    assert res["objects"].sum() + res.attrs["objects_outside_range"] == pytest.approx(len(objs))
    assert res.attrs["objects_outside_range"] == pytest.approx(0, abs=1e-9)
    assert (res["density_per_km3"] >= 0).all() and (res["risk_index"] >= 0).all()


def test_all_objects_in_one_band_puts_the_peak_there():
    objs = pd.concat([_objects(np.full(300, 812.0), 70.0), _objects(np.linspace(300, 1_900, 40), 50.0)])
    res = shell_index(objs.reset_index(drop=True))
    top = res.loc[res["risk_index"].idxmax()]
    assert top["alt_lo_km"] == 800 and top["risk_index"] == 100


def test_eccentric_orbit_spends_more_time_near_apogee():
    objs = _objects([1_100.0], 60.0, e=0.05)  # perigee ~730 km, apogee ~1,470 km
    _, band, frac = band_occupancy(objs, ShellConfig())
    edges = ShellConfig().edges()
    alt = edges[band]
    assert frac[alt >= 1_100].sum() > frac[alt < 1_100].sum()
    assert frac.sum() == pytest.approx(1.0)


def test_coplanar_prograde_equatorial_orbits_have_near_zero_relative_speed():
    res = shell_index(_objects(np.full(50, 610.0), 0.0))
    band = res.loc[res["objects"] > 0].iloc[0]
    assert band["mean_rel_speed_km_s"] < 0.05


def test_crossing_orbits_meet_at_several_km_per_second():
    objs = pd.concat([_objects(np.full(50, 810.0), 30.0), _objects(np.full(50, 810.0), 150.0)])
    band = shell_index(objs.reset_index(drop=True)).set_index("alt_lo_km").loc[800]
    assert 5 < band["mean_rel_speed_km_s"] < 16


def test_unknown_sizes_follow_the_mapping():
    df = pd.DataFrame(
        {"norad_id": [1, 2, 3], "mean_motion": [15.0] * 3, "eccentricity": [0.001] * 3, "inclination": [53.0] * 3}
    )
    satcat = pd.DataFrame({"norad_id": [1, 2, 3], "object_type": ["PAY", "DEB", "R/B"], "rcs_size": [None, None, "SMALL"]})
    default = prepare_objects(df, satcat)
    assert default["size_class"].tolist() == ["LARGE", "SMALL", "SMALL"]
    assert prepare_objects(df, satcat, "unknown_medium")["size_class"].tolist() == ["MEDIUM", "MEDIUM", "SMALL"]


def test_what_if_raises_the_index_where_objects_are_added():
    base = _objects(np.linspace(300, 1_900, 400), 60.0)
    added = _objects(np.full(200, 1_210.0), 80.0, kind="DEB", size="SMALL")
    res = what_if(base, added).set_index("alt_lo_km")
    assert res.loc[1_200, "risk_index_after"] > res.loc[1_200, "risk_index_before"]
    assert res.loc[300, "risk_index_after"] == pytest.approx(res.loc[300, "risk_index_before"], rel=0.2)


# --- Breakup simulator -----------------------------------------------------------


def test_catastrophic_threshold_and_fragment_count():
    assert specific_energy_j_per_g(950, 560, 11.7) > 40
    assert specific_energy_j_per_g(950, 1, 1.0) < 40
    assert fragment_count(1_510, 0.1) == pytest.approx(1_242, rel=0.01)


def test_fragment_mass_is_conserved():
    b = simulate(Collision())
    assert b.unresolved_mass_kg >= 0
    total = b.fragments["mass_kg"].sum() + b.unresolved_mass_kg
    lost_mass = b.input_mass_kg - total  # fragments that reentered at once carry the rest
    assert 0 <= lost_mass <= 0.2 * b.input_mass_kg
    assert b.fragments["mass_kg"].gt(0).all()


def test_non_catastrophic_collision_makes_far_fewer_fragments():
    small = simulate(Collision(impactor_mass_kg=1.0, rel_speed_km_s=1.0))
    assert not small.catastrophic and small.modelled_count < 20


def test_delta_v_follows_area_to_mass():
    f = simulate(Collision(target_mass_kg=5_000, impactor_mass_kg=3_000)).fragments
    resid = np.log10(f["dv_m_s"]) - (0.9 * np.log10(f["am_m2_kg"]) + 2.9)
    assert abs(resid.mean()) < 0.1 and resid.std() == pytest.approx(0.4, abs=0.1)


def test_same_seed_same_result():
    a, b = simulate(Collision(seed=7)).fragments, simulate(Collision(seed=7)).fragments
    pd.testing.assert_frame_equal(a, b)


def test_cloud_spreads_from_point_to_ring_to_shell():
    f = simulate(Collision(altitude_km=800, inclination_deg=74)).fragments
    point, ring, shell = spread(f, 0.25), spread(f, 24), spread(f, 24 * 365)
    assert point["along_track_coverage"] < 0.2 and point["cloud_radius_km"] < 500
    assert ring["along_track_coverage"] > 0.9 and ring["node_coverage"] < 0.2
    assert shell["node_coverage"] > 0.9


def test_bad_inputs_are_rejected():
    with pytest.raises(ValueError):
        Collision(altitude_km=50)
    with pytest.raises(ValueError):
        Collision(target_mass_kg=1e6)


def test_fragments_feed_the_what_if_index():
    f = simulate(Collision(altitude_km=800)).fragments
    base = _objects(np.linspace(300, 1_900, 400), 60.0)
    res = what_if(base, as_catalog_objects(f)).set_index("alt_lo_km")
    assert res.loc[800, "risk_index_after"] > res.loc[800, "risk_index_before"]
    assert LABEL.startswith("Illustrative")
