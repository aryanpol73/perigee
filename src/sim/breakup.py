"""Simplified collision breakup simulator (build guide 9.3). Illustrative, not a prediction.

Fragments follow the NASA Standard Breakup Model for collisions (Johnson et al.
2001, "NASA's new breakup model of EVOLVE 4.0", Adv. Space Res. 28(9)):
* catastrophic when the impactor's kinetic energy per target mass exceeds
  40 J/g; then M = m_target + m_impactor, otherwise M = m_impactor * v^2 (km/s);
* fragments larger than Lc (m): N(Lc) = 0.1 * M^0.75 * Lc^-1.71;
* area A(Lc) and a bimodal area-to-mass distribution by size (spacecraft and
  rocket-body variants), coefficients from ESA's open implementation
  (github.com/esa/NASA-breakup-model-cpp, UtilityAreaMassRatio.h);
* extra speed: log10(dv m/s) ~ Normal(0.9 * log10(A/M) + 2.9, 0.4), random direction.

Simplifications, stated so nobody mistakes this for a prediction: both bodies
start on circular orbits through one point; each fragment inherits one parent's
velocity (chosen with probability proportional to mass); fragments are sampled
down to `min_lc_m` and mass beyond what they carry is reported as unresolved;
motion is two-body plus secular J2, no drag, so decay is not modelled.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from src.orbits.elements import EARTH_RADIUS_KM, MU_EARTH_KM3_S2
from src.orbits.kepler import elements_to_state, propagate_j2, solve_kepler, state_to_elements

LABEL = "Illustrative, simplified; not a prediction."
CATASTROPHIC_J_PER_G = 40.0
REENTRY_PERIGEE_KM = 100.0

PRESETS = {
    "defunct_satellite": {"mass_kg": 950.0, "kind": "SC"},  # Cosmos 2251-like
    "active_satellite": {"mass_kg": 560.0, "kind": "SC"},  # Iridium 33-like
    "rocket_body": {"mass_kg": 1_400.0, "kind": "RB"},  # SL-8 upper stage-like
}


@dataclass(frozen=True)
class Collision:
    altitude_km: float = 800.0
    inclination_deg: float = 74.0  # Cosmos 2251's plane; near-polar planes precess far slower
    target_mass_kg: float = 950.0
    impactor_mass_kg: float = 560.0
    rel_speed_km_s: float = 11.7
    target_kind: str = "SC"  # "SC" spacecraft or "RB" rocket body (area-to-mass variant)
    impactor_kind: str = "SC"
    min_lc_m: float = 0.1  # roughly the tracking limit
    max_fragments: int = 20_000  # simulated; the modelled count is reported regardless
    seed: int = 42

    def __post_init__(self) -> None:
        if not 150 <= self.altitude_km <= 2_000:
            raise ValueError("altitude_km must be between 150 and 2000")
        if not 0 <= self.inclination_deg <= 180:
            raise ValueError("inclination_deg must be between 0 and 180")
        for m in (self.target_mass_kg, self.impactor_mass_kg):
            if not 1 <= m <= 20_000:
                raise ValueError("masses must be between 1 and 20000 kg")
        if not 0.1 <= self.rel_speed_km_s <= 16:
            raise ValueError("rel_speed_km_s must be between 0.1 and 16")
        if self.target_kind not in ("SC", "RB") or self.impactor_kind not in ("SC", "RB"):
            raise ValueError("kinds must be 'SC' or 'RB'")
        if not 0.01 <= self.min_lc_m <= 1:
            raise ValueError("min_lc_m must be between 0.01 and 1")


# --- NASA Standard Breakup Model pieces ----------------------------------------


def specific_energy_j_per_g(m_target: float, m_impactor: float, v_km_s: float) -> float:
    return 0.5 * m_impactor * (v_km_s * 1_000) ** 2 / (m_target * 1_000)


def fragment_count(mass_term: float, lc_m: np.ndarray | float) -> np.ndarray | float:
    """Number of fragments larger than lc (collisions)."""
    return 0.1 * mass_term**0.75 * np.power(lc_m, -1.71)


def area_m2(lc: np.ndarray) -> np.ndarray:
    return np.where(lc < 0.00167, 0.540424 * lc**2, 0.556945 * lc**2.0047077)


def _ramp(x: np.ndarray, x0: float, x1: float, y0: float, y1: float) -> np.ndarray:
    """y0 below x0, y1 at or above x1, linear in between."""
    return np.where(x < x0, y0, np.where(x >= x1, y1, y0 + (y1 - y0) * (x - x0) / (x1 - x0)))


def _bimodal_params(kind: str, lam: np.ndarray) -> tuple[np.ndarray, ...]:
    if kind == "RB":
        alpha = _ramp(lam, -1.4, 0.0, 1.0, 0.5)
        mu1 = _ramp(lam, -0.5, 0.0, -0.45, -0.9)
        sigma1 = np.full_like(lam, 0.55)
        mu2 = np.full_like(lam, -0.9)
        # The ESA code writes this ramp as -0.28 - 0.1636(lam + 1), which is negative;
        # the published ramp is 0.28 -> 0.1 between lam = -1 and 0.1.
        sigma2 = _ramp(lam, -1.0, 0.1, 0.28, 0.1)
    else:
        alpha = _ramp(lam, -1.95, 0.55, 0.0, 1.0)
        mu1 = _ramp(lam, -1.1, 0.0, -0.6, -0.95)
        sigma1 = _ramp(lam, -1.3, -0.3, 0.1, 0.3)
        mu2 = _ramp(lam, -0.7, -0.1, -1.2, -2.0)
        sigma2 = _ramp(lam, -0.5, -0.3, 0.5, 0.3)
    return alpha, mu1, sigma1, mu2, sigma2


def area_to_mass(rng: np.random.Generator, lc: np.ndarray, kind: np.ndarray) -> np.ndarray:
    """A/M (m^2/kg): bimodal above 11 cm, small-fragment law below 8 cm, linear blend between."""
    lam = np.log10(lc)
    large = np.empty_like(lc)
    for k in ("SC", "RB"):
        m = kind == k
        if m.any():
            alpha, mu1, s1, mu2, s2 = _bimodal_params(k, lam[m])
            first = rng.random(m.sum()) < alpha
            large[m] = np.where(first, rng.normal(mu1, s1), rng.normal(mu2, s2))
    mu_s = _ramp(lam, -1.75, -1.25, -0.3, -1.0)
    sigma_s = np.where(lam <= -3.5, 0.2, 0.2 + 0.1333 * (lam + 3.5))
    small = rng.normal(mu_s, sigma_s)
    y1, y0 = 10.0**large, 10.0**small
    return np.where(lc > 0.11, y1, np.where(lc < 0.08, y0, y0 + (lc - 0.08) * (y1 - y0) / 0.03))


# --- Simulation ------------------------------------------------------------------


@dataclass
class Breakup:
    collision: Collision
    catastrophic: bool
    specific_energy_j_per_g: float
    modelled_count: float  # N(min_lc) from the model
    input_mass_kg: float
    unresolved_mass_kg: float  # in fragments below min_lc and in remnants
    fragments: pd.DataFrame  # lc_m, area_m2, am_m2_kg, mass_kg, dv_m_s, parent, elements
    lost: dict[str, int]  # fragments dropped immediately (reentry / escape)

    def summary(self) -> dict:
        return {
            "label": LABEL,
            "collision": asdict(self.collision),
            "catastrophic": self.catastrophic,
            "specific_energy_j_per_g": round(self.specific_energy_j_per_g, 1),
            "modelled_fragments_over_min_lc": round(self.modelled_count),
            "simulated_fragments": len(self.fragments),
            "input_mass_kg": self.input_mass_kg,
            "fragment_mass_kg": float(self.fragments["mass_kg"].sum()),
            "unresolved_mass_kg": self.unresolved_mass_kg,
            "lost_immediately": self.lost,
        }


def _parent_states(c: Collision) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Collision point and both parents' velocities: circular orbits through one point.

    Both parents have the given inclination, one heading north and one south
    (like Iridium 33 and Cosmos 2251, which met at high latitude). The meeting
    latitude is where their headings differ by the angle theta that gives
    |v_t - v_i| = rel_speed. If no latitude gives that angle (theta above
    2 * inclination), they meet at the equator with the impactor's heading rotated
    by theta, which puts it on a different inclination.
    """
    r = EARTH_RADIUS_KM + c.altitude_km
    v = np.sqrt(MU_EARTH_KM3_S2 / r)
    inc = np.radians(c.inclination_deg)
    theta = 2 * np.arcsin(min(c.rel_speed_km_s / (2 * v), 1.0))
    reach = min(inc, np.pi - inc)
    sign = 1.0 if inc <= np.pi / 2 else -1.0  # retrograde orbits head west
    if 0 < theta <= 2 * reach:
        az_t = sign * (np.pi - theta) / 2  # azimuth from north towards east
        az_i = sign * np.pi - az_t
        lat = np.arccos(np.clip(abs(np.cos(inc)) / np.sin(abs(az_t)), -1, 1))
    else:
        lat = 0.0
        az_t = np.arcsin(np.clip(np.cos(inc), -1, 1))
        az_i = az_t + theta
    pos = r * np.array([np.cos(lat), 0.0, np.sin(lat)])
    north, east = np.array([-np.sin(lat), 0.0, np.cos(lat)]), np.array([0.0, 1.0, 0.0])
    v_t = v * (np.cos(az_t) * north + np.sin(az_t) * east)
    v_i = v * (np.cos(az_i) * north + np.sin(az_i) * east)
    return pos, v_t, v_i


