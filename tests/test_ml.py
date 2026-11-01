"""ML stage: point-in-time history, labels from refreshed data, features, training protocol."""

import json

import numpy as np
import pandas as pd
import pytest

from src.ml.dataset import DatasetConfig, build_for_time
from src.ml.features import FEATURES, build_features
from src.ml.model_card import write_model_card
from src.ml.train import InsufficientData, TrainConfig, operating_threshold, time_split, tradeoff_table, train_and_evaluate
from src.storage import parquet_store as store
from src.storage.elsets import ElsetHistory
from tests.test_screening import _mini_catalog

T = pd.Timestamp("2026-10-30T00:00:00", tz="UTC")
PUBLISHED_BEFORE_T = pd.Timestamp("2026-10-29T20:00:00", tz="UTC")
PUBLISHED_AFTER_T = T + pd.Timedelta(days=1)  # after T, before the label cutoff (T + 4 h + 1 day)


def _write_snapshot(processed, catalog: pd.DataFrame, when: pd.Timestamp) -> None:
    sid = when.strftime("%Y%m%dT%H%M%SZ")
    df = catalog.copy()
    df.insert(0, "snapshot_id", sid)
    df.insert(1, "snapshot_time", when)
    df["sgp4_error"] = 0
    store.write_parquet(df, store.catalog_snapshot_path(processed, sid))


@pytest.fixture
def history(tmp_path, gp_bytes):
    """Snapshot 1 (published before T) and a refreshed snapshot 2 (published after T).

    Snapshot 2 repeats every set with an epoch 1 ms later (the same trajectory),
    except SYNTH-4, nudged 0.1 deg along its orbit (~12 km): its encounters
    should stop looking close when "better data" arrives.
    """
    catalog = _mini_catalog(gp_bytes)
    _write_snapshot(tmp_path, catalog, PUBLISHED_BEFORE_T)
    fresh = catalog.copy()
    fresh["epoch"] = fresh["epoch"] + pd.Timedelta(milliseconds=1)
    nudged = fresh["object_name"] == "SYNTH-4"
    fresh.loc[nudged, "mean_anomaly"] = (fresh.loc[nudged, "mean_anomaly"] + 0.1) % 360
    _write_snapshot(tmp_path, fresh, PUBLISHED_AFTER_T)
    return ElsetHistory(tmp_path), catalog


CONFIG = DatasetConfig(horizon_days=4 / 24, scope="all", exclude_same_family=False, workers=1, label_delay_days=1.0)


def test_catalog_at_is_point_in_time(history):
    h, catalog = history
    assert h.catalog_at(PUBLISHED_BEFORE_T - pd.Timedelta(minutes=1)).empty
    at_t = h.catalog_at(T)
    assert len(at_t) == len(catalog)
    # The refreshed sets exist but were published after T, so T still sees the originals.
    np.testing.assert_array_equal(at_t.sort_values("norad_id")["epoch"], catalog.sort_values("norad_id")["epoch"])
    later = h.catalog_at(PUBLISHED_AFTER_T).set_index("norad_id")["epoch"]
    original = catalog.set_index("norad_id")["epoch"].reindex(later.index)
    assert (later > original).all()


def test_labels_follow_refreshed_data(history):
    h, _ = history
    rows, catalog, counts = build_for_time(h, T, None, CONFIG)
    assert counts["labelled"] == len(rows) > 0
    synth4 = int(catalog.loc[catalog["object_name"] == "SYNTH-4", "norad_id"].iloc[0])
    involves = (rows["norad_a"] == synth4) | (rows["norad_b"] == synth4)

    same = rows[~involves]
    # Unchanged trajectories: the refreshed miss equals the predicted one.
    np.testing.assert_allclose(same["refreshed_miss_km"], same["miss_km"], atol=0.01)
    assert (same["label"] == (same["miss_km"] < CONFIG.label_km)).all()

    moved = rows[involves & (rows["miss_km"] < CONFIG.label_km)]
    assert len(moved) > 0
    # Predicted under 5 km from the old data; the refreshed data says they pass wider.
    assert (moved["refreshed_miss_km"] > CONFIG.label_km).all()
    assert (moved["refreshed_miss_km"] > moved["miss_km"] + 1).all()
    assert (moved["label"] == 0).all()


def test_unlabelled_rows_are_dropped_and_counted(tmp_path, gp_bytes):
    _write_snapshot(tmp_path, _mini_catalog(gp_bytes), PUBLISHED_BEFORE_T)  # no newer data at all
    rows, _, counts = build_for_time(ElsetHistory(tmp_path), T, None, CONFIG)
    assert rows.empty and counts["unlabelled"] > 0 and counts["labelled"] == 0


def test_same_family_pairs_can_be_excluded(history):
    h, _ = history
    rows, _, counts = build_for_time(h, T, None, DatasetConfig(**{**CONFIG.__dict__, "exclude_same_family": True}))
    assert counts["excluded_same_family"] > 0
    assert not rows["same_family"].any()


