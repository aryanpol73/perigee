# Changelog

Phase gates follow the build guide. Versions are tagged on main once each phase's
"Done when" is met (v1.0 at launch).

## Unreleased: Phase 7, production hardening and deployment

- CI on every push and pull request: ruff, mypy, pytest with an 80% coverage gate,
  tsc, eslint, Vitest, the production build (with a check of Cesium's file layout),
  Playwright browser tests, the API image built and started, pip-audit, npm audit,
  gitleaks. Third-party Actions pinned to commit hashes; Dependabot for pip, npm,
  Actions and the base image.
- API container (slim, non-root, pinned by digest, health check) deployed to a
  Hugging Face Space after CI passes on main; the site to Cloudflare Pages with a
  content security policy and cache headers. Smoke tests check the deployed
  commit, CORS and the headers. Rollback by redeploying an earlier commit.
- Daily `publish` job: a month of snapshots and the model from the private
  archive, screen, score, shell index, results to a public dataset, API restart.
  `watchdog` every 3 h for freshness and stopped jobs. `/v1/health` reports the
  deployed `code_commit`.
- Optional Sentry error tracking for the API and the site.
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
