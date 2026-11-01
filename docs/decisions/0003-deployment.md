# ADR 0003: Deployment: code and data ship separately; results download at startup

Status: accepted, 2026-11-01. Revised the same day: the API moved from a Hugging Face
Docker Space to Render, because Docker Spaces now need a paid plan.

## Context

The guide (sections 12-14) asks for automatic deployment on a push to main, a daily
data refresh that needs no code release, free hosting, least-privilege secrets, and a
choice between three ways for the API to get fresh results (13.3): baked into the
image, downloaded at startup, or a persistent volume.

## Decisions

1. **Hosts.** The API is a free Render web service built from the `Dockerfile`
   (`render.yaml`; 512 MB, the API peaks at about 270 MB with the full catalog and
   simulator calls). The site is static on Cloudflare Pages. Results and the raw
   archive live in Hugging Face datasets (free). Hugging Face Docker Spaces were the
   first choice but now require PRO; Google Cloud Run needs a billing account.
2. **Results download at startup (option B).** The image holds code only. On boot
   `python -m src.main api` downloads the bundle `LATEST.json` names from the public
   results dataset (`PERIGEE_RESULTS_REPO`, about 9 MB) over HTTPS without a token and
   serves it from memory. If the download fails it serves the last bundle on disk, or
   503s with a clear message. A data refresh is a redeploy of the code already running
   (`publish.yml` calls the deploy hook with that commit), never a code change.
   Option A would mean a new image per data change; option C is not free on Render.
3. **Two datasets, two tokens.** The raw archive (CelesTrak snapshots, models trained
   on Space-Track history) stays private; the code refuses a public one. The results
   dataset is public and receives only the whitelisted bundle files
   (`src/results/remote.py`). `HF_TOKEN` writes only the archive; `HF_DEPLOY_TOKEN`
   writes only the results dataset. The deploy job holds neither, only the Render
   deploy hook.
4. **Code deploys after CI.** Render's own auto-deploy is off. `deploy.yml` runs on a
   successful `ci` run for a push to main (never for forks), calls the deploy hook
   with `ref=<commit>`, waits until `/v1/health` reports that `code_commit` (Render's
   `RENDER_GIT_COMMIT`), checks CORS, then builds the site with the API's address
   baked in and publishes it with Wrangler. Rolling back is the same workflow run by
   hand with an earlier commit.
5. **The orbit file stays on the API** (`/v1/orbits/<hash>`, immutable, cached for a
   year by browsers), so the site never needs a redeploy when data changes.

## Consequences

- A free Render service sleeps after 15 idle minutes and takes about a minute to wake;
  the uptime monitor's 5-minute ping keeps it awake (about 744 of 750 free hours a
  month). The site says so if the first answer is slow.
- The API can serve data up to a day old by design; the watchdog alarms at 36 h.
- Render's environment variables (`PERIGEE_RESULTS_REPO`, `PERIGEE_API_CORS_ORIGINS`,
  `SENTRY_DSN`) are set once in the Render dashboard; everything else lives in GitHub.
