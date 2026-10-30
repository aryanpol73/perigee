"""Catalog summary queries over the processed Parquet tables."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.storage import parquet_store as store

LEO_BAND_KM = 50


@dataclass
class CatalogSummary:
    snapshot_id: str | None = None
    total_objects: int = 0
    snapshots_stored: int = 0
    by_regime: dict[str, int] = field(default_factory=dict)
    by_group: dict[str, int] = field(default_factory=dict)
    by_object_type: dict[str, int] = field(default_factory=dict)
    densest_leo_bands: list[tuple[int, int]] = field(default_factory=list)
    epoch_age_median_h: float | None = None
    epoch_age_p95_h: float | None = None
    satcat_rows: int | None = None
    satcat_on_orbit: int | None = None
    space_weather_last_observed: str | None = None
    space_weather_f107_obs: float | None = None


def catalog_summary(processed_dir: Path) -> CatalogSummary:
    summary = CatalogSummary()
    con = store.connect()
    try:
        has_satcat = store.table_path(processed_dir, store.SATCAT).exists()
        if has_satcat:
            satcat = store.table_relation(processed_dir, store.SATCAT)
            total, on_orbit = con.execute(
                f"SELECT count(*), count(*) FILTER (WHERE decay_date IS NULL) FROM {satcat}"
            ).fetchall()[0]
            summary.satcat_rows, summary.satcat_on_orbit = int(total), int(on_orbit)

        if store.table_path(processed_dir, store.SPACE_WEATHER).exists():
            sw = store.table_relation(processed_dir, store.SPACE_WEATHER)
            rows = con.execute(
                f"SELECT strftime(date, '%Y-%m-%d'), f107_obs FROM {sw} "
                "WHERE f107_data_type = 'OBS' ORDER BY date DESC LIMIT 1"
            ).fetchall()
            if rows:
                summary.space_weather_last_observed, summary.space_weather_f107_obs = rows[0]

        if not store.has_catalog(processed_dir):
            return summary

        catalog = store.catalog_relation(processed_dir)
        con.execute(
            f"CREATE TEMP VIEW latest AS SELECT * FROM {catalog} "
            f"WHERE snapshot_id = (SELECT max(snapshot_id) FROM {catalog})"
        )
        summary.snapshot_id, summary.total_objects = con.execute(
            "SELECT any_value(snapshot_id), count(*) FROM latest"
        ).fetchall()[0]
        summary.snapshots_stored = int(
            con.execute(f"SELECT count(DISTINCT snapshot_id) FROM {catalog}").fetchall()[0][0]
        )
        summary.by_regime = dict(
            con.execute(
                "SELECT regime, count(*) FROM latest GROUP BY regime ORDER BY 2 DESC"
            ).fetchall()
        )
        summary.by_group = dict(
            con.execute(
                "SELECT source_group, count(*) FROM latest GROUP BY 1 ORDER BY 2 DESC"
            ).fetchall()
        )
        if has_satcat:
            summary.by_object_type = dict(
                con.execute(
                    f"SELECT coalesce(s.object_type, 'NOT IN SATCAT'), count(*) "
                    f"FROM latest l LEFT JOIN {satcat} s USING (norad_id) "
                    "GROUP BY 1 ORDER BY 2 DESC"
                ).fetchall()
            )
        summary.densest_leo_bands = [
            (int(band), int(count))
            for band, count in con.execute(
                f"SELECT floor((perigee_km + apogee_km) / 2 / {LEO_BAND_KM}) * {LEO_BAND_KM} AS band,"
                " count(*) FROM latest WHERE regime = 'LEO' GROUP BY band ORDER BY 2 DESC LIMIT 5"
            ).fetchall()
        ]
        median, p95 = con.execute(
            "SELECT quantile_cont(age, 0.5), quantile_cont(age, 0.95) FROM "
            "(SELECT date_diff('second', epoch, snapshot_time) / 3600.0 AS age FROM latest)"
        ).fetchall()[0]
        summary.epoch_age_median_h, summary.epoch_age_p95_h = median, p95
    finally:
        con.close()
    return summary


def format_summary(summary: CatalogSummary) -> str:
    if summary.snapshot_id is None:
        return "No catalog snapshot stored yet. Run: python -m src.main data"

    total = summary.total_objects or 1
    lines = [
        f"Catalog snapshot {summary.snapshot_id}  ({summary.snapshots_stored} stored)",
        f"  Objects with orbit data: {summary.total_objects:,}",
        "  By orbit regime:",
        *(
            f"    {regime:<4} {count:>7,}  {count / total:6.1%}"
            for regime, count in summary.by_regime.items()
        ),
    ]
    if summary.by_object_type:
        lines.append("  By object type (SATCAT):")
        lines += [f"    {kind:<14} {count:>7,}" for kind, count in summary.by_object_type.items()]
    lines.append("  By CelesTrak group:")
    lines += [f"    {group:<20} {count:>7,}" for group, count in summary.by_group.items()]
    if summary.densest_leo_bands:
        lines.append(f"  Densest LEO bands ({LEO_BAND_KM} km, by mean altitude):")
        lines += [
            f"    {band:>5}-{band + LEO_BAND_KM:<5} km {count:>7,}"
            for band, count in summary.densest_leo_bands
        ]
    if summary.epoch_age_median_h is not None and summary.epoch_age_p95_h is not None:
        lines.append(
            f"  Epoch age: median {summary.epoch_age_median_h:.1f} h, "
            f"95th percentile {summary.epoch_age_p95_h:.1f} h"
        )
    if summary.satcat_rows is not None:
        lines.append(
            f"SATCAT: {summary.satcat_rows:,} objects, {summary.satcat_on_orbit:,} not decayed"
        )
    if summary.space_weather_last_observed:
        lines.append(
            f"Space weather: last observed {summary.space_weather_last_observed}, "
            f"F10.7 = {summary.space_weather_f107_obs}"
        )
    return "\n".join(lines)
