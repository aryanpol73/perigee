"""The published results bundle, loaded once into memory (build guide 10.1).

The API never reads Parquet per request, downloads data or runs screening; a
new bundle means a restart (or `ResultsStore.load` again).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from src.orbits.elements import EARTH_RADIUS_KM
from src.results.bundle import POINTER
from src.risk.shells import assign_sizes
from src.storage import parquet_store as store


class NoResults(RuntimeError):
    pass


@dataclass
class ResultsStore:
    path: Path
    meta: dict
    model: dict | None
    objects: pd.DataFrame
    conjunctions: pd.DataFrame
    shells: pd.DataFrame
    shells_history: pd.DataFrame

    @classmethod
    def load(cls, results_dir: Path) -> ResultsStore:
        pointer = results_dir / POINTER
        if not pointer.exists():
            raise NoResults(f"No published results in {results_dir}. Run: python -m src.main publish")
        path = results_dir / json.loads(pointer.read_text())["path"]
        con = store.connect()
        try:
            def read(name: str) -> pd.DataFrame:
                return con.execute(f"SELECT * FROM read_parquet('{store.sql_path(path / name)}')").df()

            objects = read("objects.parquet")
            conj = read("conjunctions.parquet")
            shells = read("shells.parquet")
            history = read("shells_history.parquet")
        finally:
            con.close()
        objects["norad_id"] = objects["norad_id"].astype("int64")
        # Index by key for lookups; unnamed so sorting by the column stays unambiguous.
        objects = objects.set_index("norad_id", drop=False).rename_axis(None)
        conj = conj.set_index("id", drop=False).rename_axis(None)
        return cls(
            path=path,
            meta=json.loads((path / "meta.json").read_text()),
            model=json.loads((path / "model.json").read_text()),
            objects=objects,
            conjunctions=conj,
            shells=shells,
            shells_history=history,
        )

    @property
    def snapshot_time(self) -> pd.Timestamp:
        return pd.Timestamp(self.meta["snapshot_time"])

    def orbit_file(self) -> Path:
        return self.path / self.meta["orbit_file"]

    def shell_objects(self) -> pd.DataFrame:
        """Objects in the shape the shell index needs (for the simulator's what-if)."""
        o = self.objects
        rp, ra = EARTH_RADIUS_KM + o["perigee_km"], EARTH_RADIUS_KM + o["apogee_km"]
        df = pd.DataFrame(
            {
                "norad_id": o["norad_id"].to_numpy(),
                "a_km": ((rp + ra) / 2).to_numpy(),
                "e": ((ra - rp) / (ra + rp)).clip(lower=0).to_numpy(),
                "inc_rad": np.radians(o["inclination_deg"].to_numpy()),
                "object_type": o["object_type"].to_numpy(),
                "rcs_size": o["rcs_size"].to_numpy(),
            }
        )
        return assign_sizes(df)