def test_features_use_only_data_available_at_t(history):
    h, _ = history
    rows, catalog, _ = build_for_time(h, T, None, CONFIG)
    sw = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-10-28", "2026-10-29", "2026-10-30"]),
            "f107_data_type": ["OBS", "OBS", "OBS"],
            "f107_obs": [100.0, 110.0, 999.0],
            "f107_obs_center81": [90.0, 91.0, 999.0],
            "ap_avg": [5.0, 6.0, 999.0],
        }
    )
    feats = build_features(rows, catalog, None, h.update_stats(T), sw, T)
    assert list(feats.columns) == FEATURES
    assert (feats["f107_obs"] == 110.0).all()  # the last day before T, never T's own day
    # Epoch ages are measured from the sets known at T, not the refreshed ones.
    assert (feats["stale_epoch_age_h"] > 10).all()
    assert (feats["stale_n_records_30d"] == 1).all()  # the refreshed set is not counted yet
    assert (feats["stale_epoch_age_h"] >= feats["fresh_epoch_age_h"]).all()


# ---------------------------------------------------------------------------
# Training protocol on a synthetic table (fast, no orbits).


def _synthetic_rows(n_times: int = 14, per_time: int = 400, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    parts = []
    for k in range(n_times):
        t = pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(days=k)
        miss = rng.uniform(0, 10, per_time)
        age = rng.uniform(0, 120, per_time)
        # True danger: close and fresh data; stale predictions are unreliable.
        logit = 2.0 - 0.9 * miss - 0.03 * age + rng.normal(0, 0.7, per_time)
        df = pd.DataFrame({f: rng.normal(size=per_time) for f in FEATURES})
        df["miss_km"], df["stale_epoch_age_h"] = miss, age
        df["fresh_epoch_age_h"] = age * rng.uniform(0, 1, per_time)
        df["label"] = (logit > 0).astype(int)
        df["prediction_time"] = t
        df["tca"] = t + pd.Timedelta(days=1)
        df["norad_a"] = rng.integers(1, 300, per_time)
        df["norad_b"] = df["norad_a"] + rng.integers(1, 300, per_time)
        df["refreshed_miss_km"] = np.where(df["label"] == 1, 1.0, 8.0)
        df["manoeuvre_suspected"] = rng.random(per_time) < 0.05
        parts.append(df)
    return pd.concat(parts, ignore_index=True)


FAST = TrainConfig(optuna_trials=3, bootstrap=30, embargo=pd.Timedelta(days=2))


def test_time_split_is_chronological_with_embargo():
    split = time_split(_synthetic_rows(), FAST)
    assert split.train["prediction_time"].max() <= pd.Timestamp(split.boundaries["validation_from"]) - FAST.embargo
    assert split.val["prediction_time"].max() <= pd.Timestamp(split.boundaries["test_from"]) - FAST.embargo
    assert split.val["prediction_time"].min() > split.train["prediction_time"].max()
    assert split.test["prediction_time"].min() > split.val["prediction_time"].max()


def test_too_little_history_is_refused():
    with pytest.raises(InsufficientData, match="prediction times"):
        time_split(_synthetic_rows(n_times=3), FAST)
    rows = _synthetic_rows()
    rows["label"] = 0
    with pytest.raises(InsufficientData, match="positives"):
        time_split(rows, FAST)


def test_train_and_evaluate_reports_everything(tmp_path):
    results, booster, calibrator = train_and_evaluate(_synthetic_rows(), FAST)
    models = results["models"]
    assert set(models) == {
        "a_alert_if_predicted_miss_below_threshold",
        "b_tuned_miss_threshold",
        "c_logistic_regression",
        "lightgbm",
    }
    # Data age matters in this synthetic world, so the model must beat the plain alert.
    assert models["lightgbm"]["pr_auc"] > models["a_alert_if_predicted_miss_below_threshold"]["pr_auc"]
    lo, hi = models["lightgbm"]["pr_auc_ci95"]
    assert lo <= models["lightgbm"]["pr_auc"] <= hi
    assert 0 <= models["lightgbm"]["brier"] <= 1
    assert {"stale_epoch_age_h", "miss_km"} & set(list(results["mean_abs_shap"])[:3])
    assert "worst_false_negatives" in results["failure_analysis"]

    results.update(
        status="trained", model_version="test", commit="0" * 40, data_window=("a", "b"),
        dataset_config=DatasetConfig().__dict__, features=FEATURES,
    )
    card = tmp_path / "model-card.md"
    write_model_card(json.loads(json.dumps(results, default=str)), card)
    text = card.read_text()
    assert "Not for operational decisions" in text and "LightGBM" in text


def test_operating_threshold_respects_false_alarm_budget():
    y = np.array([1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0])
    s = np.array([0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05, 0.85, 0.01])
    th = operating_threshold(s, y, max_false_alarms_per_true=1.0)
    alerts = s >= th
    tp, fp = int((alerts & (y == 1)).sum()), int((alerts & (y == 0)).sum())
    assert fp <= tp
    assert tp == 3


def test_tradeoff_table_precision_at_recall_and_top_k():
    y = np.array([1, 1, 0, 1, 0, 0, 0, 0])
    s = np.array([0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2])
    t = tradeoff_table(y, s, recalls=(0.5, 1.0), top_k=(2, 4, 100))
    assert [r["precision"] for r in t["precision_at_recall"]] == [1.0, 0.75]
    assert t["precision_at_top_k"] == [{"k": 2, "precision": 1.0}, {"k": 4, "precision": 0.75}]
