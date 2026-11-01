"""Altitude-band (shell) risk index, build guide section 9.1.

Per band of altitude:
1. density: each orbit is sampled at equal time steps over one revolution and
   credits every band with the fraction of time it spends there; the sum over
   objects divided by the shell volume is n (objects per km^3);
2. relative speed: random pairs of objects in the band meet at a latitude both
   can reach; their horizontal velocities at that latitude (from inclination and
   vis-viva speed) give |v1 - v2|; the band stores the mean;
3. cross-section: SATCAT radar size class -> representative radius (an
   assumption, see SIZE_CLASSES); mean of pi * (r1 + r2)^2 over pairs;
4. rate = 1/2 * n^2 * sigma * v_rel * V (gas-kinetic), scaled to a 0-100 index
   (100 = riskiest band in this catalog).

The consequence-weighted variant weights each object by mass^0.75, the mass
scaling of the NASA breakup model's fragment count, so pairs of large intact
objects count for more. Both are indices, never probabilities: they ignore
manoeuvres, orbit error and that active satellites avoid each other.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.orbits.elements import EARTH_RADIUS_KM, semi_major_axis_km
from src.orbits.kepler import radius_at, speed_at

SECONDS_PER_YEAR = 365.25 * 86_400

# Radar cross-section class -> (representative radius m, mass kg). SATCAT classes
# are RCS < 0.1 m^2 (SMALL), 0.1-1 m^2 (MEDIUM), > 1 m^2 (LARGE); the radius is
# sqrt(RCS / pi) at a typical RCS in each class. Masses are rough class averages.
SIZE_CLASSES = {"SMALL": (0.1, 1.0), "MEDIUM": (0.4, 50.0), "LARGE": (1.5, 800.0)}
ROCKET_BODY_LARGE = (1.5, 1_500.0)
# SATCAT no longer publishes RCS for most new objects. Unknown payloads are mostly
# recent constellation satellites (large); unknown debris is mostly small.
UNKNOWN_BY_TYPE = {"PAY": "LARGE", "R/B": "LARGE", "DEB": "SMALL"}
SIZE_MAPPINGS = ("default", "unknown_medium")


@dataclass(frozen=True)
class ShellConfig:
    band_km: float = 25.0
    lo_km: float = 200.0
    hi_km: float = 2_000.0
    samples_per_orbit: int = 72
    pairs_per_band: int = 4_000
    rel_speed: str = "sampled"  # or "constant"
    constant_rel_speed_km_s: float = 10.0
    size_mapping: str = "default"  # or "unknown_medium"
    seed: int = 42

    def edges(self) -> np.ndarray:
        return np.arange(self.lo_km, self.hi_km + 1e-9, self.band_km)


def prepare_objects(catalog: pd.DataFrame, satcat: pd.DataFrame | None, size_mapping: str = "default") -> pd.DataFrame:
    """One row per object: semi-major axis, eccentricity, inclination, type, radius, mass."""
    if size_mapping not in SIZE_MAPPINGS:
        raise ValueError(f"size_mapping must be one of {SIZE_MAPPINGS}")
    out = pd.DataFrame(
        {
            "norad_id": catalog["norad_id"].astype("int64").to_numpy(),
            "a_km": semi_major_axis_km(catalog["mean_motion"]),
            "e": catalog["eccentricity"].astype(float).to_numpy(),
            "inc_rad": np.radians(catalog["inclination"].astype(float).to_numpy()),
        }
    )
    if satcat is not None and not satcat.empty:
        info = satcat.drop_duplicates("norad_id").set_index("norad_id")
        out["object_type"] = out["norad_id"].map(info["object_type"]).fillna("UNK").to_numpy()
        out["rcs_size"] = out["norad_id"].map(info["rcs_size"]).to_numpy()
    else:
        out["object_type"], out["rcs_size"] = "UNK", None
    return assign_sizes(out, size_mapping)


def assign_sizes(objects: pd.DataFrame, size_mapping: str = "default") -> pd.DataFrame:
    size = objects["rcs_size"].where(objects["rcs_size"].isin(list(SIZE_CLASSES)))
    if size_mapping == "default":
        fallback = objects["object_type"].map(UNKNOWN_BY_TYPE).fillna("MEDIUM")
    else:
        fallback = pd.Series("MEDIUM", index=objects.index)
    size = size.fillna(fallback)
    radius = size.map(lambda s: SIZE_CLASSES[s][0]).astype(float)
    mass = size.map(lambda s: SIZE_CLASSES[s][1]).astype(float)
    rb_large = (objects["object_type"] == "R/B") & (size == "LARGE")
    radius[rb_large], mass[rb_large] = ROCKET_BODY_LARGE
    return objects.assign(size_class=size, radius_m=radius, mass_kg=mass)


def band_occupancy(objects: pd.DataFrame, config: ShellConfig) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(object index, band index, time fraction) for every object-band pair with time in it.

    Samples are equally spaced in mean anomaly, i.e. in time. Time outside
    [lo_km, hi_km) is not returned, so fractions per object sum to <= 1.
    """
    s = config.samples_per_orbit
    m = (np.arange(s) + 0.5) / s * 2 * np.pi
    a = objects["a_km"].to_numpy()[:, None]
    e = objects["e"].to_numpy()[:, None]
    alt = radius_at(a, e, m[None, :]) - EARTH_RADIUS_KM
    n_bands = len(config.edges()) - 1
    band = np.floor((alt - config.lo_km) / config.band_km).astype(int)
    inside = (band >= 0) & (band < n_bands)
    obj = np.broadcast_to(np.arange(len(objects))[:, None], band.shape)[inside]
    key = obj.astype(np.int64) * n_bands + band[inside]
    uniq, counts = np.unique(key, return_counts=True)
    return uniq // n_bands, uniq % n_bands, counts / s


