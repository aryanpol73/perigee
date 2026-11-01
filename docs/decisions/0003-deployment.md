# ADR 0003: Deployment: code and data ship separately; results download at startup

Status: accepted, 2026-11-01.

## Context

The guide (sections 12-14) asks for automatic deployment on a push to main, a
daily data refresh that needs no code release, free hosting, least-privilege
secrets, and a choice between three ways for the API to get fresh results
(13.3): baked into the image, downloaded at startup, or a persistent volume.

## Decisions

1. **Hosts.** The API runs as a Docker Space on Hugging Face (free CPU tier, port
   7860). The web app is static on Cloudflare Pages. Results and the raw archive
   live in Hugging Face datasets.
2. **Results download at startup (option B).** The image holds code only. On boot
   `python -m src.main api` downloads the bundle `LATEST.json` names from the
   public results dataset (`PERIGEE_RESULTS_REPO`, about 9 MB) over HTTPS without
   a token, then serves it from memory. If the download fails it serves the last
   bundle on disk, or 503s with a clear message. A data refresh is a restart
   (`publish.yml` calls `restart_space`), never a rebuild. Option A would rebuild
   the image every day; option C (a volume) is not free on Spaces.
3. **Two datasets, two tokens.** The raw archive (CelesTrak snapshots, models
   trained on Space-Track history) stays in a private dataset; the code refuses
   to use a public one. The results dataset is public and receives only the
   whitelisted bundle files (`src/results/remote.py`). `HF_TOKEN` can write only
   the private archive; `HF_DEPLOY_TOKEN` can write only the results dataset and
   the Space. The deploy job never sees `HF_TOKEN`.
4. **Code deploys after CI.** `deploy.yml` runs on a successful `ci` run for a
   push to main (never for forks), pushes `src/`, the Dockerfile and the Space card
   to the Space, waits until `/v1/health` reports the new `code_commit`, then
   builds the site with the API's address baked in and publishes it with
   Wrangler. Rolling back is the same workflow run by hand with an earlier commit.
5. **The orbit file stays on the API** (`/v1/orbits/<hash>`, immutable, cached for
   a year by browsers) rather than on Pages, so the site never needs a redeploy
   when data changes.

## Consequences

- Cold start after a Space sleeps: container start plus a ~9 MB download, measured
  in the runbook once deployed. The site says so if the first answer is slow.
- The API can serve data up to a day old by design; the watchdog alarms at 36 h.
- Every configuration value is a GitHub variable or secret; the Space's variables
  are overwritten from them on each deploy, so GitHub is the single source.
