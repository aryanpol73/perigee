"""One command to rebuild the ML dataset, train, evaluate and write the model card.

    python -m src.main train            # rebuild dataset from history, then train
    python -m src.main train --reuse    # train on the stored dataset

Writes data/processed/ml/dataset.parquet (+ counts), data/models/<version>/
(model, calibrator, results.json) and docs/model-card.md.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from src.config import Settings
from src.ml.dataset import DatasetConfig, build_for_time, prediction_times
from src.ml.features import FEATURES, build_features
from src.ml.model_card import write_model_card
from src.ml.train import InsufficientData, TrainConfig, positive_counts, save, train_and_evaluate
from src.screening.run import load_satcat
from src.storage import parquet_store as store
from src.storage.elsets import ElsetHistory

log = logging.getLogger("perigee.ml.pipeline")

ROOT = Path(__file__).resolve().parents[2]


def ml_dir(settings: Settings) -> Path:
    return settings.processed_dir / "ml"


def _space_weather(settings: Settings) -> pd.DataFrame | None:
    path = store.table_path(settings.processed_dir, store.SPACE_WEATHER)
    if not path.exists():
        return None
    con = store.connect()
    try:
        return con.execute(f"SELECT * FROM {store.table_relation(settings.processed_dir, store.SPACE_WEATHER)}").df()
    finally:
        con.close()


def build_dataset(
    settings: Settings,
    config: DatasetConfig,
    every_days: float = 1.0,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
) -> pd.DataFrame:
    history = ElsetHistory(settings.processed_dir)
    satcat = load_satcat(settings)
    sw = _space_weather(settings)
    times = prediction_times(history, config, every_days, start, end)
    span = history.span()
    log.info("building dataset", extra={"prediction_times": len(times), "history": [str(span[0]), str(span[1])]})
    parts, counts = [], []
    for t in times:
        rows, catalog, c = build_for_time(history, t, satcat, config)
        counts.append(c)
        if rows.empty:
            continue
        feats = build_features(rows, catalog, satcat, history.update_stats(t), sw, t)
        parts.append(pd.concat([rows.drop(columns=[f for f in FEATURES if f in rows]), feats], axis=1))
    dataset = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["prediction_time", "label", *FEATURES])

    out = ml_dir(settings)
    out.mkdir(parents=True, exist_ok=True)
    store.write_parquet(dataset, out / "dataset.parquet")
    summary = {
        "config": asdict(config),
        "history_span": [str(span[0]), str(span[1])],
        "prediction_times": [str(t) for t in times],
        "per_time": counts,
    }
    if len(dataset):
        summary["positives_by_threshold"] = positive_counts(dataset).astype(str).to_dict("records")
    (out / "dataset.json").write_text(json.dumps(summary, indent=2, default=str))
    return dataset


def load_dataset(settings: Settings) -> pd.DataFrame:
    path = ml_dir(settings) / "dataset.parquet"
    if not path.exists():
        raise SystemExit("No ML dataset yet. Run: python -m src.main train")
    con = store.connect()
    try:
        return con.execute(f"SELECT * FROM read_parquet('{store.sql_path(path)}')").df()
    finally:
        con.close()


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def run_train(
    settings: Settings,
    dataset_config: DatasetConfig = DatasetConfig(),
    train_config: TrainConfig = TrainConfig(),
    reuse: bool = False,
    every_days: float = 1.0,
    model_card: Path | None = ROOT / "docs" / "model-card.md",
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
) -> dict:
    dataset = load_dataset(settings) if reuse else build_dataset(settings, dataset_config, every_days, start, end)
    try:
        results, booster, calibrator = train_and_evaluate(dataset, train_config)
    except InsufficientData as exc:
        return {"status": "insufficient_data", "reason": str(exc), "counts": exc.counts}

    window = (str(dataset["prediction_time"].min()), str(dataset["prediction_time"].max()))
    commit = _commit()
    version = f"{pd.Timestamp(window[1]):%Y%m%d}-{commit[:7]}"
    results.update(
        status="trained",
        model_version=version,
        commit=commit,
        data_window=window,
        dataset_config=asdict(dataset_config),
        features=FEATURES,
    )
    out = save(results, booster, calibrator, settings.data_dir / "models" / version)
    if model_card is not None:
        write_model_card(results, model_card)
    results["saved_to"] = str(out)
    return results
