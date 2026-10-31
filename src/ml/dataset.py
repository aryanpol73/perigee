"""Build the labelled dataset: one row per (pair, prediction time).

Task (build guide 8.1): on day T, screening predicts a close approach at TCA a
few days ahead. Will it still look dangerous when better data arrives?

For each prediction time T:
1. take the catalog as known at T (newest element sets published by T);
2. screen it for `horizon_days` with a `candidate_km` threshold;
3. label each event with fresher data: for each object, the first element set
   with an epoch at or after the TCA among those published by
   T + horizon + label_delay; recompute the closest approach near the predicted
   TCA; label = 1 if it is under `label_km`.

Newer data is a better estimate, not ground truth. Rows where either object has
no newer element set than at T cannot be labelled and are dropped (counted).
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from src.orbits.frames import julian_dates
from src.orbits.propagate import propagate_catalog, satrec_from_elements
from src.screening.screen import ScreenConfig, active_mask, refine_tca, screen
from src.screening.socrates import family
from src.storage.elsets import ElsetHistory

log = logging.getLogger("perigee.ml.dataset")

EARTH_RADIUS_KM = 6_378.137
MU = 398_600.4418
ELEMENTS = (
    "mean_motion",
    "eccentricity",
    "inclination",
    "ra_of_asc_node",
    "arg_of_pericenter",
    "mean_anomaly",
    "bstar",
    "mean_motion_dot",
    "mean_motion_ddot",
)


@dataclass(frozen=True)
class DatasetConfig:
    horizon_days: float = 3.0
    candidate_km: float = 10.0
    label_km: float = 5.0
    label_delay_days: float = 1.0
    scope: str = "active"
    exclude_same_family: bool = True
    step_s: float = 20.0
    # Fewer, smaller workers than a one-off screen: dataset builds run for hours
    # next to a DuckDB history connection on an ordinary laptop.
    workers: int = max(1, min(6, (os.cpu_count() or 2) - 1))
    chunk_steps: int = 30
    refine_shift_s: float = 600.0
    manoeuvre_sma_km: float = 1.0
    max_age_days: float = 30.0
    # Uniform random sample of events per prediction time (before labelling), so
    # months of history fit in memory. Uniform keeps class balance, precision and
    # calibration unbiased. None keeps every event.
    max_rows_per_time: int | None = 20_000
    seed: int = 42

    @property
    def label_cutoff(self) -> pd.Timedelta:
        return pd.Timedelta(days=self.horizon_days + self.label_delay_days)


def _satrec(row: pd.Series, prefix: str = ""):
    return satrec_from_elements(
        int(row["norad_id"]),
        pd.Timestamp(row[f"{prefix}epoch"]),
        *(float(row[f"{prefix}{c}"]) for c in ELEMENTS),
    )


def _sma_km(mean_motion: pd.Series) -> np.ndarray:
    n = mean_motion.to_numpy(dtype=float) * 2 * np.pi / 86_400
    return np.cbrt(MU / n**2)


def _ric(dr: np.ndarray, r: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Split dr into radial, in-track and cross-track of the frame (r, v)."""
    radial = r / np.linalg.norm(r, axis=1, keepdims=True)
    cross = np.cross(r, v)
    cross /= np.linalg.norm(cross, axis=1, keepdims=True)
    in_track = np.cross(cross, radial)
    return np.stack([np.sum(dr * radial, 1), np.sum(dr * in_track, 1), np.sum(dr * cross, 1)], 1)


