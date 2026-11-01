"""Compute the shell risk index for a catalog and store it (table shell_risk)."""

from __future__ import annotations

import json
import logging

import pandas as pd

from src.config import Settings
from src.risk.shells import ShellConfig, prepare_objects, shell_index
from src.screening.run import latest_catalog, load_satcat
from src.storage import parquet_store as store
from src.storage.elsets import ElsetHistory

log = logging.getLogger("perigee.risk.run")


def catalog_for(settings: Settings, at: pd.Timestamp | None) -> tuple[pd.DataFrame, str]:
    """Latest CelesTrak snapshot, or the element-set history as known at `at`."""
    if at is None:
        catalog = latest_catalog(settings)
        return catalog, str(catalog["snapshot_id"].iloc[0])
    history = ElsetHistory(settings.processed_dir)
    try:
        catalog = history.catalog_at(at)
    finally:
        history.con.close()
    if catalog.empty:
        raise SystemExit(f"No element-set history around {at:%Y-%m-%d}.")
    return catalog, f"history_{at:%Y%m%dT%H%M%SZ}"


def run_shells(settings: Settings, config: ShellConfig = ShellConfig(), at: pd.Timestamp | None = None) -> pd.DataFrame:
    catalog, source = catalog_for(settings, at)
    objects = prepare_objects(catalog, load_satcat(settings), config.size_mapping)
    result = shell_index(objects, config)
    run_id = f"{source}_{config.band_km:g}km"
    result.insert(0, "run_id", run_id)
    result.insert(1, "source", source)
    path = store.shell_risk_path(settings.processed_dir, run_id)
    store.write_parquet(result, path)
    top = result.nlargest(5, "risk_index")
    summary = {
        "run_id": run_id,
        "objects": len(objects),
        "objects_outside_range": result.attrs["objects_outside_range"],
        "config": {k: getattr(config, k) for k in config.__dataclass_fields__},
        "top_bands": top[["alt_lo_km", "alt_hi_km", "risk_index", "risk_index_weighted"]].to_dict("records"),
    }
    path.with_suffix(".json").write_text(json.dumps(summary, indent=2, default=str))
    log.info("shell index stored", extra={"run_id": run_id, "objects": len(objects)})
    return result
