# Project: Perigee (Orbital Debris Risk Tracker)

## 1. Project Summary
Perigee is a web app that ingests public orbit data for ~30,000 space objects to calculate and visualize orbital risks. It performs four main functions:
1. 3D real-time visualization of objects.
2. Conjunction screening (finding upcoming close approaches).
3. ML risk assessment to classify if flagged approaches are likely dangerous or just false alarms caused by public TLE inaccuracy.
4. Shell risk scoring for 25 km altitude bands and collision simulation.

## 2. Tech Stack
- **Backend:** Python 3.11+, FastAPI
- **Data:** DuckDB/SQLite, Parquet, pandas, numpy
- **Math/Algorithms:** sgp4 (Python), scipy.spatial.cKDTree
- **ML:** scikit-learn, XGBoost/LightGBM
- **Frontend:** HTML/JS, satellite.js, CesiumJS (3D globe)
- **Infra:** GitHub Actions (CRON data jobs)

## 3. Engineering Constraints & Rules
- **Resource Limits:** The app will run on free tiers. Optimize memory heavily. Do not stream 30k positions from the server; let client-side `satellite.js` handle high-frequency positional math.
- **Data Etiquette:** Do not aggressively poll CelesTrak. Cache data locally in Parquet/DuckDB.
- **ML Integrity:** Time-based splits for train/test are mandatory to prevent data leakage. Use PR-AUC for heavily imbalanced conjunction data.
- **Honest Framing:** Never use "collision prediction." Always use "risk index" or "estimated close approach based on public data."

## 4. Execution Rules for Claude Code
- Follow the user's specific Phase prompts exactly.
- Complete and commit the current phase's "Done when" criteria before moving on.
- Write tests alongside each module (e.g., `test_data.py`) to prevent costly late-stage debugging.
- Keep data fetching, orbital physics, ML, and API routing in separate modules.

## 5. Local Setup
- Virtual env: `.venv` (Python 3.12). Activate with `.venv\Scripts\activate` (Windows).
- Install: `pip install -r requirements-dev.txt` (includes `requirements-ml.txt` and `requirements.txt`; the hourly job installs only `requirements-jobs.txt`)
- Test: `python -m pytest`
- Dependencies are pinned in `requirements.txt`. Note: DuckDB 2.0 relations have no `fetchone()`; use `fetchall()` or `.limit(1).fetchall()`.
- Data job: `python -m src.main data` (or `make data`; `make` is not installed on the Windows dev machine). `--offline` rebuilds from raw without downloading.
- Parquet is read and written through DuckDB (no pyarrow). Open connections with `src.storage.parquet_store.connect()`, which sets `TimeZone = 'UTC'`.
- Parse ISO timestamps with `format="ISO8601"`: pandas 3 otherwise infers one format from the first value.

