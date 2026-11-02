# Changelog

Phase gates follow the build guide. Versions are tagged on main once each phase's
"Done when" is met (v1.0 at launch).

## Unreleased: Phase 7, production hardening and deployment

- CI on every push and pull request: ruff, mypy, pytest with an 80% coverage gate,
  tsc, eslint, Vitest, the production build (with a check of Cesium's file layout),
  Playwright browser tests, the API image built and started, pip-audit, npm audit,
  gitleaks. Third-party Actions pinned to commit hashes; Dependabot for pip, npm,
  Actions and the base image.
- Production is one static site on Cloudflare Pages, no server (ADR 0003): the daily
  job exports the results as data files (`export-static`), the browser answers every
  API query from them with the API's rules, and the breakup simulator runs in the
  browser with Pyodide, executing the same Python as the API. Parity tests replay
  345 recorded API answers and three simulations against the static site; the
  browser tests also run on the production build, served with its real headers.
- `deploy` (after CI on main: new code, current data) and `publish` (daily: new
  data) both build the site with `web/scripts/build-site.mjs` and smoke-test it.
  Rollback in one click from Cloudflare's deployment history. `watchdog` every 3 h
  for site, data freshness and stopped jobs.
- The API (now built from `src/api/views.py`, shared with the export) stays for
  local development and as a self-hostable container (slim, non-root, pinned by
  digest, health check, built and started in CI).
- Optional Sentry error tracking for the site.
- Fixed: object details failed (500) for objects without a SATCAT record.
- Fixed: production builds put Cesium's files under `cesium/node_modules/...`, so
  a deployed globe could not start. The app's health check ignored `VITE_API_BASE`.
- Docs: deployment setup, runbook, threat model, ADR 0003. MIT licence.

## Web: live clock and visuals (PRs #13-#15)

- LIVE / FORECAST / DATA TOO OLD clock, smooth 60 fps motion, NASA Blue Marble and
  Black Marble imagery, quality setting, measured forecast uncertainty per orbit
  class, full-screen globe.

## Phase 6: API and web app (PRs #10-#12)

- Read-only FastAPI over a published results bundle; React + Cesium app; one-command
  local run. Review fixes: provisional estimates flagged, browser tests, load fixes.

## Phase 5: shell risk index and breakup simulator (PR #9)

## Phase 4: ML false-alarm model on 2025 Space-Track history (PRs #7-#8)

## Phases 0-3: data pipeline, propagation, screening (PRs up to #6)
