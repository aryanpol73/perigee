"""The data job: fetch -> validate -> archive raw, then rebuild processed tables from raw.

Fetching and building are separate on purpose. A fetch only ever adds a raw
snapshot (or quarantines a bad one); the build step derives every processed
table from the latest accepted raw snapshot, so tables can always be rebuilt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd

from src.config import Settings
from src.ingest import archive
from src.ingest.download import (
    FetchLog,
    HttpStatusError,
    NetworkError,
    PoliteClient,
    StoppedError,
)
from src.ingest.parse import SchemaError, parse_gp, parse_satcat, parse_space_weather
from src.ingest.sources import GP, SATCAT, SPACE_WEATHER, Source, all_sources
from src.ingest.validate import (
    ValidationReport,
    validate_gp,
    validate_satcat,
    validate_space_weather,
)
from src.orbits.elements import (
    apogee_altitude_km,
    orbit_regime,
    perigee_altitude_km,
    semi_major_axis_km,
)
from src.orbits.propagate import propagate_catalog
from src.storage import parquet_store as store

log = logging.getLogger("perigee.pipeline")

FAILED_STATUSES = {"rejected", "refused", "stopped", "network_error"}


@dataclass
class SourceOutcome:
    source: str
    status: str  # fetched | cached | skipped | rejected | refused | stopped | network_error
    snapshot_id: str | None = None
    detail: str = ""


def _previous_count(settings: Settings, source: str) -> int | None:
    if source == GP:
        return store.latest_catalog_count(settings.processed_dir)
    if source == SATCAT:
        return store.table_count(settings.processed_dir, store.SATCAT)
    return None


def parse_and_validate(
    source: str,
    files: dict[str, bytes],
    settings: Settings,
    now: datetime,
    previous_count: int | None,
) -> tuple[pd.DataFrame, ValidationReport]:
    try:
        if source == GP:
            return validate_gp(
                parse_gp(files),
                expected_groups=list(files),
                now=now,
                min_objects=settings.gp_min_objects,
                previous_count=previous_count,
                max_drop=settings.max_count_drop,
                max_bad_fraction=settings.max_bad_row_fraction,
            )
        if source == SATCAT:
            return validate_satcat(
                parse_satcat(files["satcat"]),
                min_objects=settings.satcat_min_objects,
                previous_count=previous_count,
                max_drop=settings.max_count_drop,
                max_bad_fraction=settings.max_bad_row_fraction,
            )
        if source == SPACE_WEATHER:
            return validate_space_weather(
                parse_space_weather(files["space_weather"]),
                min_days=settings.space_weather_min_days,
                max_bad_fraction=settings.max_bad_row_fraction,
            )
    except SchemaError as exc:
        return pd.DataFrame(), ValidationReport(source, rows_in=0, errors=[str(exc)])
    raise ValueError(f"unknown source {source}")


def fetch_source(
    source: Source,
    settings: Settings,
    client: PoliteClient,
    fetch_log: FetchLog,
    now: datetime,
) -> SourceOutcome:
    if not fetch_log.is_due(source.name, source.min_interval_h, now):
        latest = archive.latest_snapshot(settings.raw_dir, source.name)
        log.info("not due, using cached raw data", extra={"source": source.name, "snapshot": latest})
        return SourceOutcome(source.name, "cached", latest)

    # Record the attempt first: a failed or rejected download still counts
    # against the polling interval.
    fetch_log.record(source.name, now)
    snapshot_id = archive.snapshot_id_for(now)
    files = {stem: client.get(url) for stem, url in source.files.items()}

    _, report = parse_and_validate(
        source.name, files, settings, now, _previous_count(settings, source.name)
    )
    if not report.ok:
        path = archive.write_snapshot(settings.quarantine_dir, source.name, snapshot_id, files)
        log.error("download rejected", extra={**report.as_log(), "quarantined": str(path)})
        return SourceOutcome(source.name, "rejected", snapshot_id, "; ".join(report.errors))

    path = archive.write_snapshot(settings.raw_dir, source.name, snapshot_id, files)
    log.info("download accepted", extra={**report.as_log(), "archived": str(path)})
    return SourceOutcome(source.name, "fetched", snapshot_id)


def add_orbit_geometry(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["semi_major_axis_km"] = semi_major_axis_km(out["mean_motion"])
    out["perigee_km"] = perigee_altitude_km(out["mean_motion"], out["eccentricity"])
    out["apogee_km"] = apogee_altitude_km(out["mean_motion"], out["eccentricity"])
    out["regime"] = orbit_regime(out["perigee_km"], out["apogee_km"], out["eccentricity"])
    return out


def add_sgp4_status(df: pd.DataFrame, at: datetime) -> pd.DataFrame:
    """Propagate every object to the snapshot time and record its SGP4 error code.

    Objects that fail stay in the table, flagged, so they are counted rather
    than silently dropped; downstream steps filter on sgp4_error = 0.
    """
    out = df.copy()
    result = propagate_catalog(out, [at])
    out["sgp4_error"] = result.error_code[:, 0].astype("int16")
    return out


def build_snapshot(source: str, snapshot_id: str, settings: Settings) -> bool:
    """Rebuild the processed table for one archived raw snapshot. Idempotent."""
    files = archive.read_snapshot(settings.raw_dir, source, snapshot_id)
    taken_at = archive.snapshot_time(snapshot_id)
    df, report = parse_and_validate(source, files, settings, taken_at, previous_count=None)
    if not report.ok:
        log.error("archived snapshot no longer validates", extra=report.as_log())
        return False

    df.insert(0, "snapshot_id", snapshot_id)
    df.insert(1, "snapshot_time", pd.Timestamp(taken_at))
    if source == GP:
        catalog = add_sgp4_status(add_orbit_geometry(df), taken_at)
        store.write_parquet(catalog, store.catalog_snapshot_path(settings.processed_dir, snapshot_id))
    else:
        name = store.SATCAT if source == SATCAT else store.SPACE_WEATHER
        store.write_parquet(df, store.table_path(settings.processed_dir, name))
    log.info("built table", extra={"source": source, "snapshot": snapshot_id, "rows": len(df)})
    return True


def build_latest(settings: Settings) -> None:
    for source in (SATCAT, SPACE_WEATHER, GP):
        latest = archive.latest_snapshot(settings.raw_dir, source)
        if latest is None:
            log.warning("no accepted raw data yet", extra={"source": source})
            continue
        build_snapshot(source, latest, settings)


def rebuild_all(settings: Settings) -> int:
    """Rebuild every catalog snapshot (history) plus the latest SATCAT and space weather."""
    built = 0
    for snapshot_id in archive.list_snapshots(settings.raw_dir, GP):
        built += build_snapshot(GP, snapshot_id, settings)
    build_latest(settings)
    return built


def run_data(
    settings: Settings,
    client: PoliteClient | None = None,
    now: datetime | None = None,
    fetch: bool = True,
) -> list[SourceOutcome]:
    now = now or datetime.now(UTC)
    client = client or PoliteClient(settings)
    fetch_log = FetchLog(settings.state_dir)
    outcomes: list[SourceOutcome] = []
    halted = False

    for source in all_sources(settings):
        if not fetch or halted:
            latest = archive.latest_snapshot(settings.raw_dir, source.name)
            outcomes.append(SourceOutcome(source.name, "skipped", latest))
            continue
        try:
            outcomes.append(fetch_source(source, settings, client, fetch_log, now))
        except HttpStatusError as exc:
            # Policy: stop every CelesTrak request on any non-200 response.
            halted = True
            outcomes.append(SourceOutcome(source.name, "refused", detail=str(exc)))
        except StoppedError as exc:
            halted = True
            outcomes.append(SourceOutcome(source.name, "stopped", detail=str(exc)))
        except NetworkError as exc:
            outcomes.append(SourceOutcome(source.name, "network_error", detail=str(exc)))

    build_latest(settings)
    return outcomes
