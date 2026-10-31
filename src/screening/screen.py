"""Conjunction screening: find every close approach below a threshold in a time window.

Coarse to fine, as brute force over all pairs and times is far too slow:

1. Coarse sieve. Every `step_s` seconds, all objects are placed in a KD-tree and
   pairs within a guaranteed radius are taken. Two objects that pass within
   `threshold` of each other somewhere in [t - step/2, t + step/2] are at most
   threshold + (|v1| + |v2|) * step/2 apart at t, so the radius
   threshold + v_max * step + margin cannot miss an approach.
2. Linear filter. Over a few seconds two nearby objects move almost in straight
   lines relative to each other (the error is differential gravity, bounded and
   added as `margin`). Each candidate's straight-line closest approach must be
   under threshold + margin.
3. One candidate per encounter. Consecutive steps for the same pair collapse to
   the step with the smallest linear distance, so docked or slowly drifting
   pairs do not flood the refinement.
4. Refinement. Newton iteration on range-rate = 0 with exact SGP4 states gives
   the time of closest approach (TCA) to well under a millisecond, the miss
   distance and the relative speed.

Positions come from public element sets with ~1 km error, so every result is an
estimated close approach based on public data, not a collision prediction.
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd
from scipy.spatial import cKDTree
from sgp4.api import Satrec, SatrecArray

from src.orbits.frames import julian_dates
from src.orbits.propagate import satrecs_from_catalog

log = logging.getLogger("perigee.screening")

MU_EARTH_KM3_S2 = 398_600.4418
MIN_RADIUS_KM = 6_378.137 + 150.0  # nothing in the catalog orbits lower for long
SECONDS_PER_DAY = 86_400.0

# CelesTrak SATCAT operational status codes counted as active (operational,
# partially operational, backup, spare, extended mission).
ACTIVE_STATUS = frozenset("+PBSX")

IntArray = npt.NDArray[np.int64]
FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class ScreenConfig:
    threshold_km: float = 5.0
    step_s: float = 20.0
    days: float = 7.0
    # "active": pairs with at least one active satellite (what SOCRATES screens);
    # "all": every pair.
    scope: str = "active"
    chunk_steps: int = 60
    workers: int = max(1, (os.cpu_count() or 2) - 1)
    # Below this relative speed at TCA two objects are flying together
    # (docked, formation flying): they are flagged, not treated as encounters.
    colocated_speed_km_s: float = 0.01
    refine_iterations: int = 12

    def margin_km(self, radius_km: float) -> float:
        """Bound on straight-line error over half a step for pairs within radius_km."""
        gradient = 3.0 * MU_EARTH_KM3_S2 / MIN_RADIUS_KM**3
        return 2.0 * 0.5 * gradient * radius_km * (self.step_s / 2.0) ** 2 + 0.1


@dataclass
class ScreenResult:
    events: pd.DataFrame
    config: ScreenConfig
    start: pd.Timestamp
    objects: int
    objects_with_sgp4_errors: int
    candidates: int
    seconds: float

    def summary(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "objects": self.objects,
            "objects_with_sgp4_errors": self.objects_with_sgp4_errors,
            "candidates_refined": self.candidates,
            "events": int(len(self.events)),
            "colocated_pairs": int(self.events["colocated"].sum()) if len(self.events) else 0,
            "seconds": round(self.seconds, 1),
            **{f"config_{k}": v for k, v in asdict(self.config).items()},
        }


# ---------------------------------------------------------------------------
# Pure numpy pieces (no SGP4), unit-tested directly.


def coarse_candidates(
    position: FloatArray,
    velocity: FloatArray,
    usable: npt.NDArray[np.bool_],
    active: npt.NDArray[np.bool_],
    config: ScreenConfig,
) -> tuple[IntArray, IntArray, FloatArray, FloatArray]:
    """Candidate pairs at one sample time.

    position, velocity: (objects, 3) at the sample time; usable masks objects that
    propagated. Returns (i, j, linear_miss_km, tau_s) with i < j as catalog indices,
    where tau_s is the straight-line TCA offset from the sample, within +/- step/2.
    """
    idx = np.flatnonzero(usable)
    empty = (np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0), np.zeros(0))
    if len(idx) < 2:
        return empty
    p, v = position[idx], velocity[idx]
    v_max = float(np.linalg.norm(v, axis=1).max())
    radius = config.threshold_km + v_max * config.step_s
    margin = config.margin_km(radius)
    pairs = cKDTree(p).query_pairs(radius + margin, output_type="ndarray")
    if len(pairs) == 0:
        return empty
    a, b = pairs[:, 0], pairs[:, 1]
    if config.scope == "active":
        keep = active[idx[a]] | active[idx[b]]
        a, b = a[keep], b[keep]
    dr = p[b] - p[a]
    dv = v[b] - v[a]
    vv = np.einsum("ij,ij->i", dv, dv)
    half = config.step_s / 2.0
    tau = np.clip(-np.einsum("ij,ij->i", dr, dv) / np.maximum(vv, 1e-12), -half, half)
    miss = np.linalg.norm(dr + dv * tau[:, None], axis=1)
    keep = miss < config.threshold_km + margin
    i, j = idx[a[keep]], idx[b[keep]]
    lo, hi = np.minimum(i, j), np.maximum(i, j)
    return lo.astype(np.int64), hi.astype(np.int64), miss[keep], tau[keep]


def encounter_minima(
    i: IntArray, j: IntArray, step: IntArray, miss: FloatArray
) -> npt.NDArray[np.int64]:
    """Indices of one candidate per encounter: the closest step in each run of consecutive steps."""
    if len(i) == 0:
        return np.zeros(0, dtype=np.int64)
    order = np.lexsort((step, j, i))
    i_s, j_s, k_s = i[order], j[order], step[order]
    new_run = np.ones(len(order), dtype=bool)
    new_run[1:] = (i_s[1:] != i_s[:-1]) | (j_s[1:] != j_s[:-1]) | (k_s[1:] - k_s[:-1] > 1)
    run_id = np.cumsum(new_run) - 1
    m_s = miss[order]
    # For each run, the position (in sorted order) of its smallest miss distance.
    best = np.full(run_id[-1] + 1, -1, dtype=np.int64)
    by_miss = np.lexsort((m_s, run_id))
    first_of_run = np.ones(len(by_miss), dtype=bool)
    first_of_run[1:] = run_id[by_miss[1:]] != run_id[by_miss[:-1]]
    best[run_id[by_miss[first_of_run]]] = by_miss[first_of_run]
    return order[best]


# ---------------------------------------------------------------------------
# Exact refinement with SGP4.


Epoch = tuple[float, float]  # window start as (whole Julian date, fraction), for full precision


def _state(sat: Satrec, jd0: Epoch, seconds: float) -> tuple[int, FloatArray, FloatArray]:
    error, r, v = sat.sgp4(jd0[0], jd0[1] + seconds / SECONDS_PER_DAY)
    return error, np.asarray(r), np.asarray(v)


def refine_tca(
    sat_a: Satrec, sat_b: Satrec, jd0: Epoch, t_guess_s: float, max_shift_s: float, iterations: int
) -> tuple[float, float, float, bool]:
    """Newton iteration on range-rate = 0. Returns (tca_s, miss_km, rel_speed_km_s, ok).

    jd0 is the window start; times are seconds after it. The TCA is kept within
    max_shift_s of the guess so a flat or slow encounter cannot wander into a
    different one.
    """
    t = t_guess_s
    lo, hi = t_guess_s - max_shift_s, t_guess_s + max_shift_s
    for _ in range(iterations):
        ea, ra, va = _state(sat_a, jd0, t)
        eb, rb, vb = _state(sat_b, jd0, t)
        if ea or eb:
            return t, float("nan"), float("nan"), False
        dr, dv = rb - ra, vb - va
        vv = float(dv @ dv)
        if vv < 1e-12:
            break  # no relative motion: any time is the TCA
        shift = -float(dr @ dv) / vv
        t_new = min(max(t + shift, lo), hi)
        converged = abs(t_new - t) < 1e-4
        t = t_new
        if converged:
            break
    ea, ra, va = _state(sat_a, jd0, t)
    eb, rb, vb = _state(sat_b, jd0, t)
    if ea or eb:
        return t, float("nan"), float("nan"), False
    return t, float(np.linalg.norm(rb - ra)), float(np.linalg.norm(vb - va)), True


# ---------------------------------------------------------------------------
# Parallel driver.

_WORKER: dict = {}


def _init_worker(catalog: pd.DataFrame, active: npt.NDArray[np.bool_], jd0: Epoch, config: ScreenConfig) -> None:
    sats, init_failed = satrecs_from_catalog(catalog)
    _WORKER.update(
        sats=sats,
        array=SatrecArray(sats),
        init_failed=init_failed,
        active=active,
        jd0=jd0,
        config=config,
    )


def _screen_chunk(first_step: int, last_step: int) -> tuple[pd.DataFrame, set[int], int]:
    """Screen sample steps [first_step, last_step). Returns events, failing objects, candidate count."""
    w = _WORKER
    cfg: ScreenConfig = w["config"]
    steps = np.arange(first_step, last_step)
    seconds = steps * cfg.step_s
    jd_whole, fr0 = w["jd0"]
    error, r, v = w["array"].sgp4(np.full(len(steps), jd_whole), fr0 + seconds / SECONDS_PER_DAY)
    usable_all = (error == 0) & ~w["init_failed"][:, None]
    failing = set(np.flatnonzero(~usable_all.all(axis=1)).tolist())

    found: list[tuple[IntArray, IntArray, FloatArray, FloatArray, IntArray]] = []
    for col, step in enumerate(steps):
        i, j, miss, tau = coarse_candidates(r[:, col], v[:, col], usable_all[:, col], w["active"], cfg)
        if len(i):
            found.append((i, j, miss, tau, np.full(len(i), step)))
    if not found:
        return _empty_events(), failing, 0

    i, j, miss, tau, step = (np.concatenate(parts) for parts in zip(*found, strict=True))
    pick = encounter_minima(i, j, step, miss)
    rows = []
    max_shift = 2.0 * cfg.step_s
    for c in pick:
        guess = step[c] * cfg.step_s + tau[c]
        tca, d, speed, ok = refine_tca(
            w["sats"][i[c]], w["sats"][j[c]], w["jd0"], guess, max_shift, cfg.refine_iterations
        )
        # A refinement that stopped on its shift limit did not reach a minimum: a
        # slow encounter split across a chunk boundary, whose true minimum is
        # found from the other side. Co-located pairs have no minimum to reach
        # and are kept (collapsed to one row per pair later).
        clamped = abs(tca - guess) >= max_shift - 1e-3
        if ok and d < cfg.threshold_km and (not clamped or speed < cfg.colocated_speed_km_s):
            rows.append((int(i[c]), int(j[c]), tca, d, speed))
    events = pd.DataFrame(rows, columns=["i", "j", "tca_s", "miss_km", "rel_speed_km_s"])
    return events, failing, len(pick)


def _empty_events() -> pd.DataFrame:
    return pd.DataFrame(columns=["i", "j", "tca_s", "miss_km", "rel_speed_km_s"]).astype(
        {"i": "int64", "j": "int64", "tca_s": "float64", "miss_km": "float64", "rel_speed_km_s": "float64"}
    )


def active_mask(catalog: pd.DataFrame, satcat: pd.DataFrame | None) -> npt.NDArray[np.bool_]:
    """Active satellites, from SATCAT operational status (objects not in SATCAT are not active)."""
    if satcat is None or satcat.empty:
        return np.zeros(len(catalog), dtype=bool)
    status = catalog["norad_id"].map(satcat.set_index("norad_id")["ops_status_code"])
    return status.isin(ACTIVE_STATUS).to_numpy()


def screen(
    catalog: pd.DataFrame,
    start: pd.Timestamp,
    config: ScreenConfig = ScreenConfig(),
    active: npt.NDArray[np.bool_] | None = None,
) -> ScreenResult:
    """Screen the catalog from `start` for `config.days`. Rows of `catalog` are the objects."""
    began = time.perf_counter()
    catalog = catalog.reset_index(drop=True)
    start = pd.Timestamp(start).tz_convert("UTC")
    if active is None:
        active = np.ones(len(catalog), dtype=bool)
    jd, fr = julian_dates([start])
    jd0: Epoch = (float(jd[0]), float(fr[0]))
    total_steps = int(np.ceil(config.days * SECONDS_PER_DAY / config.step_s)) + 1
    chunks = [
        (s, min(s + config.chunk_steps, total_steps)) for s in range(0, total_steps, config.chunk_steps)
    ]

    frames: list[pd.DataFrame] = []
    failing: set[int] = set()
    candidates = 0
    if config.workers <= 1:
        _init_worker(catalog, active, jd0, config)
        results = [_screen_chunk(a, b) for a, b in chunks]
    else:
        with ProcessPoolExecutor(
            max_workers=config.workers, initializer=_init_worker, initargs=(catalog, active, jd0, config)
        ) as pool:
            results = list(pool.map(_screen_chunk, *zip(*chunks, strict=True)))
    for events, bad, n in results:
        frames.append(events)
        failing |= bad
        candidates += n

    raw = pd.concat(frames, ignore_index=True) if frames else _empty_events()
    events = _finalise(raw, catalog, start, config)
    result = ScreenResult(
        events=events,
        config=config,
        start=start,
        objects=len(catalog),
        objects_with_sgp4_errors=len(failing),
        candidates=candidates,
        seconds=time.perf_counter() - began,
    )
    log.info("screening finished", extra=result.summary())
    return result


def _finalise(raw: pd.DataFrame, catalog: pd.DataFrame, start: pd.Timestamp, config: ScreenConfig) -> pd.DataFrame:
    """Window clip, deduplicate encounters found from two chunks, add identities and data age."""
    window_s = config.days * SECONDS_PER_DAY
    raw = raw[(raw["tca_s"] >= 0) & (raw["tca_s"] <= window_s)].copy()
    raw["colocated"] = raw["rel_speed_km_s"] < config.colocated_speed_km_s

    # The same encounter can be refined from both sides of a chunk boundary.
    raw = raw.sort_values(["i", "j", "tca_s"])
    gap = raw.groupby(["i", "j"])["tca_s"].diff()
    raw = raw[gap.isna() | (gap > 1.0)]
    # Objects flying together have no meaningful TCA: keep one row, the closest.
    co = raw[raw["colocated"]].sort_values(["miss_km", "tca_s"]).drop_duplicates(["i", "j"])
    raw = pd.concat([raw[~raw["colocated"]], co])

    ids = catalog["norad_id"].to_numpy(dtype=np.int64)
    epochs = pd.DatetimeIndex(pd.to_datetime(catalog["epoch"], utc=True))
    tca = pd.DatetimeIndex(start + pd.to_timedelta(raw["tca_s"].to_numpy(), unit="s"))
    i, j = raw["i"].to_numpy(), raw["j"].to_numpy()
    out = pd.DataFrame(
        {
            "norad_a": ids[i],
            "norad_b": ids[j],
            "tca": tca,
            "miss_km": raw["miss_km"].to_numpy(),
            "rel_speed_km_s": raw["rel_speed_km_s"].to_numpy(),
            "dse_a": (tca - epochs[i]) / pd.Timedelta(days=1),
            "dse_b": (tca - epochs[j]) / pd.Timedelta(days=1),
            "colocated": raw["colocated"].to_numpy(),
        }
    )
    # Catalog order is not id order; make norad_a < norad_b for stable keys.
    # Swap ids and ages separately so the integer column never passes through floats.
    swap = (out["norad_a"] > out["norad_b"]).to_numpy()
    for left, right in (("norad_a", "norad_b"), ("dse_a", "dse_b")):
        a, b = out[left].to_numpy().copy(), out[right].to_numpy().copy()
        out[left] = np.where(swap, b, a)
        out[right] = np.where(swap, a, b)
    return out.sort_values(["tca", "norad_a", "norad_b"]).reset_index(drop=True)
