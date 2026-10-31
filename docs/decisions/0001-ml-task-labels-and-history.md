# ADR 0001: ML task, labels and where the history comes from

Status: accepted, 2026-10-31. Positive counts (guide 8.8) to be added once history is loaded.

## Context

Phase 4 asks: will a close approach flagged on day T still look close when better data
arrives? That needs many past prediction times, each with fresher orbit data afterwards,
split by time into train, validation and test periods. On 2026-10-31 Perigee held a few
hours of its own snapshots. CelesTrak has no history API.

## Decisions

1. **Task and unit.** One row per (object pair, prediction time T). At each T, screen the
   catalog *as published by T* for 3 days with a 10 km candidate threshold (wider than the
   label, so the tuned-threshold baseline and positive counts at several thresholds are
   possible).
2. **Label.** For each object, take the first element set with epoch at or after the
   predicted TCA among those published by T + 4 days; recompute the closest approach within
   10 minutes of the predicted TCA. Label 1 if under 5 km. Newer data is a better estimate,
   not ground truth. Rows without a newer set for both objects are dropped and counted.
3. **Point in time.** Every element set carries `available_at` (when it could have been
   known). Features use only sets with `available_at <= T`, the last fully observed space
   weather day before T, and update statistics from sets published by T.
4. **Same-constellation pairs excluded** by default. Screening showed Starlink-Starlink
   approaches triple over a week because public data cannot model station-keeping, and
   SOCRATES omits most of them too. Including them would teach the model about artefacts.
   The flag is configurable and the exclusion is stated on the model card.
5. **History from Space-Track yearly archives, imported by hand.** Space-Track's rules
   limit `gp_history` to once per lifetime and ask for the yearly zip bundles for long
   ranges. A human downloads each zip once into `data/raw/spacetrack/` and runs
   `python -m src.main import-spacetrack`. Perigee's code never logs in to Space-Track.
6. **Publication lag for archived TLEs is 12 hours**, because TLE files carry no
   publication time. Too late a guess only wastes data; too early would leak. Revisit with
   our own snapshots: first-seen minus epoch gives an upper bound on the real lag.
7. **Evaluation as the guide requires:** chronological split with a 4-day embargo
   (the label cutoff) before validation and test; baselines (alert at 5 km, tuned
   threshold, logistic regression) before LightGBM; Optuna on validation only; isotonic
   calibration on validation; bootstrap intervals by object pair; the code refuses to report
   numbers with fewer than 6 prediction times or 10 positives in any period.

## Consequences

- The model card and results only exist after history is imported (or our cron has run for
  weeks). Until then `python -m src.main train` exits with code 3 and the counts.
- Space-Track data must be cited ("Data from Space-Track.org (USSPACECOM)") wherever results
  are published. Raw archives stay in the private store.
