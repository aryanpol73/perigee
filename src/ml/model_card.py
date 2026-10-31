"""docs/model-card.md, generated from a results.json (build guide 8.4)."""

from __future__ import annotations

from pathlib import Path

LABELS = {
    "a_alert_if_predicted_miss_below_threshold": "(a) Alert if predicted miss < threshold",
    "b_tuned_miss_threshold": "(b) Miss-distance threshold tuned on validation",
    "c_logistic_regression": "(c) Logistic regression, 5 features",
    "lightgbm": "**LightGBM (main model)**",
}


def _fmt(x: float | None, digits: int = 3) -> str:
    return "n/a" if x is None or x != x else f"{x:.{digits}f}"


def write_model_card(results: dict, path: Path) -> None:
    d = results["dataset_config"]
    c = results["counts"]
    fa = results["config"]["max_false_alarms_per_true"]
    lines = [
        "# Model card: conjunction false-alarm model",
        "",
        f"Model version `{results['model_version']}` (commit `{results['commit'][:12]}`), data window "
        f"{results['data_window'][0]} to {results['data_window'][1]}.",
        "",
        "> **Not for operational decisions.** Inputs are public orbit data with roughly kilometre-level "
        "error and no covariance. Outputs are risk estimates about estimated close approaches, not "
        "collision predictions.",
        "",
        "## Intended use",
        "",
        "Rank close approaches flagged by Perigee's screening by how likely they are to still look close "
        "when newer public orbit data arrives, so that a viewer can tell persistent approaches from "
        "artefacts of stale data. Portfolio and research use only.",
        "",
        "## Data and label",
        "",
        f"- One row per (object pair, prediction time T). At each T the catalog as published by T is screened "
        f"for {d['horizon_days']:g} days with a {d['candidate_km']:g} km threshold "
        f"({d['scope']} scope{', same-constellation pairs excluded' if d['exclude_same_family'] else ''}"
        + (f"; a uniform random sample of {d['max_rows_per_time']:,} events per prediction time" if d.get("max_rows_per_time") else "")
        + ").",
        f"- **Label** = 1 if the miss distance recomputed near the predicted TCA, from each object's first "
        f"element set with epoch at or after TCA published within {d['horizon_days'] + d['label_delay_days']:g} "
        f"days of T, is under {d['label_km']:g} km. Newer data is a better estimate, not ground truth.",
        "- Features use only data available at T: geometry, orbit state, data quality (epoch age, update "
        "history), drag proxies (B*, solar flux, geomagnetic index), object type.",
        "",
        "| Split | Prediction times | Rows | Positives |",
        "|---|---|---|---|",
        *(f"| {k} | {v['prediction_times']} | {v['rows']:,} | {v['positives']:,} |" for k, v in c.items()),
        "",
        f"Chronological split with a {results['config']['embargo']} embargo before validation and test, so "
        "no training label uses data from a later period.",
        "",
        "## Results on the test period",
        "",
        f"Operating point chosen on validation: highest recall with at most {fa} false alarms per true event.",
        "",
        "| Model | PR-AUC (95% CI, pair bootstrap) | Precision | Recall | Brier |",
        "|---|---|---|---|---|",
    ]
    for key, m in results["models"].items():
        lo, hi = m["pr_auc_ci95"]
        op = m["at_operating_point"]
        lines.append(
            f"| {LABELS.get(key, key)} | {_fmt(m['pr_auc'])} ({_fmt(lo)}-{_fmt(hi)}) | {_fmt(op['precision'])} "
            f"| {_fmt(op['recall'])} | {_fmt(m.get('brier'))} |"
        )
    verdict = (
        "LightGBM beats every baseline, with the lower end of its PR-AUC interval above the best baseline."
        if results["lightgbm_beats_all_baselines"]
        else (
            "LightGBM's point PR-AUC beats every baseline, but its confidence interval overlaps the best "
            f"baseline ({LABELS.get(results['best_baseline'])}), so the improvement is not established."
            if results["lightgbm_beats_all_baselines_point"]
            else f"**LightGBM does not beat the best baseline** ({LABELS.get(results['best_baseline'])}). Reported as is."
        )
    )
    lines += ["", verdict, ""]
    if "test_without_manoeuvres" in results:
        w = results["test_without_manoeuvres"]
        lines += [
            f"Without rows where either object probably manoeuvred ({w['rows']:,} rows): LightGBM PR-AUC "
            f"{_fmt(w['lightgbm_pr_auc'])}, tuned threshold {_fmt(w['tuned_threshold_pr_auc'])}.",
            "",
        ]
    cv = results.get("grouped_cv_pr_auc", {})
    lines += [
        f"Grouped 5-fold cross-validation by object pair on train + validation: mean PR-AUC {_fmt(cv.get('mean'))}. "
        "A much higher value than the time-split test suggests leakage or drift.",
        "",
        "## What drives predictions",
        "",
        "Mean absolute SHAP value on the test period (top features):",
        "",
        "| Feature | Mean abs. SHAP |",
        "|---|---|",
        *(f"| `{k}` | {v:.4f} |" for k, v in results["mean_abs_shap"].items()),
        "",
        "## Known failure modes",
        "",
        "Share of the worst 25 false negatives / false positives with each known cause, against its rate in the test set:",
        "",
        "| Cause | Test base rate | Worst false negatives | Worst false positives |",
        "|---|---|---|---|",
    ]
    fa_ = results["failure_analysis"]
    for name, base in fa_["base_rates_in_test"].items():
        lines.append(
            f"| {name} | {base:.0%} | {_fmt(fa_['worst_false_negatives_share'][name], 2)} | "
            f"{_fmt(fa_['worst_false_positives_share'][name], 2)} |"
        )
    lines += [
        "",
        "- Manoeuvres between T and TCA make the label describe a different trajectory.",
        "- Intra-constellation pairs are excluded (station-keeping makes most of them artefacts); the model says nothing about them.",
        "- The catalog lacks most debris and rocket bodies unless Space-Track history is loaded.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
