"""Features for the conjunction model, from information available at prediction time T only.

Groups follow the build guide (8.2): geometry, orbit state, data quality,
physics, object type. The two objects are ordered "stale" (older element set at
T) and "fresh", rather than by catalog number, so each feature means the same
thing in every row.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.orbits.elements import apogee_altitude_km, perigee_altitude_km
from src.screening.screen import ACTIVE_STATUS

GEOMETRY = [
    "miss_km",
    "rel_speed_km_s",
    "hours_to_tca",
    "altitude_km",
    "approach_angle_deg",
    "miss_radial_km",
    "miss_intrack_km",
    "miss_cross_km",
]
PER_OBJECT = [
    "epoch_age_h",
    "dse_at_tca_d",
    "eccentricity",
    "inclination",
    "mean_motion",
    "perigee_km",
    "apogee_km",
    "bstar",
    "n_records_30d",
    "sma_jump_max_km",
    "sma_jump_median_km",
    "update_gap_median_h",
    "is_payload",
    "is_debris",
    "is_rocket_body",
    "rcs_size",
    "active",
]
PHYSICS = ["f107_obs", "f107_obs_81d", "ap_avg"]
PAIR = ["both_active", "same_family"]

FEATURES = (
    GEOMETRY
    + [f"stale_{c}" for c in PER_OBJECT]
    + [f"fresh_{c}" for c in PER_OBJECT]
    + PHYSICS
    + PAIR
)
RCS_CODE = {"SMALL": 1, "MEDIUM": 2, "LARGE": 3}


def _object_table(
    catalog: pd.DataFrame, satcat: pd.DataFrame | None, stats: pd.DataFrame, start: pd.Timestamp
) -> pd.DataFrame:
    t = catalog.set_index("norad_id")
    out = pd.DataFrame(index=t.index)
    out["epoch"] = t["epoch"]
    out["epoch_age_h"] = (start - t["epoch"]) / pd.Timedelta(hours=1)
    for c in ("eccentricity", "inclination", "mean_motion", "bstar"):
        out[c] = t[c].astype(float)
    out["perigee_km"] = perigee_altitude_km(t["mean_motion"], t["eccentricity"])
    out["apogee_km"] = apogee_altitude_km(t["mean_motion"], t["eccentricity"])
    s = stats.set_index("norad_id").reindex(out.index)
    out["n_records_30d"] = s["n_records"].fillna(1).astype(float)
    out["sma_jump_max_km"] = s["sma_jump_max_km"].fillna(0.0)
    out["sma_jump_median_km"] = s["sma_jump_median_km"].fillna(0.0)
    out["update_gap_median_h"] = s["update_gap_median_h"]
    if satcat is not None and not satcat.empty:
        sc = satcat.set_index("norad_id").reindex(out.index)
        kind = sc["object_type"]
        out["is_payload"] = (kind == "PAY").astype(float)
        out["is_debris"] = (kind == "DEB").astype(float)
        out["is_rocket_body"] = (kind == "R/B").astype(float)
        out["rcs_size"] = sc["rcs_size"].map(RCS_CODE).fillna(0).astype(float)
        out["active"] = sc["ops_status_code"].isin(ACTIVE_STATUS).astype(float)
    else:
        for c in ("is_payload", "is_debris", "is_rocket_body", "rcs_size", "active"):
            out[c] = np.nan
    return out


def _space_weather_at(space_weather: pd.DataFrame | None, start: pd.Timestamp) -> dict[str, float]:
    """Last fully observed day strictly before T (today's values are not final at T)."""
    if space_weather is None or space_weather.empty:
        return {c: np.nan for c in PHYSICS}
    day = start.tz_convert(None).normalize()
    obs = space_weather[(space_weather["f107_data_type"] == "OBS") & (space_weather["date"] < day)]
    if obs.empty:
        return {c: np.nan for c in PHYSICS}
    last = obs.sort_values("date").iloc[-1]
    return {"f107_obs": last["f107_obs"], "f107_obs_81d": last["f107_obs_center81"], "ap_avg": last["ap_avg"]}


def build_features(
    rows: pd.DataFrame,
    catalog: pd.DataFrame,
    satcat: pd.DataFrame | None,
    stats: pd.DataFrame,
    space_weather: pd.DataFrame | None,
    start: pd.Timestamp,
) -> pd.DataFrame:
    """Feature matrix (columns FEATURES) for dataset rows predicted at `start`."""
    objects = _object_table(catalog, satcat, stats, start)
    age_a = rows["norad_a"].map(objects["epoch_age_h"]).to_numpy()
    age_b = rows["norad_b"].map(objects["epoch_age_h"]).to_numpy()
    a_is_stale = age_a >= age_b
    stale_id = np.where(a_is_stale, rows["norad_a"], rows["norad_b"])
    fresh_id = np.where(a_is_stale, rows["norad_b"], rows["norad_a"])
    dse_a, dse_b = rows["dse_a"].to_numpy(), rows["dse_b"].to_numpy()

    out = pd.DataFrame(index=rows.index)
    out["miss_km"] = rows["miss_km"]
    out["rel_speed_km_s"] = rows["rel_speed_km_s"]
    out["hours_to_tca"] = (rows["tca"] - start) / pd.Timedelta(hours=1)
    for c in ("altitude_km", "approach_angle_deg", "miss_radial_km", "miss_intrack_km", "miss_cross_km"):
        out[c] = rows[c]
    for role, ids, dse in (("stale", stale_id, np.where(a_is_stale, dse_a, dse_b)), ("fresh", fresh_id, np.where(a_is_stale, dse_b, dse_a))):
        table = objects.reindex(ids)
        for c in PER_OBJECT:
            if c == "dse_at_tca_d":
                out[f"{role}_{c}"] = dse
            else:
                out[f"{role}_{c}"] = table[c].to_numpy()
    for c, v in _space_weather_at(space_weather, start).items():
        out[c] = v
    out["both_active"] = out["stale_active"] * out["fresh_active"]
    out["same_family"] = rows["same_family"].astype(float) if "same_family" in rows else 0.0
    return out[FEATURES]
