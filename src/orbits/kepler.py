"""Two-body orbits with secular J2 drift, vectorised over many objects.

Used where SGP4 is not needed or not available: sampling where an orbit spends
its time (shell risk index) and spreading breakup fragments for hours to weeks
(simulator). Angles are radians; distances km; speeds km/s.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from src.orbits.elements import EARTH_RADIUS_KM, MU_EARTH_KM3_S2

J2 = 1.08262668e-3
FloatArray = npt.NDArray[np.float64]


def solve_kepler(mean_anomaly: npt.ArrayLike, eccentricity: npt.ArrayLike, iterations: int = 12) -> FloatArray:
    """Eccentric anomaly E from M = E - e sin E (Newton; e < 1)."""
    m = np.asarray(mean_anomaly, dtype=float)
    e = np.asarray(eccentricity, dtype=float)
    big_e = np.where(e < 0.8, m, np.pi)
    for _ in range(iterations):
        big_e = big_e - (big_e - e * np.sin(big_e) - m) / (1.0 - e * np.cos(big_e))
    return big_e


def radius_at(a: npt.ArrayLike, e: npt.ArrayLike, mean_anomaly: npt.ArrayLike) -> FloatArray:
    big_e = solve_kepler(mean_anomaly, e)
    return np.asarray(a, dtype=float) * (1.0 - np.asarray(e, dtype=float) * np.cos(big_e))


def speed_at(a: npt.ArrayLike, r: npt.ArrayLike) -> FloatArray:
    """Vis-viva speed at radius r."""
    return np.sqrt(MU_EARTH_KM3_S2 * (2.0 / np.asarray(r, dtype=float) - 1.0 / np.asarray(a, dtype=float)))


def state_to_elements(r: FloatArray, v: FloatArray) -> dict[str, FloatArray]:
    """Classical elements (a, e, i, raan, argp, mean anomaly) from inertial states (n, 3)."""
    mu = MU_EARTH_KM3_S2
    rn = np.linalg.norm(r, axis=1)
    vn2 = np.sum(v * v, axis=1)
    h = np.cross(r, v)
    hn = np.linalg.norm(h, axis=1)
    node = np.cross(np.array([0.0, 0.0, 1.0]), h)
    nn = np.linalg.norm(node, axis=1)
    ev = ((vn2 - mu / rn)[:, None] * r - np.sum(r * v, axis=1)[:, None] * v) / mu
    e = np.linalg.norm(ev, axis=1)
    a = 1.0 / (2.0 / rn - vn2 / mu)
    inc = np.arccos(np.clip(h[:, 2] / hn, -1, 1))
    safe_nn = np.where(nn > 1e-12, nn, 1.0)
    safe_e = np.where(e > 1e-12, e, 1.0)
    raan = np.where(nn > 1e-12, np.arctan2(node[:, 1], node[:, 0]), 0.0) % (2 * np.pi)
    argp = np.arccos(np.clip(np.sum(node * ev, axis=1) / (safe_nn * safe_e), -1, 1))
    argp = np.where(ev[:, 2] < 0, 2 * np.pi - argp, argp)
    nu = np.arccos(np.clip(np.sum(ev * r, axis=1) / (safe_e * rn), -1, 1))
    nu = np.where(np.sum(r * v, axis=1) < 0, 2 * np.pi - nu, nu)
    # Near-circular orbits: measure from the node (argument of latitude) instead.
    circular = e < 1e-8
    u = np.arccos(np.clip(np.sum(node * r, axis=1) / (safe_nn * rn), -1, 1))
    u = np.where(r[:, 2] < 0, 2 * np.pi - u, u)
    argp = np.where(circular, 0.0, argp)
    nu = np.where(circular, u, nu)
    big_e = 2 * np.arctan2(np.sqrt(np.clip(1 - e, 0, None)) * np.sin(nu / 2), np.sqrt(1 + e) * np.cos(nu / 2))
    mean_anomaly = (big_e - e * np.sin(big_e)) % (2 * np.pi)
    return {"a": a, "e": e, "inc": inc, "raan": raan, "argp": argp, "mean_anomaly": mean_anomaly}


def elements_to_state(el: dict[str, FloatArray]) -> tuple[FloatArray, FloatArray]:
    """Inertial position and velocity (n, 3) from classical elements."""
    a, e, inc, raan, argp = (np.asarray(el[k], dtype=float) for k in ("a", "e", "inc", "raan", "argp"))
    big_e = solve_kepler(el["mean_anomaly"], e)
    nu = 2 * np.arctan2(np.sqrt(1 + e) * np.sin(big_e / 2), np.sqrt(1 - e) * np.cos(big_e / 2))
    p = a * (1 - e**2)
    r_mag = p / (1 + e * np.cos(nu))
    r_pf = np.stack([r_mag * np.cos(nu), r_mag * np.sin(nu), np.zeros_like(nu)], 1)
    v_pf = np.stack([-np.sin(nu), e + np.cos(nu), np.zeros_like(nu)], 1) * np.sqrt(MU_EARTH_KM3_S2 / p)[:, None]
    co, so, ci, si, cw, sw = np.cos(raan), np.sin(raan), np.cos(inc), np.sin(inc), np.cos(argp), np.sin(argp)
    rot = np.stack(
        [
            np.stack([co * cw - so * sw * ci, -co * sw - so * cw * ci, so * si], 1),
            np.stack([so * cw + co * sw * ci, -so * sw + co * cw * ci, -co * si], 1),
            np.stack([sw * si, cw * si, ci], 1),
        ],
        1,
    )
    return np.einsum("nij,nj->ni", rot, r_pf), np.einsum("nij,nj->ni", rot, v_pf)


def j2_secular_rates(a: FloatArray, e: FloatArray, inc: FloatArray) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Secular drift of RAAN, argument of perigee and mean anomaly (rad/s) under J2."""
    n = np.sqrt(MU_EARTH_KM3_S2 / a**3)
    p = a * (1 - e**2)
    k = 1.5 * J2 * (EARTH_RADIUS_KM / p) ** 2 * n
    raan_dot = -k * np.cos(inc)
    argp_dot = 0.5 * k * (5 * np.cos(inc) ** 2 - 1)
    m_dot = n + 0.5 * k * np.sqrt(1 - e**2) * (3 * np.cos(inc) ** 2 - 1)
    return raan_dot, argp_dot, m_dot


def propagate_j2(el: dict[str, FloatArray], dt_s: float) -> dict[str, FloatArray]:
    """Elements after dt seconds of two-body motion plus secular J2 (no drag)."""
    raan_dot, argp_dot, m_dot = j2_secular_rates(el["a"], el["e"], el["inc"])
    return {
        **el,
        "raan": (el["raan"] + raan_dot * dt_s) % (2 * np.pi),
        "argp": (el["argp"] + argp_dot * dt_s) % (2 * np.pi),
        "mean_anomaly": (el["mean_anomaly"] + m_dot * dt_s) % (2 * np.pi),
    }