## 6. Code Layout
- `src/config.py` env-var settings (all listed in `.env.example`); `src/log.py` JSON logs with run id.
- `src/ingest/` fetching only: `sources.py`, `download.py` (polite client, STOP file, fetch log), `archive.py` (immutable raw), `parse.py`, `validate.py`.
- `src/orbits/` orbital physics: `elements.py` (Kepler quantities), `propagate.py` (SGP4 over the catalog via `SatrecArray`; failures flagged with error codes, never dropped), `frames.py` (TEME -> ECEF -> WGS84), `reference.py` (NASA ISS OEM reader), `satjs_bridge.py` (runs satellite.js for parity checks).
- `src/storage/` Parquet + DuckDB queries. `src/pipeline.py` orchestrates; each catalog snapshot carries `sgp4_error` (0 = propagates at snapshot time). Filter on it downstream.
- `scripts/validate_propagation.py` (needs `requirements-dev.txt` + `npm install` in `scripts/satjs`) writes `docs/validation.md`; `scripts/altitude_chart.py` writes `docs/figures/`.
- satellite.js 7 reads OMM `EPOCH` with millisecond precision only (a few metres of difference vs Python at LEO speed). That is expected, not a bug.
- astropy cannot load from very long Windows paths (MAX_PATH); keep the checkout at a short path.
- `src/screening/`: `screen.py` (KD-tree sieve at 20 s steps with a guaranteed radius, straight-line filter, Newton TCA refinement, parallel chunks), `run.py` (`python -m src.main screen`, writes `data/processed/conjunctions/<snapshot>_<scope>.parquet` + `.json` summary), `socrates.py` (comparison with CelesTrak SOCRATES). `scripts/validate_screening.py` writes `docs/screening-validation.md`.
- Screening facts: identical-input recall vs SOCRATES is 100%; disagreements are missing objects, data age, and SOCRATES omitting most same-constellation pairs. Starlink-Starlink approaches grow ~3x over 7 days (unmodelled station-keeping), so treat days 4-7 of intra-constellation lists as artefacts. Co-located pairs (<10 m/s) are one row per pair.
- `src/storage/elsets.py`: element-set history with `available_at` (point in time; DuckDB). `src/ml/`: `dataset.py` (screen at T, label from first set after TCA), `features.py` (T-only data), `train.py` (time split + embargo, baselines, LightGBM/Optuna, isotonic, pair bootstrap), `pipeline.py` + `model_card.py`; `python -m src.main train` (exit 3 = not enough history). Decisions in `docs/decisions/0001-*.md`.
- ML facts (2025 Space-Track history, weekly T): ~1M labelled rows, 15.7% positive at 5 km. LightGBM test PR-AUC ~0.70 vs 0.39 best baseline; radial miss dominates (newer data mostly moves the TCA in-track). A full `train` takes ~2 h dataset + ~40 min training; `train --reuse` skips the dataset. Follow any JSON log live with `scripts/watch_progress.ps1 <log>`.
- `src/ingest/spacetrack_import.py`: imports Space-Track yearly TLE zips downloaded by hand (`import-spacetrack`). Never call Space-Track gp_history from code (1/lifetime rule).
- `src/orbits/kepler.py`: two-body + secular J2, vectorised (Kepler solver, state <-> elements). `src/risk/shells.py`: shell index (time-in-band sampling, sampled relative speed, SATCAT size classes; unknown PAY/R/B = LARGE, DEB = SMALL), `run.py` (`python -m src.main shells [--at DATE]`, writes `data/processed/shell_risk/`). `src/sim/breakup.py`: NASA breakup model collisions (coefficients from ESA's NASA-breakup-model-cpp; its rocket-body sigma_2 ramp sign is a typo), `python -m src.main simulate`. `scripts/validate_risk.py` writes `docs/risk-validation.md` + figures (~3 min, needs the Space-Track history).
- Risk facts: n^2 makes the index dominated by the constellation shells; use a log scale. Near-polar debris planes precess slowly (J2 ~ cos i), so a 'shell' takes months to years; the demo uses 74 deg. Starlink moved many satellites from ~550 to ~480 km in 2026, so the live top band is 450-500 km.
- `src/ml/score.py` scores live approaches (<= 72 h, cross-constellation, not co-located) with the newest model in `data/models/`, using the training feature code; `src/results/bundle.py` (`python -m src.main publish`) builds the results bundle (meta, objects, conjunctions, shells, model.json, `orbits.<hash>.json.gz`), validates it and switches `data/results/LATEST.json` atomically. `--fixture` rewrites the committed fixture in `tests/fixtures/results` (public CelesTrak data + derived results only; its Parquet is un-ignored in .gitignore).
- `src/api/` read-only FastAPI (`python -m src.main api`): loads the bundle into memory at startup; every route under /v1, typed schemas in `schemas.py`, one error shape, ETag, gzip, CORS from `PERIGEE_API_CORS_ORIGINS`, per-IP rate limit. `tests/fixtures/openapi.json` is a schema snapshot: after an intended API change run `UPDATE_OPENAPI_SNAPSHOT=1 python -m pytest tests/test_api.py` and `npm run gen:api` in `web/`. Decisions in `docs/decisions/0002-*.md`.
- `web/`: React + TypeScript + Vite 8 + CesiumJS (no ion token: bundled Natural Earth imagery) + satellite.js in a Web Worker (`src/orbit.worker.ts`), point primitives, request-render mode. `python -m src.main dev` runs API + web (fixture bundle if nothing is published). In dev Cesium is served from node_modules; builds copy it to /cesium. Workers build as ES modules. Same-URL hash navigation does not reload the page: use `location.reload()` when checking a change in the browser.
- Web clock (`web/src/clock.ts`): LIVE follows the real time; FORECAST is any moment in the snapshot's 7-day window at any speed; DATA TOO OLD when the real time is past it. The store keeps an anchor, not a ticking time. `?now=<ISO>` pins the real time (e2e tests use it). The slider marks now (blue = calculated up to now, amber = forecast, faded after day 3) and the badge shows the measured median LEO error for the shown lead (`web/src/uncertainty.ts`, from `scripts/forecast_error.py` -> docs/forecast-uncertainty.md: ~1.6 km at 1 day, ~17 km at 7 days, p90 ~480 km). Smooth motion (`web/src/motion.ts`): a pool of up to 4 workers (one catalog slice each; `?workers=N` overrides) samples ECEF position + velocity; each frame Hermite-interpolates every point into reused objects (~1.5 ms for 20k points, 60 fps at 600x); beyond 60 s from a sample points hold instead of extrapolating (seeks). Type-check with `tsc --noEmit` (`npm run typecheck`): `tsc -b` writes .js files next to the sources, which Vite then serves instead of the .tsx. Earth: NASA GIBS Blue Marble + Black Marble (Web Mercator, levels 0-8, CORS *) over bundled Natural Earth, real sunlight from the shown time; quality high/balanced/battery (bloom, resolution, 30 fps). `dev --api-port/--web-port` runs a second copy beside one already on 8000/5173.
- Live scores are provisional (`history_complete: false`, `estimate_status: provisional`) below 14 days of snapshots; with no history they run at about half the observed rate (docs/history-sensitivity.md). Publishing in CI must run `python -m src.ops.archive_sync pull --history-days 30` first; plain `pull` restores only the newest snapshot.
- API performance: `scripts/load_test.py` (run its readers in several processes; one Python client process saturates first). Results in docs/api-performance.md. Browser tests: `npm run e2e` in `web/` (Playwright, installed Chrome; PW_CHANNEL="" + `npx playwright install chromium` in CI). Dependency advisories reviewed in docs/security.md.
- `src/ops/archive_sync.py` + `.github/workflows/data-refresh.yml`: hourly job that pulls state + latest raw from a private Hugging Face dataset, runs the pipeline, pushes new snapshots (`docs/data-job.md`). Deps in `requirements-jobs.txt`. It must refuse public datasets.
- CelesTrak rules are in `docs/data-sources.md`. Never delete `data/state/STOP_CELESTRAK` or bypass the fetch log without the user's say-so.
