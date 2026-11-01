# Runbook

Where things run: docs/deployment.md. Data job details: docs/data-job.md.

## Service objectives

| Objective | Target | Watched by |
|---|---|---|
| Site available | 99% | UptimeRobot (5 min) |
| API available while awake | 99%; a cold start after sleep is not an outage | UptimeRobot on `/v1/health` |
| Orbit snapshots | newest under 24 h old; hourly job succeeded in the last 6 h | `watchdog` |
| Conjunction run served | under 36 h old | `watchdog`, `/v1/health` (503 when stale) |
| API latency | p95 under 200 ms for read endpoints | docs/api-performance.md (measured locally) |
| Page load | under 5 s on a typical mobile connection | Lighthouse, before releases |

## When something is wrong

| Symptom | First checks | Usual fix |
|---|---|---|
| Site loads but shows no data | `/v1/health` in a browser; the console's network tab | Space asleep: wait a minute (the site says so). A CORS error: `PERIGEE_WEB_URL` must equal the site's origin exactly, then re-run `deploy`. |
| "Data too old" on the site; `watchdog` red | *Actions*: the last `publish` and `data-refresh` runs | Re-run the failed workflow. A `data-refresh` exit 2 means CelesTrak refused a request: follow docs/data-job.md, never delete the STOP file without reading why it is there. |
| `publish` fails at *Screen* or runs out of time | Job log: step durations and memory | Rerun once (runners vary). If it repeats, screen fewer days (`--days 5`) and record it. |
| `publish` fails at *Check the API serves the new run* | The Space's *Logs* tab | The Space failed to download the bundle (Hub outage) or crashed. Restart it from the Space settings; it serves the last bundle it has. |
| `deploy` fails waiting for the new build | The Space's *Build* logs | A dependency did not install, or the image failed its health check. Fix, push; or roll back (below). |
| Object count dropped sharply | `data-refresh` log: `rejected_by_reason` | The validator rejects a partial download and keeps the last good data; the next run retries. |
| API crashes at startup | Space logs; memory (free CPU tier: 16 GB) | The bundle is about 9 MB, far below the limit, so suspect a bad bundle: point `LATEST.json` at the previous run (*Bad results* below). |
| Globe janky on a phone | Object count, filters, quality | The legend's *Quality* switch (battery saver); phones default to a smaller object set. |
| Scheduled workflows stopped running | *Actions*: a banner says the workflow is disabled | GitHub disables schedules in public repositories after 60 days without commits. Enable each workflow again (*Actions → workflow → Enable*) and push any commit. |

## Rollback

**Code.** *Actions → deploy → Run workflow*, `ref` = the last good commit (or tag).
It redeploys that commit's API to the Space and that commit's site to Pages, with
the same smoke tests. Then revert the bad commit on main with a pull request, so
the next push does not redeploy it.

**Bad results.** Results are kept for the last 7 runs in the results dataset. To
serve an earlier one: in the dataset's *Files* tab, edit `LATEST.json` to the
earlier run's folder name (`{"run_id": "...", "path": "..."}`), then restart the
Space. The next daily `publish` replaces it again; pause `publish` (*Disable
workflow*) if the problem is in the pipeline.

**Practised:** record the date of the first rollback drill here once done.

## Rebuild drill

The raw archive is the source of truth; every table and result is rebuilt from it.

```bash
python -m src.ops.archive_sync pull --history-days 30 --models   # needs HF_TOKEN
python -m src.main rebuild
python -m src.main screen
python -m src.main shells
python -m src.main publish
```

**Measured:** record the wall time of the first full rebuild here.

## Backups

- Raw archive: the private Hugging Face dataset is the primary copy; a laptop copy
  under `data/raw/` (pulled with `archive_sync pull --history-days 3650`) is the
  second. Space-Track yearly zips are kept as downloaded, never committed.
- Results and processed tables need no backup: they are rebuilt from raw.
