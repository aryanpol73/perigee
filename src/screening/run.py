"""Screen the latest catalog snapshot and store the conjunction list."""

from __future__ import annotations

import json
import logging
from dataclasses import replace

import pandas as pd

from src.config import Settings
from src.screening.screen import ScreenConfig, ScreenResult, active_mask, screen
from src.storage import parquet_store as store

log = logging.getLogger("perigee.screening.run")


def latest_catalog(settings: Settings) -> pd.DataFrame:
    """Latest snapshot, objects that propagate at snapshot time only."""
    if not store.has_catalog(settings.processed_dir):
        raise SystemExit("No catalog snapshot stored yet. Run: python -m src.main data")
    con = store.connect()
    try:
        catalog = store.catalog_relation(settings.processed_dir)
        return con.execute(
            f"SELECT * FROM {catalog} WHERE snapshot_id = (SELECT max(snapshot_id) FROM {catalog}) "
            "AND coalesce(sgp4_error, 0) = 0 ORDER BY norad_id"
        ).df()
    finally:
        con.close()


def load_satcat(settings: Settings) -> pd.DataFrame | None:
    if not store.table_path(settings.processed_dir, store.SATCAT).exists():
        return None
    con = store.connect()
    try:
        return con.execute(
            f"SELECT norad_id, object_name, object_type, ops_status_code, rcs_size "
            f"FROM {store.table_relation(settings.processed_dir, store.SATCAT)}"
        ).df()
    finally:
        con.close()


def run_screen(settings: Settings, config: ScreenConfig = ScreenConfig()) -> ScreenResult:
    catalog = latest_catalog(settings)
    satcat = load_satcat(settings)
    snapshot_id = str(catalog["snapshot_id"].iloc[0])
    start = pd.Timestamp(catalog["snapshot_time"].iloc[0])
    active = active_mask(catalog, satcat)
    if config.scope == "active" and not active.any():
        log.warning("no SATCAT status available; screening all pairs instead")
        config = replace(config, scope="all")

    result = screen(catalog, start, config, active)
    events = result.events.copy()
    events.insert(0, "run_id", f"{snapshot_id}_{config.scope}")
    events.insert(1, "snapshot_id", snapshot_id)
    path = store.conjunctions_path(settings.processed_dir, snapshot_id, config.scope)
    store.write_parquet(events, path)
    summary = {"snapshot_id": snapshot_id, "active_objects": int(active.sum()), **result.summary()}
    path.with_suffix(".json").write_text(json.dumps(summary, indent=2, default=str))
    return result
