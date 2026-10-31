"""Phase 3 validation: compare the latest screening run with CelesTrak SOCRATES.

Usage: python scripts/validate_screening.py [--socrates PATH]
Needs a stored run (python -m src.main screen) and a SOCRATES CSV in
data/reference/socrates/ (downloaded once from celestrak.org/SOCRATES/sort-minRange.csv).
Writes docs/screening-validation.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import Settings  # noqa: E402
from src.screening.run import load_satcat  # noqa: E402
from src.screening.socrates import compare, load_socrates, socrates_epochs  # noqa: E402
from src.storage import parquet_store as store  # noqa: E402

SOCRATES_DIR = ROOT / "data" / "reference" / "socrates"


def pct(x: float) -> str:
    return f"{x:.1%}" if x == x else "n/a"


def table(rows: list[tuple], header: tuple) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socrates", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "screening-validation.md")
    args = parser.parse_args()

    settings = Settings.from_env()
    runs = sorted((settings.processed_dir / store.CONJUNCTIONS).glob("*_active.parquet"))
    if not runs:
        raise SystemExit("No screening run stored. Run: python -m src.main screen")
    run_path = runs[-1]
    run = json.loads(run_path.with_suffix(".json").read_text())
    snapshot_id = run["snapshot_id"]
    soc_path = args.socrates or sorted(SOCRATES_DIR.glob("*.csv"))[-1]

    con = store.connect()
    ours = con.execute(f"SELECT * FROM read_parquet('{store.sql_path(run_path)}')").df()
    catalog = con.execute(
        f"SELECT * FROM {store.catalog_relation(settings.processed_dir)} WHERE snapshot_id = ?", [snapshot_id]
    ).df()
    con.close()
    satcat = load_satcat(settings)
    socrates = load_socrates(soc_path)
    cmp = compare(ours, socrates, catalog, satcat)
    s, o = cmp.socrates, cmp.ours
    lo, hi = cmp.window
    cutoff = socrates_epochs(socrates).max()

    comparable = s[s["both_in_catalog"]]
    same = s[s["same_inputs"]]
    close = s[s["TCA_RANGE"] < 1.0]

    # Agreement on events both found from identical inputs.
    agree = same[same["found"] & ~same["colocated"]].copy()
    agree["dmiss_m"] = (agree["TCA_RANGE"] - agree["our_miss_km"]).abs() * 1000
    dtca_ms = (agree["TCA"] - agree["our_tca"]).dt.total_seconds().abs() * 1000
    outliers = agree[agree["dmiss_m"] > 50]

    lines = [
        "# Screening validation against CelesTrak SOCRATES (Phase 3)",
        "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `scripts/validate_screening.py`.",
        "",
        "All results are estimated close approaches based on public orbit data, not collision predictions.",
        "",
        "## Runs compared",
        "",
        *table(
            [
                ("Catalog snapshot", f"`{snapshot_id}`", f"`{soc_path.name}`"),
                ("Objects", f"{run['objects']:,} (CelesTrak GP groups)", "full public catalog"),
                ("Scope", "active satellites vs all objects", "active satellites vs all objects"),
                ("Threshold / window", f"{run['config_threshold_km']:g} km / {run['config_days']:g} days", "5 km / 7 days"),
                ("Newest element set used", f"{pd.to_datetime(catalog['epoch'], utc=True).max():%Y-%m-%d %H:%M} UTC", f"{cutoff:%Y-%m-%d %H:%M} UTC"),
                ("Propagator", "Python `sgp4` 2.27", "SGP4 in STK 12.2"),
                ("Events (full window)", f"{len(ours):,}", f"{len(socrates):,}"),
            ],
            ("", "Perigee", "SOCRATES"),
        ),
        "",
        f"Comparison window (overlap of both runs, 5 min trimmed at each end): "
        f"{lo:%Y-%m-%d %H:%M} to {hi:%Y-%m-%d %H:%M} UTC. Events match when they involve the same pair and "
        "their times of closest approach (TCA) are within 60 s.",
        "",
        "## Headline: how much of SOCRATES does Perigee find?",
        "",
        *table(
            [
                ("All SOCRATES events", f"{len(s):,}", pct(s["found"].mean())),
                ("Both objects in Perigee's catalog", f"{len(comparable):,}", pct(comparable["found"].mean())),
                ("Identical inputs (both element sets the same)", f"{len(same):,}", f"**{pct(same['found'].mean())}**"),
                ("High-risk: SOCRATES min range < 1 km", f"{len(close):,}", pct(close["found"].mean())),
                ("High-risk, both objects in catalog", f"{close['both_in_catalog'].sum():,}", pct(close[close['both_in_catalog']]['found'].mean())),
                ("High-risk, identical inputs", f"{close['same_inputs'].sum():,}", pct(close[close['same_inputs']]['found'].mean())),
            ],
            ("SOCRATES events", "Count", "Found by Perigee"),
        ),
        "",
        "**Identical inputs** is the test of the screening algorithm: SOCRATES publishes each object's "
        "days-since-epoch, so its exact element set is known, and wherever both objects' sets equal ours "
        "the two programs had the same inputs. The other rows also measure catalog coverage and data age.",
        "",
        "## Why SOCRATES events were not found",
        "",
        *table(
            [(reason, f"{n:,}", pct(n / len(s))) for reason, n in s["reason"].value_counts().items()],
            ("Reason", "Events", "Share of SOCRATES events"),
        ),
        "",
        "- **Object not in our catalog.** CelesTrak's GP groups (active, analyst and three debris clouds) "
        "are not the full catalog; most missing objects are debris and rocket bodies. The full catalog "
        "needs Space-Track.",
        "- **Different element sets.** SOCRATES's newest data was "
        f"{cutoff:%Y-%m-%d %H:%M} UTC; ours is about "
        f"{(pd.to_datetime(catalog['epoch'], utc=True).max() - cutoff) / pd.Timedelta(hours=1):.0f} h newer. "
        "Fresher data moves predicted positions by kilometres days ahead, so many near-threshold events "
        "appear or vanish. This is the data-age effect Phase 4's ML stage is about.",
    ]
    missed_same = same[~same["found"]]
    if len(missed_same):
        lines += [
            "- **Identical inputs, not found.** Each one:",
            "",
            *table(
                [
                    (r.a, r.b, f"{r.TCA:%Y-%m-%d %H:%M:%S}", f"{r.TCA_RANGE:.3f}", f"{r.TCA_RELATIVE_SPEED:.3f}")
                    for r in missed_same.itertuples()
                ],
                ("Object A", "Object B", "SOCRATES TCA", "SOCRATES range (km)", "Rel. speed (km/s)"),
            ),
            "",
            "  A SOCRATES range at the 5 km line can fall just above it in Perigee's computation (the two "
            "SGP4 implementations and TCA searches differ by metres), so it drops out of the list.",
        ]
    lines += [
        "",
        "## Agreement where both found the event from identical inputs",
        "",
        *table(
            [
                ("TCA difference", f"{dtca_ms.median():.1f} ms", f"{dtca_ms.quantile(.99):.1f} ms", f"{dtca_ms.max():.0f} ms"),
                ("Miss distance difference", f"{agree['dmiss_m'].median():.1f} m", f"{agree['dmiss_m'].quantile(.99):.1f} m", f"{agree['dmiss_m'].max():.0f} m"),
            ],
            ("", "Median", "99th percentile", "Max"),
        ),
        "",
        f"{len(agree):,} events. Differences above 50 m: {len(outliers)}"
        + (
            ". These are unexplained so far: the objects' epochs match SOCRATES's to within its "
            "86-second DSE rounding, so they look like the same element sets. Possible causes are an "
            "element set re-issued with the same epoch, or an SGP4 difference between STK and Python "
            "`sgp4` for these objects. Open item."
            if len(outliers)
            else "."
        ),
    ]
    if len(outliers):
        lines += [
            "",
            *table(
                [(r.a, r.b, f"{r.TCA:%Y-%m-%d %H:%M}", f"{r.TCA_RANGE:.3f}", f"{r.our_miss_km:.3f}") for r in outliers.itertuples()],
                ("Object A", "Object B", "TCA", "SOCRATES (km)", "Perigee (km)"),
            ),
        ]

    # Reverse direction.
    lines += [
        "",
        "## Perigee events that SOCRATES does not list",
        "",
        *table(
            [(reason, f"{n:,}", pct(n / len(o))) for reason, n in o["reason"].value_counts().items()],
            ("Reason", "Events", "Share of Perigee events"),
        ),
        "",
    ]
    o_same = o[o["same_inputs"]]
    by_kind = o_same.groupby("kind")["in_socrates"].agg(["mean", "size"])
    o_same = o_same.assign(pair=np.where(o_same["same_family"], "same constellation or family", "different"))
    by_family = o_same.groupby("pair")["in_socrates"].agg(["mean", "size"])
    lines += [
        "With identical inputs, how many of Perigee's events SOCRATES also lists:",
        "",
        *table([(k, f"{int(r['size']):,}", pct(r["mean"])) for k, r in by_kind.iterrows()], ("Pair kind", "Perigee events", "Also in SOCRATES")),
        "",
        *table([(k, f"{int(r['size']):,}", pct(r["mean"])) for k, r in by_family.iterrows()], ("Satellites in the pair", "Perigee events", "Also in SOCRATES")),
        "",
        "**Pairs within one constellation.** For pairs from different operators the two lists agree almost "
        "completely. For pairs within one constellation (Starlink-Starlink above all, but also OneWeb-OneWeb "
        "and smaller families), SOCRATES lists only a minority of the approaches Perigee finds, even with "
        "identical inputs, and the ones it does list agree with Perigee to metres. So this is not a "
        "computation difference: SOCRATES publishes only some intra-constellation approaches. Its published "
        "methodology does not say why; a plausible reason is that operators screen their own constellations, "
        "but that is a guess. Ruled out by the data: different element sets (the kept events match to "
        "metres), a relative-speed or distance cut (the shortfall is the same in every speed and distance "
        "bin), and a per-object cap (no spike in events per object). (\"Family\" is the first word of both "
        "names, which also groups unrelated COSMOS satellites; that only affects this classification.)",
        "",
    ]
    rest = o[o["reason"] == "same inputs, not in SOCRATES"]
    if len(rest):
        names_all = satcat.set_index("norad_id")["object_name"] if satcat is not None else pd.Series(dtype=str)
        lines += [
            f"The {len(rest)} remaining identical-input events SOCRATES does not list (open items; most are "
            "near the 5 km threshold or are formation-flying pairs, which SOCRATES lists inconsistently):",
            "",
            *table(
                [
                    (
                        f"{names_all.get(r.a, r.a)}",
                        f"{names_all.get(r.b, r.b)}",
                        f"{r.tca:%Y-%m-%d %H:%M}",
                        f"{r.miss_km:.3f}",
                        f"{r.rel_speed_km_s:.3f}",
                    )
                    for r in rest.sort_values("miss_km").itertuples()
                ],
                ("Object A", "Object B", "TCA", "Perigee miss (km)", "Rel. speed (km/s)"),
            ),
            "",
        ]
    lines += [
        "Starlink-Starlink approaches also grow through the week in both lists, while every other kind is flat:",
        "",
    ]
    day = lambda t, t0: ((t - t0) / pd.Timedelta(days=1)).astype(int)
    o_full = ours.rename(columns={"norad_a": "a", "norad_b": "b"})
    names = catalog.set_index("norad_id")["object_name"].fillna("")
    sl = lambda df: df["a"].map(names).fillna("").str.startswith("STARLINK") & df["b"].map(names).fillna("").str.startswith("STARLINK")
    ours_daily = o_full.assign(d=day(o_full["tca"], o_full["tca"].min()), sl=sl(o_full)).groupby(["d", "sl"]).size().unstack(fill_value=0)
    soc = socrates.assign(d=day(socrates["TCA"], socrates["TCA"].min()))
    soc_sl = soc["OBJECT_NAME_1"].str.startswith("STARLINK") & soc["OBJECT_NAME_2"].str.startswith("STARLINK")
    soc_daily = soc.assign(sl=soc_sl).groupby(["d", "sl"]).size().unstack(fill_value=0)
    lines += table(
        [
            (f"Day {d + 1}", f"{ours_daily.loc[d, True]:,}", f"{ours_daily.loc[d, False]:,}", f"{soc_daily.loc[d, True]:,}", f"{soc_daily.loc[d, False]:,}")
            for d in range(min(len(ours_daily), len(soc_daily)))
        ],
        ("", "Perigee Starlink-Starlink", "Perigee other", "SOCRATES Starlink-Starlink", "SOCRATES other"),
    )
    lines += [
        "",
        "Starlink satellites manoeuvre constantly to hold their slots; public GP data knows nothing of future "
        "manoeuvres, so propagated days ahead, neighbours in a shell drift into each other. Days 4-7 of any "
        "Starlink-Starlink list built from public data are mostly artefacts. A good input for the ML stage: "
        "time to TCA and object type should matter a lot.",
        "",
        "## Performance",
        "",
        *table(
            [
                ("Objects screened", f"{run['objects']:,}"),
                ("Window", f"{run['config_days']:g} days at {run['config_step_s']:g} s coarse steps"),
                ("Wall time", f"**{run['seconds']:.0f} s** on {run['config_workers']} worker processes"),
                ("Candidates refined", f"{run['candidates_refined']:,}"),
                ("Budget (build guide)", "under 30 minutes on a laptop"),
            ],
            ("", ""),
        ),
        "",
        "## Limitations",
        "",
        "- Perigee's catalog lacks most debris and rocket bodies (CelesTrak groups only); SOCRATES uses the full catalog.",
        "- Both use public GP data with roughly kilometre-level error and no covariance, so a list of approaches "
        "under 5 km is a list of *possible* close approaches. SOCRATES's maximum-probability figure is not reproduced.",
        "- Co-located objects (docked or formation flying, relative speed under 10 m/s) are flagged and kept "
        "once per pair, at their closest approach; SOCRATES lists them once per orbit. They are compared "
        "by pair over both full runs.",
        "",
    ]
    text = "\n".join(lines)
    args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