def geometry_at_tca(events: pd.DataFrame, catalog: pd.DataFrame, start: pd.Timestamp) -> pd.DataFrame:
    """Altitude, approach angle and miss components at the predicted TCA (data at T)."""
    sats = {int(r.norad_id): _satrec(pd.Series(r._asdict())) for r in catalog.itertuples(index=False)}
    jd, fr = julian_dates([start])
    jd0 = (float(jd[0]), float(fr[0]))
    t_s = ((events["tca"] - start) / pd.Timedelta(seconds=1)).to_numpy()
    ra, va, rb, vb = (np.zeros((len(events), 3)) for _ in range(4))
    for k, (a, b, t) in enumerate(zip(events["norad_a"], events["norad_b"], t_s, strict=True)):
        _, ra[k], va[k] = sats[int(a)].sgp4(jd0[0], jd0[1] + t / 86_400)
        _, rb[k], vb[k] = sats[int(b)].sgp4(jd0[0], jd0[1] + t / 86_400)
    ric = _ric(rb - ra, ra, va)
    cos = np.sum(va * vb, 1) / (np.linalg.norm(va, axis=1) * np.linalg.norm(vb, axis=1))
    return pd.DataFrame(
        {
            "altitude_km": (np.linalg.norm(ra, axis=1) + np.linalg.norm(rb, axis=1)) / 2 - EARTH_RADIUS_KM,
            "approach_angle_deg": np.degrees(np.arccos(np.clip(cos, -1, 1))),
            "miss_radial_km": np.abs(ric[:, 0]),
            "miss_intrack_km": np.abs(ric[:, 1]),
            "miss_cross_km": np.abs(ric[:, 2]),
        },
        index=events.index,
    )


def label_events(
    events: pd.DataFrame,
    catalog: pd.DataFrame,
    history: ElsetHistory,
    start: pd.Timestamp,
    config: DatasetConfig,
) -> pd.DataFrame:
    """Refreshed miss distance at each predicted TCA, from fresher element sets."""
    n = len(events)
    queries = pd.DataFrame(
        {
            "qid": np.arange(2 * n),
            "norad_id": np.concatenate([events["norad_a"].to_numpy(), events["norad_b"].to_numpy()]),
            "after": np.concatenate([events["tca"].to_numpy(), events["tca"].to_numpy()]),
        }
    )
    fresh = history.first_after(queries, start + config.label_cutoff).set_index("qid")
    fa, fb = fresh.loc[np.arange(n)].reset_index(drop=True), fresh.loc[np.arange(n, 2 * n)].reset_index(drop=True)

    at_t = catalog.set_index("norad_id")
    epoch_a = events["norad_a"].map(at_t["epoch"]).to_numpy()
    epoch_b = events["norad_b"].map(at_t["epoch"]).to_numpy()
    newer = (fa["r_epoch"].to_numpy() > epoch_a) & (fb["r_epoch"].to_numpy() > epoch_b)

    jd, fr = julian_dates([start])
    jd0 = (float(jd[0]), float(fr[0]))
    t_guess = ((events["tca"] - start) / pd.Timedelta(seconds=1)).to_numpy()
    miss = np.full(n, np.nan)
    tca_s = np.full(n, np.nan)
    cols = [f"r_{c}" for c in ELEMENTS]
    va, vb = fa[cols].to_numpy(dtype=float), fb[cols].to_numpy(dtype=float)
    ea, eb = fa["r_epoch"], fb["r_epoch"]
    ida, idb = events["norad_a"].to_numpy(), events["norad_b"].to_numpy()
    for k in np.flatnonzero(newer):
        sa = satrec_from_elements(int(ida[k]), pd.Timestamp(ea.iloc[k]), *va[k])
        sb = satrec_from_elements(int(idb[k]), pd.Timestamp(eb.iloc[k]), *vb[k])
        t, d, _, ok = refine_tca(sa, sb, jd0, t_guess[k], config.refine_shift_s, 12)
        if ok:
            miss[k], tca_s[k] = d, t

    d_sma = np.maximum(
        np.abs(_sma_km(fa["r_mean_motion"]) - _sma_km(events["norad_a"].map(at_t["mean_motion"]))),
        np.abs(_sma_km(fb["r_mean_motion"]) - _sma_km(events["norad_b"].map(at_t["mean_motion"]))),
    )
    out = pd.DataFrame(
        {
            "label_available": newer & ~np.isnan(miss),
            "refreshed_miss_km": miss,
            "refreshed_tca_shift_s": tca_s - t_guess,
            "refreshed_tca_clamped": np.abs(tca_s - t_guess) >= config.refine_shift_s - 1e-6,
            "refreshed_epoch_after_tca_h": np.minimum(
                (fa["r_epoch"] - events["tca"].reset_index(drop=True)) / pd.Timedelta(hours=1),
                (fb["r_epoch"] - events["tca"].reset_index(drop=True)) / pd.Timedelta(hours=1),
            ).to_numpy(),
            "sma_change_max_km": d_sma,
            "manoeuvre_suspected": d_sma > config.manoeuvre_sma_km,
        },
        index=events.index,
    )
    out["label"] = (out["refreshed_miss_km"] < config.label_km).astype("int8")
    return out


