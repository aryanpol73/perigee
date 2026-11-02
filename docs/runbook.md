# Runbook

Where things run: docs/deployment.md (a static site on Cloudflare Pages; computing in
GitHub Actions). Data job details: docs/data-job.md.

## Service objectives

| Objective | Target | Watched by |
|---|---|---|
| Site available | 99% | UptimeRobot (5 min) |
| Orbit snapshots | the hourly job succeeded in the last 12 h (GitHub runs it every 4-8 h in practice) | `watchdog` |
| Results the site serves | under 36 h old | `watchdog` |
| Page load | under 5 s on a typical mobile connection | Lighthouse, before releases |
| Simulator | first run of a visit within ~10 s on a laptop (Python loads), then about a second | `web/e2e` (static mode) |

## When something is wrong

| Symptom | First checks | Usual fix |
|---|---|---|
| Site down | Cloudflare status page; *Workers & Pages* → the project → *Deployments* | Roll back to the previous deployment (below) if the newest is broken. |
| Site loads but shows no data | Browser console, network tab: does `/data/index.json` load? | A deploy without data: run `publish`, or roll back. |
| "Data too old" on the site; `watchdog` red | *Actions*: the last `publish` and `data-refresh` runs | Re-run the failed workflow. A `data-refresh` exit 2 means CelesTrak refused a request: follow docs/data-job.md, never delete the STOP file without reading why it is there. |
| `publish` fails at *Screen* or runs out of time | Job log: step durations | Rerun once (runners vary). |
| `publish` or `deploy` fails at *Smoke test* | The step's error line | The deployment is live but incomplete: roll back, then fix and redeploy. |
| `deploy` warns "the site has no data yet" | | The first run: run `publish`. |
| `deploy` fails at *The data the live site serves now* | The step's error line | The live site's data could not be read (site down, or its format changed): nothing was deployed. Fix the cause, then re-run `deploy`. |
| Simulator says it could not start | Browser console | Old browser without WebAssembly support, or `/pyodide/` files missing from the deployment (`scripts/check-build.mjs --static` should have caught it). |
| Object count dropped sharply | `data-refresh` log: `rejected_by_reason` | The validator rejects a partial download and keeps the last good data; the next run retries. |
| Globe janky on a phone | Object count, filters, quality | The legend's *Quality* switch (battery saver); phones default to a smaller object set. |
| Scheduled workflows stopped running | *Actions*: a banner says the workflow is disabled | GitHub disables schedules in public repositories after 60 days without commits. Enable each workflow again (*Actions → workflow → Enable*) and push any commit. |

## Rollback

Cloudflare keeps every deployment, each with its code and its data.

- **Fastest:** *Workers & Pages* → `perigee` → *Deployments* → the last good one →
  *⋯* → *Rollback to this deployment*. Instant.
- **From GitHub:** *Actions → deploy → Run workflow*, `ref` = the last good commit. It
  rebuilds that code with the data the site serves now.

Then revert the bad commit on main with a pull request, so the next push does not
redeploy it. Pause `publish` (*Disable workflow*) if the problem is in the data.

**Practised:** record the date of the first rollback drill here once done.

## Rebuild drill

The raw archive is the source of truth; every table and result is rebuilt from it.

```bash
python -m src.ops.archive_sync pull --history-days 30 --models   # needs HF_TOKEN
python -m src.main rebuild
python -m src.main screen
python -m src.main shells
python -m src.main publish
python -m src.main export-static --out site-data
cd web && node scripts/build-site.mjs --data ../site-data          # the site, in web/dist
```

**Measured:** record the wall time of the first full rebuild here.

## Backups

- Raw archive: the private Hugging Face dataset is the primary copy; a laptop copy
  under `data/raw/` (pulled with `archive_sync pull --history-days 3650`) is the
  second. Space-Track yearly zips are kept as downloaded, never committed.
- Results and the site need no backup: they are rebuilt from raw, and Cloudflare keeps
  every past deployment.
