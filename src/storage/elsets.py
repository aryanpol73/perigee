"""Element-set history: every orbit record ever seen, with when it became available.

The ML stage must only use information available at prediction time T. For orbit
data that means "the newest element set published by T", which is not the same
as "the newest epoch before T": a record is fitted and published hours after its
epoch. Each record therefore carries `available_at`:

* from our own snapshots: the snapshot time (we saw it then, at the latest);
* from Space-Track history (later): its CREATION_DATE.

Queries run in DuckDB over Parquet, so months of history never sit in memory.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import duckdb
import pandas as pd

from src.storage import parquet_store as store

ELSET_HISTORY = "elset_history"  # processed/elset_history/*.parquet (Space-Track backfill)

ELEMENT_COLUMNS = (
    "norad_id",
    "object_name",
    "object_id",
    "epoch",
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

# Semi-major axis (km) from mean motion (rev/day), in SQL.
SMA_SQL = "pow(398600.4418 / pow(mean_motion * 2 * pi() / 86400, 2), 1.0 / 3)"


class ElsetHistory:
    def __init__(
        self,
        processed_dir: Path,
        con: duckdb.DuckDBPyConnection | None = None,
        memory_limit: str = "2GB",
        threads: int = 4,
    ) -> None:
        self.processed_dir = processed_dir
        self.con = con or store.connect()
        # DuckDB defaults to 80% of RAM; window queries over months of history
        # would starve the screening workers. Spill to the system temp dir (not
        # the project folder, which may be synced) instead.
        self.con.execute(f"SET memory_limit = '{memory_limit}'")
        self.con.execute(f"SET threads = {int(threads)}")
        self.con.execute(f"SET temp_directory = '{store.sql_path(Path(tempfile.gettempdir()) / 'perigee-duckdb')}'")
        sources = []
        cols = ", ".join(ELEMENT_COLUMNS)
        if store.has_catalog(processed_dir):
            sources.append(
                f"SELECT {cols}, snapshot_time AS available_at, 'snapshot' AS source "
                f"FROM {store.catalog_relation(processed_dir)}"
            )
        extra = processed_dir / ELSET_HISTORY
        if any(extra.glob("*.parquet")):
            glob = store.sql_path(extra) + "/*.parquet"
            sources.append(
                f"SELECT {cols}, available_at, 'spacetrack' AS source "
                f"FROM read_parquet('{glob}', union_by_name = true)"
            )
        if not sources:
            raise FileNotFoundError(f"no element sets under {processed_dir}")
        # One row per distinct element set (object, epoch), at the first time it was available.
        self.con.execute(
            f"""
            CREATE OR REPLACE TEMP VIEW elsets AS
            SELECT * EXCLUDE (rn) FROM (
              SELECT *, row_number() OVER (PARTITION BY norad_id, epoch ORDER BY available_at, source) AS rn
              FROM ({' UNION ALL '.join(sources)})
            ) WHERE rn = 1
            """
        )

    def span(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        # .df(), not fetchall(): DuckDB needs pytz to hand back tz-aware Python datetimes.
        row = self.con.execute("SELECT min(available_at) AS lo, max(available_at) AS hi FROM elsets").df().iloc[0]
        return pd.Timestamp(row["lo"]), pd.Timestamp(row["hi"])

    def catalog_at(self, t: pd.Timestamp, max_age_days: float = 30.0) -> pd.DataFrame:
        """The catalog as known at t: each object's newest set published by t.

        Objects whose newest set is older than max_age_days at t are left out
        (lost, decayed, or not tracked any more).
        """
        return self.con.execute(
            """
            SELECT * FROM elsets
            WHERE available_at <= ? AND epoch <= ?
            QUALIFY row_number() OVER (PARTITION BY norad_id ORDER BY epoch DESC) = 1
               AND epoch >= ?
            ORDER BY norad_id
            """,
            [t, t, t - pd.Timedelta(days=max_age_days)],
        ).df()

    def first_after(self, queries: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
        """For each (norad_id, after) row, the first set with epoch >= after published by cutoff.

        Falls back to the newest set published by cutoff when none is newer.
        Returns the query rows with the element columns appended (prefixed "r_").
        """
        self.con.register("_queries", queries[["qid", "norad_id", "after"]])
        try:
            df = self.con.execute(
                """
                WITH avail AS (SELECT * FROM elsets WHERE available_at <= ?),
                after AS (
                  SELECT q.qid, e.* EXCLUDE (norad_id)
                  FROM _queries q JOIN avail e ON e.norad_id = q.norad_id AND e.epoch >= q.after
                  QUALIFY row_number() OVER (PARTITION BY q.qid ORDER BY e.epoch) = 1
                ),
                latest AS (
                  SELECT q.qid, e.* EXCLUDE (norad_id)
                  FROM _queries q JOIN avail e ON e.norad_id = q.norad_id
                  QUALIFY row_number() OVER (PARTITION BY q.qid ORDER BY e.epoch DESC) = 1
                )
                SELECT q.qid, q.norad_id, coalesce(a.epoch, l.epoch) AS r_epoch,
                  coalesce(a.mean_motion, l.mean_motion) AS r_mean_motion,
                  coalesce(a.eccentricity, l.eccentricity) AS r_eccentricity,
                  coalesce(a.inclination, l.inclination) AS r_inclination,
                  coalesce(a.ra_of_asc_node, l.ra_of_asc_node) AS r_ra_of_asc_node,
                  coalesce(a.arg_of_pericenter, l.arg_of_pericenter) AS r_arg_of_pericenter,
                  coalesce(a.mean_anomaly, l.mean_anomaly) AS r_mean_anomaly,
                  coalesce(a.bstar, l.bstar) AS r_bstar,
                  coalesce(a.mean_motion_dot, l.mean_motion_dot) AS r_mean_motion_dot,
                  coalesce(a.mean_motion_ddot, l.mean_motion_ddot) AS r_mean_motion_ddot
                FROM _queries q LEFT JOIN after a USING (qid) LEFT JOIN latest l USING (qid)
                ORDER BY q.qid
                """,
                [cutoff],
            ).df()
        finally:
            self.con.unregister("_queries")
        return df

    def update_stats(self, t: pd.Timestamp, lookback_days: float = 30.0) -> pd.DataFrame:
        """Per object, from sets published by t in the lookback window: how often it was
        updated and how much its semi-major axis jumped between consecutive sets."""
        return self.con.execute(
            f"""
            WITH s AS (
              SELECT norad_id, epoch, {SMA_SQL} AS sma
              FROM elsets WHERE available_at <= ? AND epoch <= ? AND epoch >= ?
            ),
            d AS (
              SELECT norad_id, epoch,
                abs(sma - lag(sma) OVER (PARTITION BY norad_id ORDER BY epoch)) AS jump,
                (epoch_ms(epoch) - epoch_ms(lag(epoch) OVER (PARTITION BY norad_id ORDER BY epoch))) / 3.6e6 AS gap_h
              FROM s
            )
            SELECT norad_id,
              count(*) AS n_records,
              coalesce(max(jump), 0) AS sma_jump_max_km,
              coalesce(median(jump), 0) AS sma_jump_median_km,
              median(gap_h) AS update_gap_median_h
            FROM d GROUP BY norad_id
            """,
            [t, t, t - pd.Timedelta(days=lookback_days)],
        ).df()