def simulate(c: Collision) -> Breakup:
    rng = np.random.default_rng(c.seed)
    m_t, m_p = max(c.target_mass_kg, c.impactor_mass_kg), min(c.target_mass_kg, c.impactor_mass_kg)
    kind_t, kind_p = (c.target_kind, c.impactor_kind) if c.target_mass_kg >= c.impactor_mass_kg else (c.impactor_kind, c.target_kind)
    energy = specific_energy_j_per_g(m_t, m_p, c.rel_speed_km_s)
    catastrophic = energy >= CATASTROPHIC_J_PER_G
    mass_term = m_t + m_p if catastrophic else m_p * c.rel_speed_km_s**2
    input_mass = m_t + m_p
    modelled = float(fragment_count(mass_term, c.min_lc_m))

    n = int(min(rng.poisson(modelled), c.max_fragments))
    lc_max = max(c.min_lc_m * 1.01, (m_t / 100.0) ** (1 / 3))  # about the target's size
    u_max = (lc_max / c.min_lc_m) ** -1.71
    lc = c.min_lc_m * rng.uniform(u_max, 1.0, n) ** (-1 / 1.71)  # truncated power law
    from_target = rng.random(n) < m_t / input_mass
    kind = np.where(from_target, kind_t, kind_p)
    am = area_to_mass(rng, lc, kind)
    area = area_m2(lc)
    mass = area / am
    # Mass conservation: drop fragments (random order) until within the input mass.
    order = rng.permutation(n)
    keep = order[np.cumsum(mass[order]) <= input_mass]
    keep.sort()
    lc, kind, am, area, mass, from_target = lc[keep], kind[keep], am[keep], area[keep], mass[keep], from_target[keep]

    dv = 10.0 ** rng.normal(0.9 * np.log10(am) + 2.9, 0.4) / 1_000  # km/s
    direction = rng.normal(size=(len(lc), 3))
    direction /= np.linalg.norm(direction, axis=1, keepdims=True)
    pos, v_t, v_i = _parent_states(c)
    vel = np.where(from_target[:, None], v_t, v_i) + dv[:, None] * direction
    el = state_to_elements(np.broadcast_to(pos, vel.shape).copy(), vel)
    perigee = el["a"] * (1 - el["e"]) - EARTH_RADIUS_KM
    escaped = (el["e"] >= 1) | (el["a"] <= 0)
    reentered = ~escaped & (perigee < REENTRY_PERIGEE_KM)
    stay = ~escaped & ~reentered

    frags = pd.DataFrame(
        {
            "lc_m": lc,
            "area_m2": area,
            "am_m2_kg": am,
            "mass_kg": mass,
            "dv_m_s": dv * 1_000,
            "parent": np.where(from_target, "target", "impactor"),
            **{k: v for k, v in el.items()},
        }
    )[stay].reset_index(drop=True)
    return Breakup(
        collision=c,
        catastrophic=bool(catastrophic),
        specific_energy_j_per_g=energy,
        modelled_count=modelled,
        input_mass_kg=input_mass,
        unresolved_mass_kg=float(input_mass - mass.sum()),
        fragments=frags,
        lost={"escaped": int(escaped.sum()), "reentered_at_once": int(reentered.sum())},
    )


