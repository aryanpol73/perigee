"""Train and evaluate the conjunction model (build guide 8.3).

Protocol:
* split by prediction time (train, then validation, then test), never randomly;
  an embargo drops rows whose labels used data from the next period;
* baselines in order: (a) alert if predicted miss < label threshold,
  (b) miss-distance threshold tuned on validation, (c) logistic regression on a
  few features; main model LightGBM, tuned with Optuna on validation only, then
  isotonic calibration fitted on validation;
* metrics on the test period: PR-AUC, precision/recall at an operating point that
  tolerates `max_false_alarms_per_true` false alarms per true event (chosen on
  validation), Brier score, calibration table; bootstrap confidence intervals
  resampling object pairs, not rows;
* checks: grouped cross-validation, metrics without suspected manoeuvres,
  feature attributions (LightGBM SHAP values), failure analysis.

Every number is about estimated close approaches from public data; the label
uses newer public data as a better estimate, not ground truth.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import optuna
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, precision_recall_curve
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src.ml.features import FEATURES

log = logging.getLogger("perigee.ml.train")

LOGISTIC_FEATURES = ["miss_km", "rel_speed_km_s", "hours_to_tca", "stale_epoch_age_h", "fresh_epoch_age_h"]


class InsufficientData(RuntimeError):
    """Too little labelled history for an honest evaluation."""

    def __init__(self, message: str, counts: dict) -> None:
        super().__init__(message)
        self.counts = counts


@dataclass(frozen=True)
class TrainConfig:
    label_km: float = 5.0
    val_fraction: float = 0.2
    test_fraction: float = 0.2
    embargo: pd.Timedelta = pd.Timedelta(days=4)  # = dataset label cutoff
    # 1 false alarm per true event (precision >= 0.5). With a ~15% base rate a
    # looser budget is met by alerting on nearly everything and says nothing.
    max_false_alarms_per_true: float = 1.0
    optuna_trials: int = 30
    bootstrap: int = 500
    seed: int = 42
    min_prediction_times: int = 6
    min_positives_per_split: int = 10


@dataclass
class Split:
    train: pd.DataFrame
    val: pd.DataFrame
    test: pd.DataFrame
    boundaries: dict[str, str] = field(default_factory=dict)


def time_split(rows: pd.DataFrame, config: TrainConfig) -> Split:
    """Chronological split by prediction time, with an embargo before each later period."""
    times = np.sort(rows["prediction_time"].unique())
    counts = {"prediction_times": int(len(times)), "rows": int(len(rows)), "positives": int(rows["label"].sum())}
    if len(times) < config.min_prediction_times:
        raise InsufficientData(
            f"{len(times)} prediction times; need at least {config.min_prediction_times} for a time split",
            counts,
        )
    n = len(times)
    n_test = max(1, round(n * config.test_fraction))
    n_val = max(1, round(n * config.val_fraction))
    val_start, test_start = pd.Timestamp(times[n - n_test - n_val]), pd.Timestamp(times[n - n_test])

    t = rows["prediction_time"]
    train = rows[t <= val_start - config.embargo]
    val = rows[(t >= val_start) & (t <= test_start - config.embargo)]
    test = rows[t >= test_start]
    for name, part in (("train", train), ("validation", val), ("test", test)):
        counts[f"{name}_rows"] = int(len(part))
        counts[f"{name}_positives"] = int(part["label"].sum())
        if part["label"].sum() < config.min_positives_per_split or (part["label"] == 0).sum() == 0:
            raise InsufficientData(
                f"{name} period has {int(part['label'].sum())} positives and {int((part['label'] == 0).sum())} "
                f"negatives; need at least {config.min_positives_per_split} positives and some negatives",
                counts,
            )
    return Split(
        train,
        val,
        test,
        {
            "train_until": str(train["prediction_time"].max()),
            "validation_from": str(val_start),
            "validation_until": str(val["prediction_time"].max()),
            "test_from": str(test_start),
            "test_until": str(test["prediction_time"].max()),
        },
    )


# ---------------------------------------------------------------------------
# Metrics


def operating_threshold(scores: np.ndarray, y: np.ndarray, max_false_alarms_per_true: float) -> float:
    """Highest-recall score threshold whose precision allows at most N false alarms per true event."""
    precision, recall, thresholds = precision_recall_curve(y, scores)
    floor = 1.0 / (1.0 + max_false_alarms_per_true)
    ok = precision[:-1] >= floor
    if not ok.any():
        return float("inf")
    best = np.argmax(np.where(ok, recall[:-1], -1))
    return float(thresholds[best])


def point_metrics(y: np.ndarray, alerts: np.ndarray) -> dict[str, float]:
    tp = int((alerts & (y == 1)).sum())
    fp = int((alerts & (y == 0)).sum())
    fn = int((~alerts & (y == 1)).sum())
    return {
        "alerts": int(alerts.sum()),
        "precision": tp / (tp + fp) if tp + fp else float("nan"),
        "recall": tp / (tp + fn) if tp + fn else float("nan"),
        "false_alarms_per_true": fp / tp if tp else float("inf"),
    }


def calibration_table(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[dict[str, float]]:
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    edges = np.unique(edges)
    which = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, len(edges) - 2)
    out = []
    for b in range(len(edges) - 1):
        m = which == b
        if m.any():
            out.append({"mean_predicted": float(p[m].mean()), "observed_rate": float(y[m].mean()), "rows": int(m.sum())})
    return out


def bootstrap_ci(
    y: np.ndarray, scores: np.ndarray, groups: np.ndarray, metric, n: int, seed: int
) -> tuple[float, float]:
    """95% interval of `metric`, resampling groups (object pairs) with replacement."""
    rng = np.random.default_rng(seed)
    uniq, inverse = np.unique(groups, return_inverse=True)
    members = [np.flatnonzero(inverse == g) for g in range(len(uniq))]
    values = []
    for _ in range(n):
        pick = rng.integers(0, len(uniq), len(uniq))
        idx = np.concatenate([members[g] for g in pick])
        if y[idx].min() == y[idx].max():
            continue
        values.append(metric(y[idx], scores[idx]))
    if not values:
        return float("nan"), float("nan")
    return float(np.percentile(values, 2.5)), float(np.percentile(values, 97.5))


def _pair_groups(df: pd.DataFrame) -> np.ndarray:
    return (df["norad_a"].astype("int64") * 1_000_000_000 + df["norad_b"].astype("int64")).to_numpy()


# ---------------------------------------------------------------------------
# Models


def _lgb_params(trial: optuna.Trial | None, seed: int, pos_weight: float) -> dict[str, Any]:
    base = {
        "objective": "binary",
        "verbosity": -1,
        "seed": seed,
        "deterministic": True,
        "num_threads": 0,
        "scale_pos_weight": pos_weight,
    }
    if trial is None:
        return {**base, "learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 50}
    return {
        **base,
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
        "num_leaves": trial.suggest_int("num_leaves", 8, 128, log=True),
        "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 20, 500, log=True),
        "feature_fraction": trial.suggest_float("feature_fraction", 0.4, 1.0),
        "bagging_fraction": trial.suggest_float("bagging_fraction", 0.5, 1.0),
        "bagging_freq": 1,
        "lambda_l2": trial.suggest_float("lambda_l2", 1e-3, 10.0, log=True),
    }


def _fit_lgb(params: dict, train: pd.DataFrame, val: pd.DataFrame) -> lgb.Booster:
    dtrain = lgb.Dataset(train[FEATURES], train["label"])
    dval = lgb.Dataset(val[FEATURES], val["label"], reference=dtrain)
    return lgb.train(
        params,
        dtrain,
        num_boost_round=5000,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(100, verbose=False)],
    )


def train_and_evaluate(rows: pd.DataFrame, config: TrainConfig = TrainConfig()) -> tuple[dict, lgb.Booster, IsotonicRegression]:
    split = time_split(rows, config)
    tr, va, te = split.train, split.val, split.test
    y_va, y_te = va["label"].to_numpy(), te["label"].to_numpy()
    groups_te = _pair_groups(te)
    fa = config.max_false_alarms_per_true
    results: dict[str, Any] = {"config": {k: str(v) for k, v in asdict(config).items()}, "split": split.boundaries}
    results["counts"] = {
        name: {"rows": int(len(d)), "positives": int(d["label"].sum()), "prediction_times": int(d["prediction_time"].nunique())}
        for name, d in (("train", tr), ("validation", va), ("test", te))
    }

    models: dict[str, dict[str, Any]] = {}

    def evaluate(name: str, s_va: np.ndarray, s_te: np.ndarray, probs_te: np.ndarray | None, threshold: float | None = None) -> None:
        th = operating_threshold(s_va, y_va, fa) if threshold is None else threshold
        ap = average_precision_score(y_te, s_te)
        entry = {
            "pr_auc": float(ap),
            "pr_auc_ci95": bootstrap_ci(y_te, s_te, groups_te, average_precision_score, config.bootstrap, config.seed),
            "operating_threshold": th,
            "at_operating_point": point_metrics(y_te, s_te >= th),
        }
        if probs_te is not None:
            entry["brier"] = float(brier_score_loss(y_te, probs_te))
            entry["calibration"] = calibration_table(y_te, probs_te)
        models[name] = entry

    # (a) the plain screening alert: predicted miss below the label threshold.
    alert_va = (va["miss_km"] < config.label_km).to_numpy().astype(float)
    alert_te = (te["miss_km"] < config.label_km).to_numpy().astype(float)
    evaluate("a_alert_if_predicted_miss_below_threshold", alert_va, alert_te, None, threshold=0.5)
    # (b) a miss-distance threshold tuned on validation (score = -miss).
    evaluate("b_tuned_miss_threshold", -va["miss_km"].to_numpy(), -te["miss_km"].to_numpy(), None)
    # (c) logistic regression on a few features.
    logit = make_pipeline(StandardScaler(), LogisticRegression(class_weight="balanced", max_iter=2000, random_state=config.seed))
    logit.fit(tr[LOGISTIC_FEATURES].fillna(0), tr["label"])
    p_va = logit.predict_proba(va[LOGISTIC_FEATURES].fillna(0))[:, 1]
    p_te = logit.predict_proba(te[LOGISTIC_FEATURES].fillna(0))[:, 1]
    iso_logit = IsotonicRegression(out_of_bounds="clip").fit(p_va, y_va)
    evaluate("c_logistic_regression", p_va, p_te, iso_logit.predict(p_te))

    # Main model: LightGBM tuned on validation only.
    pos_weight = float((tr["label"] == 0).sum() / max(1, tr["label"].sum()))
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(trial: optuna.Trial) -> float:
        booster = _fit_lgb(_lgb_params(trial, config.seed, pos_weight), tr, va)
        return average_precision_score(y_va, booster.predict(va[FEATURES]))

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=config.seed))
    study.optimize(objective, n_trials=config.optuna_trials)
    best_params = _lgb_params(optuna.trial.FixedTrial(study.best_params), config.seed, pos_weight)
    booster = _fit_lgb(best_params, tr, va)
    raw_va, raw_te = booster.predict(va[FEATURES]), booster.predict(te[FEATURES])
    calibrator = IsotonicRegression(out_of_bounds="clip").fit(raw_va, y_va)
    cal_te = calibrator.predict(raw_te)
    evaluate("lightgbm", raw_va, raw_te, cal_te)
    models["lightgbm"]["best_params"] = study.best_params
    models["lightgbm"]["best_iteration"] = int(booster.best_iteration)
    results["models"] = models

    best_baseline = max((k for k in models if k != "lightgbm"), key=lambda k: models[k]["pr_auc"])
    results["lightgbm_beats_all_baselines"] = bool(
        models["lightgbm"]["pr_auc_ci95"][0] > models[best_baseline]["pr_auc"]
    )
    results["lightgbm_beats_all_baselines_point"] = bool(models["lightgbm"]["pr_auc"] > models[best_baseline]["pr_auc"])
    results["best_baseline"] = best_baseline

    # Without suspected manoeuvres (their labels describe a different trajectory).
    clean = ~te["manoeuvre_suspected"].to_numpy()
    if clean.any() and y_te[clean].min() != y_te[clean].max():
        results["test_without_manoeuvres"] = {
            "rows": int(clean.sum()),
            "lightgbm_pr_auc": float(average_precision_score(y_te[clean], raw_te[clean])),
            "tuned_threshold_pr_auc": float(average_precision_score(y_te[clean], -te["miss_km"].to_numpy()[clean])),
        }

    # Grouped cross-validation on train + validation as a sanity check of the time split.
    both = pd.concat([tr, va])
    cv = []
    for k, (a, b) in enumerate(GroupKFold(n_splits=5).split(both, both["label"], _pair_groups(both))):
        fold_tr, fold_va = both.iloc[a], both.iloc[b]
        if fold_va["label"].nunique() < 2:
            continue
        m = _fit_lgb(best_params, fold_tr, fold_va)
        cv.append(float(average_precision_score(fold_va["label"], m.predict(fold_va[FEATURES]))))
    results["grouped_cv_pr_auc"] = {"folds": cv, "mean": float(np.mean(cv)) if cv else None}

    # Feature attributions (SHAP values from LightGBM itself).
    contrib = booster.predict(te[FEATURES], pred_contrib=True)[:, :-1]
    importance = pd.Series(np.abs(contrib).mean(axis=0), index=FEATURES).sort_values(ascending=False)
    results["mean_abs_shap"] = {k: float(v) for k, v in importance.head(15).items()}

    results["failure_analysis"] = failure_analysis(te, cal_te, models["lightgbm"]["operating_threshold"], raw_te)
    return results, booster, calibrator


def failure_analysis(test: pd.DataFrame, prob: np.ndarray, threshold: float, raw: np.ndarray) -> dict:
    """Worst misses and false alarms, and which known causes they share."""
    df = test.assign(prob=prob, raw=raw)
    categories = {
        "stale data (an element set > 3 days old at T)": df["stale_epoch_age_h"] > 72,
        "suspected manoeuvre": df["manoeuvre_suspected"],
        "low perigee (< 350 km, decaying)": np.minimum(df["stale_perigee_km"], df["fresh_perigee_km"]) < 350,
        "slow encounter (< 1 km/s)": df["rel_speed_km_s"] < 1.0,
        "TCA more than 2 days ahead": df["hours_to_tca"] > 48,
    }
    fn = df[(df["label"] == 1) & (df["raw"] < threshold)].nsmallest(25, "raw")
    fp = df[(df["label"] == 0) & (df["raw"] >= threshold)].nlargest(25, "raw")
    def share(rows: pd.DataFrame) -> dict[str, float]:
        return {name: float(mask.loc[rows.index].mean()) if len(rows) else float("nan") for name, mask in categories.items()}
    cols = ["prediction_time", "norad_a", "norad_b", "tca", "miss_km", "refreshed_miss_km", "prob"]
    return {
        "base_rates_in_test": {name: float(mask.mean()) for name, mask in categories.items()},
        "worst_false_negatives_share": share(fn),
        "worst_false_positives_share": share(fp),
        "worst_false_negatives": fn[cols].astype(str).to_dict("records")[:10],
        "worst_false_positives": fp[cols].astype(str).to_dict("records")[:10],
    }


def positive_counts(rows: pd.DataFrame, thresholds=(1.0, 2.0, 5.0, 10.0)) -> pd.DataFrame:
    """Positives per prediction time at several label thresholds (for the ADR, guide 8.8)."""
    out = rows.groupby("prediction_time").agg(rows=("label", "size"))
    for km in thresholds:
        out[f"positives_lt_{km:g}km"] = rows.groupby("prediction_time")["refreshed_miss_km"].apply(lambda s, k=km: int((s < k).sum()))
    return out.reset_index()


def save(results: dict, booster: lgb.Booster, calibrator: IsotonicRegression, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(out_dir / "lightgbm.txt"))
    (out_dir / "calibrator.json").write_text(
        json.dumps({"x": calibrator.X_thresholds_.tolist(), "y": calibrator.y_thresholds_.tolist()})
    )
    (out_dir / "results.json").write_text(json.dumps(results, indent=2, default=str))
    return out_dir
