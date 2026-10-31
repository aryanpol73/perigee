"""Parquet tables queried with DuckDB.

Layout under the processed dir:
  catalog_snapshots/snapshot_date=YYYY-MM-DD/<snapshot_id>.parquet  (one file per snapshot)
  satcat/satcat.parquet                                              (latest only)
  space_weather/space_weather.parquet                                (latest only)

Writes go to a hidden temp file and are renamed into place, so readers never see
a half-written table and the previous good file stays live until the new one is ready.
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pandas as pd

CATALOG = "catalog_snapshots"
SATCAT = "satcat"
SPACE_WEATHER = "space_weather"
CONJUNCTIONS = "conjunctions"


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    # Without this DuckDB hands timestamps back in the machine's local zone.
    con.execute("SET TimeZone = 'UTC'")
    return con


def sql_path(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def write_parquet(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    con = connect()
    try:
        con.register("frame", df)
        con.execute(f"COPY (SELECT * FROM frame) TO '{sql_path(tmp)}' (FORMAT parquet)")
    finally:
        con.close()
    os.replace(tmp, path)
    return path


def catalog_snapshot_path(processed_dir: Path, snapshot_id: str) -> Path:
    date = f"{snapshot_id[0:4]}-{snapshot_id[4:6]}-{snapshot_id[6:8]}"
    return processed_dir / CATALOG / f"snapshot_date={date}" / f"{snapshot_id}.parquet"


def conjunctions_path(processed_dir: Path, snapshot_id: str, scope: str) -> Path:
    return processed_dir / CONJUNCTIONS / f"{snapshot_id}_{scope}.parquet"


def table_path(processed_dir: Path, name: str) -> Path:
    return processed_dir / name / f"{name}.parquet"


def has_catalog(processed_dir: Path) -> bool:
    return any((processed_dir / CATALOG).glob("snapshot_date=*/*.parquet"))


def catalog_relation(processed_dir: Path) -> str:
    """SQL table expression over every stored catalog snapshot.

    union_by_name lets snapshots written before a column was added be read
    alongside newer ones (the missing column reads as NULL).
    """
    glob = sql_path(processed_dir / CATALOG) + "/snapshot_date=*/*.parquet"
    return f"read_parquet('{glob}', hive_partitioning = true, union_by_name = true)"


def table_relation(processed_dir: Path, name: str) -> str:
    return f"read_parquet('{sql_path(table_path(processed_dir, name))}')"


def latest_catalog_count(processed_dir: Path) -> int | None:
    if not has_catalog(processed_dir):
        return None
    con = connect()
    try:
        rows = con.execute(
            f"SELECT count(*) FROM {catalog_relation(processed_dir)} "
            f"WHERE snapshot_id = (SELECT max(snapshot_id) FROM {catalog_relation(processed_dir)})"
        ).fetchall()
    finally:
        con.close()
    return int(rows[0][0])


def table_count(processed_dir: Path, name: str) -> int | None:
    if not table_path(processed_dir, name).exists():
        return None
    con = connect()
    try:
        rows = con.execute(f"SELECT count(*) FROM {table_relation(processed_dir, name)}").fetchall()
    finally:
        con.close()
    return int(rows[0][0])