def elements_of(fragments: pd.DataFrame) -> dict[str, np.ndarray]:
    return {k: fragments[k].to_numpy() for k in ("a", "e", "inc", "raan", "argp", "mean_anomaly")}


def positions_at(fragments: pd.DataFrame, hours: float) -> np.ndarray:
    """Inertial positions (km) of every fragment `hours` after the collision."""
    r, _ = elements_to_state(propagate_j2(elements_of(fragments), hours * 3_600))
    return r


def spread(fragments: pd.DataFrame, hours: float, bins: int = 36) -> dict[str, float]:
    """How far the cloud has spread: point, ring (along the orbit), shell (around Earth).

    A bin counts as covered when it holds at least a fifth of its share under a
    uniform spread, so a few stray fragments do not count.
    cloud_radius_km: median distance from the cloud's centre (small = still a point).
    along_track_coverage: covered share of 10-degree bins of argument of latitude
    (1 = the debris fills its orbits all the way round: a ring).
    node_coverage: covered share of 10-degree bins of the ascending node (grows as
    planes precess at different rates; 1 = orbits at every orientation: a shell).
    """
    el = propagate_j2(elements_of(fragments), hours * 3_600)
    r, _ = elements_to_state(el)
    centre = np.median(r, axis=0)
    u = (el["argp"] + _true_anomaly(el)) % (2 * np.pi)
    return {
        "hours": hours,
        "cloud_radius_km": float(np.median(np.linalg.norm(r - centre, axis=1))),
        "along_track_coverage": _coverage(u, bins),
        "node_coverage": _coverage(el["raan"], bins),
    }


