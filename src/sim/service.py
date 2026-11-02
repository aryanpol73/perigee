"""One simulator run as the app shows it: the breakup, fragment positions at the
requested moments, their spread, and the what-if change to the shell index.

Shared by the API (POST /v1/simulate) and the static site, which runs this same
file in the browser with Pyodide; so it imports only numpy, pandas and the
physics modules, never FastAPI or pydantic. Returns plain dicts and lists in the
shape of src.api.schemas.SimulateResponse.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.orbits.frames import ecef_to_geodetic, gmst_rad, julian_dates
from src.risk.shells import ShellConfig, shell_index
from src.sim.breakup import Collision, as_catalog_objects, positions_at, simulate, spread

MAX_FRAGMENTS = 5_000
MAX_HOURS = 8_760.0


class SimulationInputError(ValueError):
    pass


@dataclass
class Catalog:
    """The catalog in the shell index's shape, plus the baseline index (computed once)."""

    objects: pd.DataFrame
    snapshot_time: pd.Timestamp
    _base: pd.DataFrame | None = field(default=None, repr=False)

    @property
    def base(self) -> pd.DataFrame:
        if self._base is None:
            self._base = shell_index(self.objects, ShellConfig())
        return self._base


def run(req: dict, catalog: Catalog) -> dict:
    """`req` has the fields of SimulateRequest (already bounds-checked by the caller's schema)."""
    times = [float(t) for t in req["times_h"]]
    if any(t < 0 or t > MAX_HOURS for t in times):
        raise SimulationInputError("times_h values must be between 0 and 8760 hours.")
    collision = Collision(
        altitude_km=req["altitude_km"], inclination_deg=req["inclination_deg"], target_mass_kg=req["target_mass_kg"],
        impactor_mass_kg=req["impactor_mass_kg"], rel_speed_km_s=req["rel_speed_km_s"], target_kind=req["target_kind"],
        impactor_kind=req["impactor_kind"], seed=req["seed"], max_fragments=MAX_FRAGMENTS,
    )
    b = simulate(collision)
    frags = b.fragments
    rng = np.random.default_rng(req["seed"])
    pick = rng.choice(len(frags), min(req["max_points"], len(frags)), replace=False) if len(frags) else np.array([], int)
    sub = frags.iloc[np.sort(pick)]
    t0 = catalog.snapshot_time
    frames = []
    for hours in sorted(times):
        r = positions_at(sub, hours) if len(sub) else np.zeros((0, 3))
        jd, fr = julian_dates([t0 + pd.Timedelta(hours=hours)])
        g = float(gmst_rad(jd, fr)[0])
        rot = np.array([[np.cos(g), np.sin(g), 0], [-np.sin(g), np.cos(g), 0], [0, 0, 1]])
        lat, lon, alt = ecef_to_geodetic(r @ rot.T) if len(r) else (np.array([]),) * 3
        spr = spread(frags, hours) if len(frags) else {"along_track_coverage": 0.0, "node_coverage": 0.0}
        frames.append(
            {
                "hours": hours,
                "along_track_coverage": spr["along_track_coverage"],
                "node_coverage": spr["node_coverage"],
                "points": [(round(float(a), 3), round(float(o), 3), round(float(h), 1)) for a, o, h in zip(lat, lon, alt, strict=True)],
            }
        )
    base = catalog.base
    after = shell_index(
        pd.concat([catalog.objects, as_catalog_objects(frags)], ignore_index=True),
        ShellConfig(),
        base["mean_rel_speed_km_s"].to_numpy(),
    )
    top = base["rate_per_year"].max()
    after_idx = 100 * after["rate_per_year"] / top if top > 0 else after["rate_per_year"] * 0
    # The bands whose index rises most relative to their own level (near-empty bands left out).
    populated = base["risk_index"] > 0.01
    change = (after_idx / base["risk_index"].where(populated) - 1).fillna(-np.inf)
    rows = change.nlargest(8).index.sort_values()
    summary = b.summary()
    return {
        "catastrophic": b.catastrophic,
        "specific_energy_j_per_g": summary["specific_energy_j_per_g"],
        "modelled_fragments": summary["modelled_fragments_over_min_lc"],
        "simulated_fragments": len(frags),
        "points_returned": len(sub),
        "input_mass_kg": b.input_mass_kg,
        "fragment_mass_kg": summary["fragment_mass_kg"],
        "unresolved_mass_kg": b.unresolved_mass_kg,
        "collision_time": t0.isoformat(),
        "frames": frames,
        "what_if": [
            {"alt_lo_km": float(base.loc[i, "alt_lo_km"]), "alt_hi_km": float(base.loc[i, "alt_hi_km"]),
             "risk_index_before": float(base.loc[i, "risk_index"]), "risk_index_after": float(after_idx[i])}
            for i in rows
        ],
    }
