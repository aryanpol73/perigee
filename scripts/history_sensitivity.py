"""How much does thin update history change the model's inputs and scores?

The model's update-history features (records in 30 days, orbit jumps, update gap)
were built from Space-Track's full element-set history. Live runs see only the
snapshots our job took. This rescored the saved model on the 2025 test period with
those features rebuilt as a snapshot job would have seen them:

  full     every element set (as in training)
  2h       one snapshot every 2 hours (the most CelesTrak's policy allows)
  6h       one snapshot every 6 hours (about what GitHub's cron delivers now)
  none     a single snapshot (no history: today's local runs)

Writes docs/history-sensitivity.md. Needs the 2025 Space-Track history, the ML
dataset and a trained model under data/models/.

Usage: python scripts/history_sensitivity.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import Settings  # noqa: E402
from src.ml.features import FEATURES  # noqa: E402
from src.ml.score import load_model  # noqa: E402
from src.ml.train import TrainConfig, operating_threshold, point_metrics, time_split  # noqa: E402
from src.storage.elsets import SMA_SQL, ElsetHistory  # noqa: E402
from src.storage.parquet_store import connect  # noqa: E402

UPDATE = ["n_records_30d", "sma_jump_max_km", "sma_jump_median_km", "update_gap_median_h"]
OUT = ROOT / "docs" / "history-sensitivity.md"


def sampled_stats(history: ElsetHistory, t: pd.Timestamp, every_h: float | None, lookback_days: float = 30.0) -> pd.DataFrame:
    """update_stats as a job snapshotting every `every_h` hours would compute it (None = every set)."""
    keep = (
        ""
        if every_h is None
        else f"QUALIFY row_number() OVER (PARTITION BY norad_id, floor(epoch_ms(available_at) / {every_h * 3.6e6}) "
        "ORDER BY available_at DESC, epoch DESC) = 1"
    )
    return history.con.execute(
        f"""
        WITH s AS (
          SELECT norad_id, epoch, {SMA_SQL} AS sma FROM elsets
          WHERE available_at <= ? AND epoch <= ? AND epoch >= ? {keep}
        ),
        d AS (
          SELECT norad_id,
            abs(sma - lag(sma) OVER (PARTITION BY norad_id ORDER BY epoch)) AS jump,
            (epoch_ms(epoch) - epoch_ms(lag(epoch) OVER (PARTITION BY norad_id ORDER BY epoch))) / 3.6e6 AS gap_h
          FROM s
        )
        SELECT norad_id, count(*) AS n_records, coalesce(max(jump), 0) AS sma_jump_max_km,
          coalesce(median(jump), 0) AS sma_jump_median_km, median(gap_h) AS update_gap_median_h
        FROM d GROUP BY norad_id
        """,
        [t, t, t - pd.Timedelta(days=lookback_days)],
    ).df()


def with_stats(rows: pd.DataFrame, stats: pd.DataFrame | None) -> pd.DataFrame:
    """Replace the update-history features of both objects (None = no history at all)."""
    out = rows.copy()
    a_is_stale = np.isclose(rows["stale_dse_at_tca_d"], rows["dse_a"])
    ids = {"stale": np.where(a_is_stale, rows["norad_a"], rows["norad_b"]),
           "fresh": np.where(a_is_stale, rows["norad_b"], rows["norad_a"])}
    for role, norad in ids.items():
        if stats is None:
            out[f"{role}_n_records_30d"] = 1.0
            out[f"{role}_sma_jump_max_km"] = 0.0
            out[f"{role}_sma_jump_median_km"] = 0.0
            out[f"{role}_update_gap_median_h"] = np.nan
            continue
        s = stats.set_index("norad_id").reindex(norad)
        out[f"{role}_n_records_30d"] = s["n_records"].fillna(1).to_numpy(dtype=float)
        out[f"{role}_sma_jump_max_km"] = s["sma_jump_max_km"].fillna(0).to_numpy(dtype=float)
        out[f"{role}_sma_jump_median_km"] = s["sma_jump_median_km"].fillna(0).to_numpy(dtype=float)
        out[f"{role}_update_gap_median_h"] = s["update_gap_median_h"].to_numpy(dtype=float)
    return out


def main() -> int:
    started = time.monotonic()
    settings = Settings.from_env()
    model = load_model(settings.data_dir / "models")
    if model is None:
        raise SystemExit("No trained model under data/models. Run: python -m src.main train")
    con = connect()
    rows = con.execute(f"SELECT * FROM read_parquet('{(settings.processed_dir / 'ml' / 'dataset.parquet').as_posix()}')").df()
    split = time_split(rows, TrainConfig())
    test, val = split.test, split.val
    raw_val = model.booster.predict(val[FEATURES].astype(float))
    raw_threshold = operating_threshold(raw_val, val["label"].to_numpy(), TrainConfig().max_false_alarms_per_true)
    y = test["label"].to_numpy()

    history = ElsetHistory(settings.processed_dir)
    scenarios: dict[str, pd.DataFrame] = {"full (as trained)": test}
    try:
        for name, every, days in (
            ("snapshot every 2 h", 2.0, 30.0),
            ("snapshot every 6 h", 6.0, 30.0),
            ("every 6 h, only 15 days collected", 6.0, 15.0),
            ("every 6 h, only 7 days collected", 6.0, 7.0),
        ):
            parts = [with_stats(g, sampled_stats(history, pd.Timestamp(t), every, days)) for t, g in test.groupby("prediction_time")]
            scenarios[name] = pd.concat(parts).loc[test.index]
    finally:
        history.con.close()
    scenarios["no history (one snapshot)"] = with_stats(test, None)

    results, feature_rows = [], []
    for name, df in scenarios.items():
        raw = model.booster.predict(df[FEATURES].astype(float))
        p = model.probability(raw)
        op = point_metrics(y, raw >= raw_threshold)
        top = np.argsort(-raw)[:1000]
        close = df["miss_km"] < 2
        results.append((
            name, f"{average_precision_score(y, raw):.3f}", f"{brier_score_loss(y, p):.3f}", f"{p.mean():.3f}",
            f"{p[close].mean():.3f}", f"{op['precision']:.2f}", f"{op['recall']:.2f}", f"{y[top].mean():.2f}",
        ))
        feature_rows.append((name, *(f"{df[f'{r}_{c}'].median():.2f}" for c in UPDATE for r in ("stale",))))

    observed = (y.mean(), y[(test["miss_km"] < 2).to_numpy()].mean())
    table = lambda head, body: "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head), *("| " + " | ".join(r) + " |" for r in body)])  # noqa: E731
    lines = [
        "# Update history and the model's scores",
        "",
        f"Generated by `scripts/history_sensitivity.py` in {time.monotonic() - started:.0f} s on the 2025 test period "
        f"({len(test):,} approaches, model `{model.version}`). Data from Space-Track.org (USSPACECOM).",
        "",
        "Four of the model's inputs per object describe its recent update history (records in the last 30 days, "
        "largest and median orbit jump between records, median gap between records). Training used every element set "
        "Space-Track published. Live runs see only the snapshots our job took, so these inputs change; this shows how "
        "much, by rebuilding them as a snapshot job would have seen them and rescoring the same test approaches.",
        "",
        "## Inputs (median over test rows, older-data object)",
        "",
        table(["History", "Records in 30 days", "Max orbit jump (km)", "Median orbit jump (km)", "Median update gap (h)"], feature_rows),
        "",
        "## Scores",
        "",
        f"Observed rate of approaches still under 5 km: {observed[0]:.3f} overall, {observed[1]:.3f} for predicted misses "
        "under 2 km. The operating point is the validation threshold of the trained model, unchanged.",
        "",
        table(["History", "PR-AUC", "Brier", "Mean estimate", "Mean estimate, miss < 2 km", "Precision at op.", "Recall at op.", "Top-1,000 still close"], results),
        "",
        "## What this means",
        "",
        "- Thin history changes the model's **inputs**, not only a label: with one snapshot every object looks as if it "
        "had one record and no orbit changes, and the estimates shift with it (see the tables).",
        "- Snapshot cadence barely matters: every 2 h or every 6 h over 30 days scores like the full history. "
        "A short collection window matters: compare the 7- and 15-day rows with the observed rates.",
        "- The API marks every response from a run with under 25 days of snapshots (`history_complete: false`) and "
        "every estimate in it as provisional; the app shows that next to each estimate.",
        "- Publishing in CI must pull 30 days of snapshots first (`python -m src.ops.archive_sync pull --history-days 30`).",
        "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[lines.index("## Inputs (median over test rows, older-data object)"):]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