def _coverage(angle: np.ndarray, bins: int) -> float:
    counts = np.bincount(np.floor(angle % (2 * np.pi) / (2 * np.pi) * bins).astype(int) % bins, minlength=bins)
    return float((counts >= max(1.0, 0.2 * len(angle) / bins)).mean())


def _true_anomaly(el: dict[str, np.ndarray]) -> np.ndarray:
    e = el["e"]
    big_e = solve_kepler(el["mean_anomaly"], e)
    return 2 * np.arctan2(np.sqrt(1 + e) * np.sin(big_e / 2), np.sqrt(1 - e) * np.cos(big_e / 2))


def altitude_distribution(fragments: pd.DataFrame, hours: float, band_km: float = 50.0) -> pd.Series:
    """Fragments per altitude band at one moment."""
    alt = np.linalg.norm(positions_at(fragments, hours), axis=1) - EARTH_RADIUS_KM
    bands = np.floor(alt / band_km) * band_km
    return pd.Series(bands).value_counts().sort_index()


def as_catalog_objects(fragments: pd.DataFrame) -> pd.DataFrame:
    """Fragments in the shape src.risk.shells expects (for the what-if index)."""
    size = np.where(fragments["area_m2"] < 0.1, "SMALL", np.where(fragments["area_m2"] < 1.0, "MEDIUM", "LARGE"))
    return pd.DataFrame(
        {
            "norad_id": -np.arange(1, len(fragments) + 1),
            "a_km": fragments["a"].to_numpy(),
            "e": fragments["e"].to_numpy(),
            "inc_rad": fragments["inc"].to_numpy(),
            "object_type": "DEB",
            "rcs_size": size,
            "size_class": size,
            "radius_m": np.sqrt(fragments["area_m2"].to_numpy() / np.pi),
            "mass_kg": fragments["mass_kg"].to_numpy(),
        }
    )