def _sampled_rel_speed(
    rng: np.random.Generator, inc: np.ndarray, v: np.ndarray, weights: np.ndarray, n_pairs: int
) -> float:
    """Mean |v1 - v2| of random pairs meeting at a latitude both orbits reach."""
    p = weights / weights.sum()
    i1, i2 = rng.choice(len(p), n_pairs, p=p), rng.choice(len(p), n_pairs, p=p)
    reach = np.minimum(inc, np.pi - inc)  # highest latitude an orbit reaches
    # Latitude drawn from the first object's time distribution (uniform argument of latitude).
    lat = np.arcsin(np.sin(reach[i1]) * np.sin(rng.uniform(0, 2 * np.pi, n_pairs)))
    ok = np.abs(lat) <= reach[i2]
    if not ok.any():
        return float("nan")
    i1, i2, lat = i1[ok], i2[ok], lat[ok]

    def heading(idx: np.ndarray) -> np.ndarray:
        # cos(inc) = cos(lat) * sin(azimuth); ascending or descending pass at random.
        az = np.arcsin(np.clip(np.cos(inc[idx]) / np.cos(lat), -1, 1))
        return np.where(rng.random(len(idx)) < 0.5, az, np.pi - az)

    d = heading(i1) - heading(i2)
    v1, v2 = v[i1], v[i2]
    return float(np.sqrt(np.maximum(v1**2 + v2**2 - 2 * v1 * v2 * np.cos(d), 0)).mean())


def shell_index(
    objects: pd.DataFrame, config: ShellConfig = ShellConfig(), rel_speeds: np.ndarray | None = None
) -> pd.DataFrame:
    """Risk index per altitude band (see module docstring).

    rel_speeds: mean relative speed per band to use instead of sampling (what-if
    comparisons reuse the baseline's, so sampling noise does not show as change).
    """
    rng = np.random.default_rng(config.seed)
    edges = config.edges()
    lo, hi = edges[:-1], edges[1:]
    n_bands = len(lo)
    obj, band, frac = band_occupancy(objects, config)

    radius_km = objects["radius_m"].to_numpy() / 1_000
    weight = objects["mass_kg"].to_numpy() ** 0.75
    weight = weight / weight.mean()
    a = objects["a_km"].to_numpy()
    inc = objects["inc_rad"].to_numpy()

    occupancy = np.bincount(band, frac, n_bands)
    weighted = np.bincount(band, frac * weight[obj], n_bands)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean_r = np.bincount(band, frac * radius_km[obj], n_bands) / occupancy
        mean_r2 = np.bincount(band, frac * radius_km[obj] ** 2, n_bands) / occupancy
    sigma = np.pi * (2 * mean_r2 + 2 * mean_r**2)

    rel_speed = np.full(n_bands, np.nan)
    for b in np.flatnonzero(occupancy > 0):
        if rel_speeds is not None and not np.isnan(rel_speeds[b]):
            rel_speed[b] = rel_speeds[b]
            continue
        if config.rel_speed == "constant":
            rel_speed[b] = config.constant_rel_speed_km_s
            continue
        members = band == b
        idx, w = obj[members], frac[members]
        r_mid = EARTH_RADIUS_KM + (lo[b] + hi[b]) / 2
        v = speed_at(a[idx], np.full(len(idx), r_mid))
        rel_speed[b] = _sampled_rel_speed(rng, inc[idx], v, w, config.pairs_per_band)

    volume = 4 / 3 * np.pi * ((EARTH_RADIUS_KM + hi) ** 3 - (EARTH_RADIUS_KM + lo) ** 3)
    density = occupancy / volume
    rate = np.nan_to_num(0.5 * density**2 * sigma * rel_speed * volume) * SECONDS_PER_YEAR
    rate_w = np.nan_to_num(0.5 * (weighted / volume) ** 2 * sigma * rel_speed * volume) * SECONDS_PER_YEAR
    out = pd.DataFrame(
        {
            "alt_lo_km": lo,
            "alt_hi_km": hi,
            "objects": occupancy,
            "density_per_km3": density,
            "mean_rel_speed_km_s": rel_speed,
            "mean_cross_section_m2": sigma * 1e6,
            "rate_per_year": rate,
            "weighted_rate_per_year": rate_w,
        }
    )
    out["risk_index"] = _scale(rate)
    out["risk_index_weighted"] = _scale(rate_w)
    out.attrs["objects_outside_range"] = float(len(objects) - occupancy.sum())
    return out


def _scale(x: np.ndarray, reference: float | None = None) -> np.ndarray:
    top = reference if reference is not None else (x.max() if len(x) else 0.0)
    return 100 * x / top if top > 0 else np.zeros_like(x)


def what_if(objects: pd.DataFrame, added: pd.DataFrame, config: ShellConfig = ShellConfig()) -> pd.DataFrame:
    """Index per band before and after adding objects (e.g. simulated fragments).

    Both are scaled to the baseline's riskiest band, so "after" can exceed 100.
    """
    before = shell_index(objects, config)
    after = shell_index(
        pd.concat([objects, added], ignore_index=True), config, before["mean_rel_speed_km_s"].to_numpy()
    )
    return pd.DataFrame(
        {
            "alt_lo_km": before["alt_lo_km"],
            "alt_hi_km": before["alt_hi_km"],
            "objects_before": before["objects"],
            "objects_after": after["objects"],
            "risk_index_before": before["risk_index"],
            "risk_index_after": _scale(after["rate_per_year"].to_numpy(), before["rate_per_year"].max()),
        }
    )
