# ADR 0002: The API serves a published results bundle; the simulator runs on the server

Status: accepted, 2026-11-01.

## Context

The guide (sections 4, 10, 13.3) asks for a read-only API on a free host, a
static orbit file for the browser, versioned results, last-good-run publishing,
and a decision on where the breakup simulator runs.

## Decisions

1. **One results bundle per run** (`python -m src.main publish`): meta, objects,
   scored conjunctions, shell index and history, model card summary and a
   content-hashed orbit file, written to a temporary directory, validated, then
   moved into place before `LATEST.json` switches to it. The API loads the
   bundle into memory at startup and never touches Parquet per request, never
   downloads, never screens. A new run means a restart.
2. **The orbit file** (`orbits.<hash>.json.gz`, ~1 MB for 20,000 objects) is
   pre-compressed and immutable. Locally the API serves it at
   `/v1/orbits/<name>`; in production it can sit on the CDN next to the
   frontend. `/v1/meta` names the current file.
3. **The simulator runs on the server** (`POST /v1/simulate`), not in the
   browser. The Python implementation is the one validated in
   `docs/risk-validation.md` (fragment counts, mass budget, spread) and the what-if
   needs the shell index over the whole catalog; a TypeScript port would be a
   second implementation to keep in step. Costs are bounded: inputs have hard
   caps, fragments are capped at 5,000 simulated and 2,000 returned, at most 12
   moments of at most one year, a 15 s time limit, 10 requests per minute per IP.
   Measured: 0.75-1.5 s per call on a laptop.
4. **Same-constellation pairs are left out of the bundle** (776,580 of 869,072
   screened approaches in the current run) and counted in `meta`. Most are
   artefacts of unmodelled station-keeping (docs/screening-validation.md) and the
   model was not trained on them.
5. **Scores are flagged when history is thin.** The update-history features need
   30 days of element sets before the snapshot. `meta.history_complete` is false
   below 25 days; the app says estimates are less reliable. Locally there is one
   snapshot, so every local run is flagged and scores run low (mean 0.27 for
   approaches under 2 km, against 0.41 observed in testing); the scheduled job
   with the archived snapshots will not have this gap.

## Consequences

- Deploying data and code stay separate (guide 13.3 option B is open for Phase 7:
  download the newest bundle at startup).
- Read endpoints measured p95 under 40 ms on the full bundle (92,492
  conjunctions, 20,087 objects), against a 200 ms target.
- A committed fixture bundle (1,798 objects, public CelesTrak data plus derived
  results) lets a clean checkout run the app without any download.
