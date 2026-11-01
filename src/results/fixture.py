"""The small results bundle committed to the repo, so a clean checkout runs the app.

Built from public CelesTrak data with `python -m src.main publish --fixture`:
the objects in the highest-scored close approaches, the ISS, and a random
sample of the rest. No Space-Track records are included (only derived results).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.config import Settings
from src.screening.run import latest_catalog
from src.storage import parquet_store as store

ISS = 25544


def fixture_selection(settings: Settings, top_pairs: int = 150, sample: int = 1_500, seed: int = 7) -> pd.Series:
    """Mask over the latest snapshot's objects to keep in the fixture."""
    catalog = latest_catalog(settings)
    snapshot_id = str(catalog["snapshot_id"].iloc[0])
    con = store.connect()
    try:
        events = con.execute(
            f"SELECT norad_a, norad_b, miss_km FROM read_parquet('{store.sql_path(store.conjunctions_path(settings.processed_dir, snapshot_id, 'active'))}') "
            "WHERE NOT colocated ORDER BY miss_km LIMIT 20000"
        ).df()
    finally:
        con.close()
    names = catalog.set_index("norad_id")["object_name"].fillna("")
    # Prefer pairs from different constellations (the ones the app shows).
    first = events["norad_a"].map(names).str.split().str[0]
    second = events["norad_b"].map(names).str.split().str[0]
    cross = events[first != second].head(top_pairs)
    keep = set(cross["norad_a"]) | set(cross["norad_b"]) | {ISS}
    rng = np.random.default_rng(seed)
    others = catalog.loc[~catalog["norad_id"].isin(keep), "norad_id"].to_numpy()
    keep |= set(rng.choice(others, min(sample, len(others)), replace=False).tolist())
    return catalog["norad_id"].isin(keep)
