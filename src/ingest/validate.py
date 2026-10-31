"""Data quality checks. A failed check rejects the download; the last good data stays live.

Two kinds of problem:
* fatal (wrong row count, duplicated ids, an empty group): the whole file is rejected;
* row-level (impossible orbit values): bad rows are excluded and counted, unless
  they exceed max_bad_row_fraction, which suggests a broken file rather than a few
  bad records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

EPOCH_CLOCK_SKEW = timedelta(hours=1)


@dataclass
class ValidationReport:
    source: str
    rows_in: int
    rows_out: int = 0
    rejected_rows: dict[str, int] = field(default_factory=dict)
    deduplicated: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_log(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "rows_rejected": sum(self.rejected_rows.values()),
            "rejected_by_reason": self.rejected_rows,
            "deduplicated": self.deduplicated,
            "errors": self.errors,
        }


class ValidationError(RuntimeError):
    def __init__(self, report: ValidationReport) -> None:
        super().__init__(f"{report.source} rejected: {'; '.join(report.errors)}")
        self.report = report


def _drop_bad_rows(
    df: pd.DataFrame,
    masks: dict[str, pd.Series],
    report: ValidationReport,
    max_bad_fraction: float,
) -> pd.DataFrame:
    bad = pd.Series(False, index=df.index)
    for reason, mask in masks.items():
        mask = mask.astype("boolean").fillna(True).astype(bool)
        if count := int(mask.sum()):
            report.rejected_rows[reason] = count
        bad |= mask
    if len(df) and bad.sum() / len(df) > max_bad_fraction:
        report.errors.append(
            f"{int(bad.sum())} of {len(df)} rows failed checks "
            f"(limit {max_bad_fraction:.1%}): {report.rejected_rows}"
        )
    return df[~bad]


def _check_count(
    report: ValidationReport,
    count: int,
    min_count: int,
    previous_count: int | None,
    max_drop: float,
) -> None:
    if count < min_count:
        report.errors.append(f"only {count} rows, expected at least {min_count}")
    if previous_count and count < previous_count * (1.0 - max_drop):
        report.errors.append(
            f"row count fell from {previous_count} to {count} "
            f"(more than {max_drop:.0%}); likely a partial download"
        )


def validate_gp(
    df: pd.DataFrame,
    *,
    expected_groups: tuple[str, ...] | list[str],
    now: datetime,
    min_objects: int,
    previous_count: int | None,
    max_drop: float,
    max_bad_fraction: float,
) -> tuple[pd.DataFrame, ValidationReport]:
    report = ValidationReport("gp", rows_in=len(df))

    group_counts = df["source_group"].value_counts()
    for group in expected_groups:
        if group_counts.get(group, 0) == 0:
            report.errors.append(f"group {group!r} returned no rows")

    duplicated = df.duplicated(["source_group", "norad_id"]) & df["norad_id"].notna()
    if duplicated.any():
        report.errors.append(f"{int(duplicated.sum())} duplicated catalog numbers within a group")

    clean = _drop_bad_rows(
        df,
        {
            "missing_norad_id": df["norad_id"].isna(),
            "missing_epoch": df["epoch"].isna(),
            "future_epoch": df["epoch"] > pd.Timestamp(now + EPOCH_CLOCK_SKEW),
            "eccentricity_out_of_range": ~df["eccentricity"].between(0.0, 1.0, inclusive="left"),
            "mean_motion_not_positive": ~(df["mean_motion"] > 0.0),
            "inclination_out_of_range": ~df["inclination"].between(0.0, 180.0),
        },
        report,
        max_bad_fraction,
    )

    # Objects can appear in more than one group; keep the freshest record.
    before = len(clean)
    clean = clean.sort_values("epoch", ascending=False).drop_duplicates("norad_id")
    report.deduplicated = before - len(clean)

    report.rows_out = len(clean)
    _check_count(report, report.rows_out, min_objects, previous_count, max_drop)
    return clean.sort_values("norad_id").reset_index(drop=True), report


def validate_satcat(
    df: pd.DataFrame,
    *,
    min_objects: int,
    previous_count: int | None,
    max_drop: float,
    max_bad_fraction: float,
) -> tuple[pd.DataFrame, ValidationReport]:
    report = ValidationReport("satcat", rows_in=len(df))

    duplicated = df["norad_id"].duplicated() & df["norad_id"].notna()
    if duplicated.any():
        report.errors.append(f"{int(duplicated.sum())} duplicated catalog numbers")

    clean = _drop_bad_rows(
        df,
        {
            "missing_norad_id": df["norad_id"].isna(),
            "missing_object_type": df["object_type"].isna(),
        },
        report,
        max_bad_fraction,
    )
    report.rows_out = len(clean)
    _check_count(report, report.rows_out, min_objects, previous_count, max_drop)
    return clean.sort_values("norad_id").reset_index(drop=True), report


def validate_space_weather(
    df: pd.DataFrame,
    *,
    min_days: int,
    max_bad_fraction: float,
) -> tuple[pd.DataFrame, ValidationReport]:
    report = ValidationReport("space_weather", rows_in=len(df))

    duplicated = df["date"].duplicated() & df["date"].notna()
    if duplicated.any():
        report.errors.append(f"{int(duplicated.sum())} duplicated dates")

    observed = df["f107_data_type"] == "OBS"
    clean = _drop_bad_rows(
        df,
        {
            "missing_date": df["date"].isna(),
            "observed_f107_not_positive": observed & ~(df["f107_obs"] > 0.0),
        },
        report,
        max_bad_fraction,
    )
    report.rows_out = len(clean)
    _check_count(report, report.rows_out, min_days, None, 0.0)
    return clean.sort_values("date").reset_index(drop=True), report
