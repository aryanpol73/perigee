# Model card: conjunction false-alarm model

Model version `20251225-feaaf72` (commit `feaaf72682cf`), data window 2025-01-09 00:00:00+00:00 to 2025-12-25 00:00:00+00:00.

> **Not for operational decisions.** Inputs are public orbit data with roughly kilometre-level error and no covariance. Outputs are risk estimates about estimated close approaches, not collision predictions.

## Intended use

Rank close approaches flagged by Perigee's screening by how likely they are to still look close when newer public orbit data arrives, so that a viewer can tell persistent approaches from artefacts of stale data. Portfolio and research use only.

## Data and label

- One row per (object pair, prediction time T). At each T the catalog as published by T is screened for 3 days with a 10 km threshold (active scope, same-constellation pairs excluded; a uniform random sample of 20,000 events per prediction time).
- **Label** = 1 if the miss distance recomputed near the predicted TCA, from each object's first element set with epoch at or after TCA published within 4 days of T, is under 5 km. Newer data is a better estimate, not ground truth.
- Features use only data available at T: geometry, orbit state, data quality (epoch age, update history), drag proxies (B*, solar flux, geomagnetic index), object type.

| Split | Prediction times | Rows | Positives |
|---|---|---|---|
| train | 31 | 606,472 | 100,629 |
| validation | 10 | 192,958 | 28,729 |
| test | 10 | 194,140 | 26,576 |

Chronological split with a 4 days 00:00:00 embargo before validation and test, so no training label uses data from a later period.

## Results on the test period

Operating point chosen on validation: highest recall with at most 1.0 false alarms per true event.

| Model | PR-AUC (95% CI, pair bootstrap) | Precision | Recall | Brier |
|---|---|---|---|---|
| (a) Alert if predicted miss < threshold | 0.293 (0.289-0.297) | 0.368 | 0.673 | n/a |
| (b) Miss-distance threshold tuned on validation | 0.356 (0.350-0.361) | 0.375 | 0.000 | n/a |
| (c) Logistic regression, 5 features | 0.386 (0.380-0.392) | 0.462 | 0.222 | 0.097 |
| **LightGBM (main model)** | 0.698 (0.693-0.704) | 0.496 | 0.762 | 0.069 |

LightGBM beats every baseline, with the lower end of its PR-AUC interval above the best baseline.

Without rows where either object probably manoeuvred (167,293 rows): LightGBM PR-AUC 0.718, tuned threshold 0.404.

Grouped 5-fold cross-validation by object pair on train + validation: mean PR-AUC 0.735. A much higher value than the time-split test suggests leakage or drift.

## What drives predictions

Mean absolute SHAP value on the test period (top features):

| Feature | Mean abs. SHAP |
|---|---|
| `miss_radial_km` | 5.1337 |
| `miss_km` | 1.0287 |
| `stale_bstar` | 0.3860 |
| `fresh_perigee_km` | 0.2565 |
| `stale_mean_motion` | 0.2488 |
| `stale_apogee_km` | 0.2420 |
| `approach_angle_deg` | 0.2343 |
| `fresh_mean_motion` | 0.2328 |
| `fresh_bstar` | 0.2313 |
| `miss_cross_km` | 0.2279 |
| `stale_sma_jump_median_km` | 0.2114 |
| `fresh_apogee_km` | 0.2051 |
| `fresh_update_gap_median_h` | 0.2005 |
| `stale_sma_jump_max_km` | 0.2001 |
| `rel_speed_km_s` | 0.1901 |

## Known failure modes

Share of the worst 25 false negatives / false positives with each known cause, against its rate in the test set:

| Cause | Test base rate | Worst false negatives | Worst false positives |
|---|---|---|---|
| stale data (an element set > 3 days old at T) | 3% | 0.08 | 0.00 |
| suspected manoeuvre | 14% | 0.48 | 0.28 |
| low perigee (< 350 km, decaying) | 1% | 0.00 | 0.00 |
| slow encounter (< 1 km/s) | 1% | 0.08 | 0.12 |
| TCA more than 2 days ahead | 33% | 0.36 | 0.16 |

- Manoeuvres between T and TCA make the label describe a different trajectory.
- Intra-constellation pairs are excluded (station-keeping makes most of them artefacts); the model says nothing about them.
- The catalog lacks most debris and rocket bodies unless Space-Track history is loaded.