def build_for_time(
    history: ElsetHistory,
    start: pd.Timestamp,
    satcat: pd.DataFrame | None,
    config: DatasetConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Rows for one prediction time, plus the catalog at T (for features) and counts."""
    catalog = history.catalog_at(start, config.max_age_days)
    ok = propagate_catalog(catalog, [start]).ok[:, 0]
    catalog = catalog[ok].reset_index(drop=True)
    result = screen(
        catalog,
        start,
        ScreenConfig(
            threshold_km=config.candidate_km,
            step_s=config.step_s,
            days=config.horizon_days,
            scope=config.scope,
            workers=config.workers,
            chunk_steps=config.chunk_steps,
        ),
        active_mask(catalog, satcat),
    )
    events = result.events[~result.events["colocated"]].copy()
    names = catalog.set_index("norad_id")["object_name"]
    if satcat is not None and not satcat.empty:
        # Archived 2LE files carry no names: fall back to SATCAT's.
        names = names.fillna(satcat.drop_duplicates("norad_id").set_index("norad_id")["object_name"].reindex(names.index))
    same = events["norad_a"].map(names).map(family) == events["norad_b"].map(names).map(family)
    events["same_family"] = same.fillna(False).astype(bool) & events["norad_a"].map(names).map(family).notna()
    counts = {"prediction_time": start.isoformat(), "objects": len(catalog), "events": len(events)}
    if config.exclude_same_family:
        counts["excluded_same_family"] = int(events["same_family"].sum())
        events = events[~events["same_family"]]
    if config.max_rows_per_time is not None and len(events) > config.max_rows_per_time:
        counts["sampled_from"] = len(events)
        seed = config.seed + int(start.timestamp()) // 86_400  # different, reproducible draw per T
        events = events.sample(n=config.max_rows_per_time, random_state=seed)
    events = events.sort_values(["tca", "norad_a", "norad_b"]).reset_index(drop=True)
    if events.empty:
        return events, catalog, counts

    events = pd.concat([events, geometry_at_tca(events, catalog, start)], axis=1)
    labels = label_events(events, catalog, history, start, config)
    rows = pd.concat([events, labels], axis=1)
    rows.insert(0, "prediction_time", start)
    counts.update(
        labelled=int(rows["label_available"].sum()),
        unlabelled=int((~rows["label_available"]).sum()),
        positives=int(rows.loc[rows["label_available"], "label"].sum()),
    )
    log.info("built dataset rows", extra=counts)
    return rows[rows["label_available"]].reset_index(drop=True), catalog, counts


def prediction_times(
    history: ElsetHistory,
    config: DatasetConfig,
    every_days: float = 1.0,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
) -> list[pd.Timestamp]:
    """Prediction times (daily by default) whose labels can be complete within the history.

    start/end restrict them to one continuous stretch of history; the first
    `max_age_days` are skipped so every T sees a full catalog.
    """
    lo, hi = history.span()
    lo = max(lo, start) if start is not None else lo
    hi = min(hi, end) if end is not None else hi
    first = (lo + pd.Timedelta(days=min(config.max_age_days, 7))).ceil("D")
    last = hi - config.label_cutoff
    if last < first:
        return []
    return list(pd.date_range(first, last, freq=pd.Timedelta(days=every_days)))


def config_dict(config: DatasetConfig) -> dict:
    return asdict(config)
