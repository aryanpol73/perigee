# ADR 0003: Deployment: a static site; computing happens in GitHub Actions and the browser

Status: accepted, 2026-11-02. Supersedes the first version of this ADR (2026-11-01),
which put the API on a Hugging Face Docker Space and then on Render.

## Context

The guide (sections 12-14) asks for automatic deployment on a push to main, a daily
data refresh that needs no code release, free hosting and least-privilege secrets.
Free API hosts turned out poor: Hugging Face Docker Spaces now need a paid plan; Google
Cloud Run and Oracle need a payment method; Render's free plan has about a tenth of a
CPU, sleeps after 15 minutes and takes a minute to wake, which would push the
simulator (1.8 s on a laptop) past its 15 s limit.

What the API does at request time is small: it serves results computed once a day,
filters and sorts them, and runs the breakup simulator.

## Decisions

1. **No server in production.** The daily job exports the results bundle as static
   files (`python -m src.main export-static`, `src/results/static_site.py`), and the
   site and its data are one Cloudflare Pages deployment (free, no card, unlimited
   bandwidth, a global CDN, nothing to sleep).
2. **The browser answers the API's questions.** `web/src/static-api.ts` gives every
   GET the API's body, filtering, sorting and paging in the browser with the API's
   rules. The values come from the same Python builders as the API
   (`src/api/views.py`); a parity test replays 345 recorded API answers against the
   static site (`tests/test_static_site.py`, `web/src/static-api.test.ts`).
3. **The simulator runs in the browser** with Pyodide, executing the same Python files
   as the API (`src/sim/service.py`). Measured: 0.9 s per simulation once loaded (1.8 s
   natively), with identical results (`web/src/sim-python.test.ts` compares three
   simulations with the API to 1e-9). Pyodide and its numpy and pandas (about 13 MB
   compressed, checksummed against Pyodide's lock file) are served by the site itself
   and load only when the simulator page opens.
4. **Two kinds of deploy.** `publish.yml` computes and publishes data with the current
   code; `deploy.yml` publishes new code with the data the live site already serves
   (`src/results/site_fetch.py`). Both build with `web/scripts/build-site.mjs` and are smoke
   tested (`scripts/smoke_site.sh`). Cloudflare keeps every deployment, so rolling
   back code or data is one click.
5. **Secrets.** `HF_TOKEN` (private archive: raw data and models) never reaches a
   deploy step; the deploy jobs hold only the Cloudflare token. Nothing derived from
   Space-Track goes public except the derived results, as before.
6. **The API stays** for local development (`python -m src.main dev`) and as a
   self-hostable container (`Dockerfile`, built and started in CI).

## Consequences

- Faster than any free API host: data comes from the CDN, filters apply instantly,
  there is no cold start. The first simulation of a visit waits for Python to load
  (a few seconds; it starts loading when the simulator page opens).
- The close-approach list downloads once per visit (about 2.8 MB compressed for 92,000
  approaches) instead of a page at a time.
- Any change to an API route must be mirrored in `static-api.ts`; the parity test
  fails until it is.
