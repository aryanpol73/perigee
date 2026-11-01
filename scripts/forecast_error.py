"""How far off is a forecast from public orbit data, by how far ahead it looks?

The globe forecasts up to 7 days from one snapshot. This measures the position
error of doing that, on the 2025 Space-Track history: at a start time T, take each
object's newest element set published by T (what a snapshot holds) and propagate
it to T + d. Compare with the first element set whose epoch is at or after T + d
(fitted to tracking data from then), propagated to T + d. That reference has
kilometre-level error of its own, so short leads read a little high.

Writes docs/forecast-uncertainty.md; the web app's badge uses the LEO medians
(web/src/uncertainty.ts). Needs the 2025 Space-Track history (import-spacetrack).

Usage: python scripts/forecast_error.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import Settings
from src.orbits.propagate import propagate_catalog
from src.storage.elsets import ElsetHistory
from src.storage.parquet_store import connect

OUT = ROOT / "docs" / "forecast-uncertainty.md"
LEADS_DAYS = [0.25, 0.5, 1, 2, 3, 4, 5, 6, 7]
STARTS = pd.date_range("2025-01-15", "2025-12-15", freq="MS", tz="UTC") + pd.Timedelta(
    days=14
)
PER_START = 4000  # objects sampled per start time
MAX_REF_GAP_H = 24  # the reference must be fitted within a day of T + d
ELEMENTS = [
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
]


def perigee_km(cat: pd.DataFrame) -> np.ndarray:
    n = cat["mean_motion"].to_numpy() * 2 * np.pi / 86400
    a = (398600.4418 / n**2) ** (1 / 3)
    return a * (1 - cat["eccentricity"].to_numpy()) - 6378.137


def starlink_ids(processed_dir: Path) -> set[int]:
    """Starlink NORAD ids from SATCAT (the Space-Track history has no names)."""
    glob = (processed_dir / "satcat").as_posix() + "/*.parquet"
    df = (
        connect()
        .execute(
            f"SELECT DISTINCT norad_id FROM read_parquet('{glob}') WHERE object_name LIKE 'STARLINK%'"
        )
        .df()
    )
    return set(df["norad_id"].astype(int))


STARLINK: set[int] = set()


def measure(
    history: ElsetHistory, start: pd.Timestamp, rng: np.random.Generator
) -> pd.DataFrame:
    cat = history.catalog_at(start, max_age_days=3)
    if len(cat) > PER_START:
        cat = cat.iloc[
            np.sort(rng.choice(len(cat), PER_START, replace=False))
        ].reset_index(drop=True)
    times = [start + pd.Timedelta(days=d) for d in LEADS_DAYS]
    forecast = propagate_catalog(cat, times)
    fpos = forecast.ecef_km()
    # One query for every (object, lead): the first set at or after each lead time.
    n = len(cat)
    queries = pd.DataFrame(
        {
            "qid": np.arange(n * len(times)),
            "norad_id": np.tile(cat["norad_id"].to_numpy(), len(times)),
            "after": np.repeat(pd.DatetimeIndex(times), n),
        }
    )
    refs = history.first_after(
        queries, times[-1] + pd.Timedelta(hours=MAX_REF_GAP_H + 6)
    )
    refs = refs.rename(columns={f"r_{c}": c for c in ELEMENTS})
    rows = []
    for j, (d, t) in enumerate(zip(LEADS_DAYS, times, strict=True)):
        ref = refs.iloc[j * n : (j + 1) * n].reset_index(drop=True)
        gap_h = (ref["epoch"] - t) / pd.Timedelta(hours=1)
        refprop = propagate_catalog(ref, [t])
        rpos = refprop.ecef_km()[:, 0, :]
        ok = (
            forecast.ok[:, j]
            & refprop.ok[:, 0]
            & (gap_h >= 0).to_numpy()
            & (gap_h <= MAX_REF_GAP_H).to_numpy()
        )
        err = np.linalg.norm(fpos[:, j, :] - rpos, axis=1)
        age_h = (start - cat["epoch"]) / pd.Timedelta(hours=1)
        rows.append(
            pd.DataFrame(
                {
                    "start": start,
                    "lead_days": d,
                    "norad_id": cat["norad_id"],
                    "perigee_km": perigee_km(cat),
                    "starlink": cat["norad_id"].isin(STARLINK),
                    "data_age_h": age_h,
                    "error_km": err,
                }
            )[ok]
        )
    return pd.concat(rows, ignore_index=True)


def table(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("lead_days")["error_km"]
    return pd.DataFrame(
        {"objects": g.size(), "median_km": g.median(), "p90_km": g.quantile(0.9)}
    )


def main() -> None:
    settings = Settings()
    history = ElsetHistory(settings.processed_dir)
    STARLINK.update(starlink_ids(settings.processed_dir))
    rng = np.random.default_rng(7)
    parts = []
    for start in STARTS:
        print(f"{start:%Y-%m-%d} ...", flush=True)
        parts.append(measure(history, start, rng))
    df = pd.concat(parts, ignore_index=True)
    leo = df[df["perigee_km"] < 2000]
    groups = {
        "LEO (perigee < 2,000 km)": leo,
        "LEO without Starlink": leo[~leo["starlink"]],
        "Starlink": leo[leo["starlink"]],
        "Above LEO": df[df["perigee_km"] >= 2000],
    }
    lines = [
        "# Forecast uncertainty",
        "",
        "How far a position forecast from one snapshot of public orbit data drifts from where",
        "the object later turned out to be, by how far ahead the forecast looks.",
        "Generated by `scripts/forecast_error.py` from the 2025 Space-Track history:",
        f"{len(STARTS)} start times (one a month), up to {PER_START:,} objects each.",
        "",
        "Method: at start time T, each object's newest element set published by T is",
        "propagated to T + lead and compared with the first set whose epoch is at or after",
        f"T + lead (within {MAX_REF_GAP_H} h), propagated to the same moment. The reference has",
        "about a kilometre of error itself, so the shortest leads read slightly high.",
        f"Median age of the snapshot's data at T: {df.drop_duplicates(['start', 'norad_id'])['data_age_h'].median():.0f} h.",
        "",
    ]
    for name, part in groups.items():
        t = table(part)
        lines += [
            f"## {name}",
            "",
            "| Lead (days) | Objects | Median error (km) | 90th percentile (km) |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {d:g} | {int(r.objects):,} | {r.median_km:.1f} | {r.p90_km:.1f} |"
            for d, r in t.iterrows()
        ]
        lines.append("")
    lines += [
        "## Reading it",
        "",
        "- The error is mostly along the direction of travel (timing), not altitude.",
        "- Manoeuvring satellites (Starlink adjusts its orbits often) and low, high-drag objects",
        "  drift fastest; a manoeuvre after the snapshot is invisible to any forecast from it.",
        "- The web app shows the LEO median for the shown lead as a rough ± next to the",
        "  FORECAST badge (web/src/uncertainty.ts).",
        "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    t = table(leo)
    print("LEO medians for web/src/uncertainty.ts:")
    print(
        "["
        + ", ".join(
            f"[{d:g}, {r.median_km:.1f}, {r.p90_km:.0f}]" for d, r in t.iterrows()
        )
        + "]"
    )
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
