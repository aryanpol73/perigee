"""Run satellite.js (scripts/satjs) on catalog rows, to compare it with Python SGP4.

The browser globe propagates with satellite.js, so the two must agree. This
bridge sends the same OMM fields to both and is used by the validation script
and the parity test.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd

SATJS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "satjs"
SCRIPT = SATJS_DIR / "propagate.mjs"


def available() -> bool:
    return shutil.which("node") is not None and (SATJS_DIR / "node_modules" / "satellite.js").exists()


def omm_records(catalog: pd.DataFrame) -> list[dict[str, Any]]:
    """Catalog rows -> OMM dicts in the shape CelesTrak's JSON (and satellite.js) uses."""
    records = []
    for row in catalog.itertuples(index=False):
        records.append(
            {
                # Analyst objects have no name or designator; pandas stores those as NaN.
                "OBJECT_NAME": None if pd.isna(row.object_name) else row.object_name,
                "OBJECT_ID": None if pd.isna(row.object_id) else row.object_id,
                "EPOCH": pd.Timestamp(row.epoch).strftime("%Y-%m-%dT%H:%M:%S.%f"),
                "MEAN_MOTION": float(row.mean_motion),
                "ECCENTRICITY": float(row.eccentricity),
                "INCLINATION": float(row.inclination),
                "RA_OF_ASC_NODE": float(row.ra_of_asc_node),
                "ARG_OF_PERICENTER": float(row.arg_of_pericenter),
                "MEAN_ANOMALY": float(row.mean_anomaly),
                "EPHEMERIS_TYPE": 0,
                "CLASSIFICATION_TYPE": "U",
                "NORAD_CAT_ID": int(row.norad_id),
                "ELEMENT_SET_NO": 999,
                "REV_AT_EPOCH": 0,
                "BSTAR": float(row.bstar),
                "MEAN_MOTION_DOT": float(row.mean_motion_dot),
                "MEAN_MOTION_DDOT": float(row.mean_motion_ddot),
            }
        )
    return records


def propagate(
    catalog: pd.DataFrame, times: pd.DatetimeIndex
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """satellite.js TEME positions and geodetic (lat, lon, height), each (objects, times, 3).

    Entries satellite.js could not propagate are NaN.
    """
    payload = {
        "records": omm_records(catalog),
        "times": [t.strftime("%Y-%m-%dT%H:%M:%S.%fZ") for t in times],
    }
    completed = subprocess.run(
        ["node", str(SCRIPT)],
        input=json.dumps(payload, allow_nan=False),
        capture_output=True,
        text=True,
        cwd=SATJS_DIR,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"satellite.js failed: {completed.stderr.strip()[-2000:]}")
    results = json.loads(completed.stdout)["results"]
    shape = (len(results), len(times), 3)
    eci = np.full(shape, np.nan)
    geodetic = np.full(shape, np.nan)
    for i, result in enumerate(results):
        for j, (p, g) in enumerate(zip(result["eci"], result["geodetic"], strict=True)):
            if p is not None:
                eci[i, j] = p
                geodetic[i, j] = g
    return eci, geodetic
