# Scheduled data job

The data job keeps a dated archive of CelesTrak snapshots in a **private** Hugging
Face dataset. The ML stage needs weeks of history, and CelesTrak history cannot be
downloaded later, so it should run continuously.

It runs **hourly on GitHub Actions** (`.github/workflows/data-refresh.yml`). The
owner's PC job (`scripts/local_fetch.ps1`, below) is the fallback. **Never run both**:
they share one fetch log and STOP file.

### Shared runner IPs and CelesTrak's "not updated" 403

CelesTrak serves each GP update once per IP and answers a second download of the
same data with **403** "GP data has not updated since your last successful
download"; the 403 clears when the data next updates (every 2 hours). GitHub's
runners share IPs with other projects, so in November 2026 someone else's download
made ours look like a repeat and the job stopped itself, twice.

That one 403 is now not a refusal: the job skips GP for that run (no snapshot, no
STOP file, exit 0) and asks again only after GP's interval, the next update. After
`PERIGEE_MAX_NOT_UPDATED` (3) in a row, about 6 hours without new GP data, it writes a
STOP file saying so: then run the data job from another IP (the PC fallback). Every
other non-200 answer still stops everything at once. The count is in
`state/not_updated.json`; it is far below CelesTrak's firewall threshold (about 50
errors per IP in 2 hours): at most one GP request per 2 hours.

### CelesTrak outages (502/503/504)

On 25 November 2026 CelesTrak's server was down for a while and answered a plain
**503 Service Unavailable**. The job used to treat that as a refusal: a STOP file,
and every run failed for days until a human deleted it. A server outage is now
handled on its own: that run makes no further request (status `unavailable`,
exit 0), and no request at all is made for `PERIGEE_OUTAGE_COOLDOWN_MIN` (60)
minutes (the policy asks for at least 15 after a 503). The source that got the 503
still counts against its interval. Only after `PERIGEE_MAX_OUTAGES` (6) outages in a
row does the job write a STOP file ("6 outages in a row"); then check that
celestrak.org is up and delete it. The count is in `state/outage.json`; any
successful download clears it. A STOP file or cool-down no longer uses up a source's
interval in the fetch log.

## The PC fallback

Use it when GitHub's IPs keep getting the "not updated" 403 (a STOP file says so).
First disable the schedule of `data-refresh.yml` (or the workflow: *Actions →
data-refresh → ⋯ → Disable workflow*), then on the PC:

Set up once (needs the project's `.venv`), in PowerShell from the project folder:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_local_fetch.ps1
```

It asks for the dataset name and the Hugging Face token (typed hidden; stored
encrypted with Windows DPAPI in `%LOCALAPPDATA%\Perigee\hf_token.dat`, readable only
by your Windows account), then registers the task **Perigee data fetch**: hourly at
:17 while you are signed in, and as soon as possible after a missed run. If the PC is
off, nothing is lost but freshness: the next run fetches what is due.

- Run now: `Start-ScheduledTask -TaskName "Perigee data fetch"`
- Result of the last run: `%LOCALAPPDATA%\Perigee\last-status.json`; logs (30 days):
  `%LOCALAPPDATA%\Perigee\logs`. A failure also shows a Windows notification, once.
- New token: `... install_local_fetch.ps1 -NewToken`; remove everything: `-Uninstall`.

Each run works in a fresh `%LOCALAPPDATA%\Perigeeetch` folder (outside OneDrive),
so the dataset is the only state, exactly as on a CI runner.

## How a run works

1. Pull `state/` (fetch log, STOP file) and the newest raw snapshot of each source
   from the dataset (`python -m src.ops.archive_sync pull`).
2. Rebuild the processed tables offline. This gives the count-drop check its baseline.
3. `python -m src.main data`: download whatever is due. The fetch log enforces
   CelesTrak's intervals (GP 2 h, SATCAT 12 h, space weather 24 h), so hourly runs
   never poll more often than the policy allows, and cron drift never skips an update.
4. Push new raw snapshots, quarantined (rejected) downloads and `state/`
   (`python -m src.ops.archive_sync push`). Raw snapshots are immutable; push only adds.
5. The run fails if a download was rejected (exit 1) or CelesTrak refused a request
   (exit 2). On the PC it shows a notification; the watchdog workflow flags the site's
   data going stale (over 36 h) by email.

Processed Parquet tables are not stored; they are rebuilt from raw.

## One-time setup

1. **Create the dataset** on Hugging Face: <https://huggingface.co/new-dataset>,
   name it e.g. `perigee-snapshots`, and choose **Private**.
2. **Create a token**: <https://huggingface.co/settings/tokens>, type
   *Fine-grained*, with *write* access to that one dataset only.
3. **Add both to GitHub**: repo *Settings → Secrets and variables → Actions*.
   - *Secrets* tab, new repository secret `HF_TOKEN` = the token.
   - *Variables* tab, new repository variable `PERIGEE_HF_DATASET` =
     `<your-hf-username>/perigee-snapshots`. (A secret with that name works too.)
4. **Seed it with the snapshots already on your machine** (optional but worth it:
   they are the oldest history you have). In PowerShell, from the project folder:

   ```powershell
   $s = Read-Host -AsSecureString "HF token"   # typed hidden, works in PowerShell 5.1 and 7
   $env:HF_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
       [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
   $env:PERIGEE_HF_DATASET = "<your-hf-username>/perigee-snapshots"
   .\.venv\Scripts\python.exe -m pip install -r requirements-jobs.txt
   .\.venv\Scripts\python.exe -m src.ops.archive_sync push
   ```

   This also uploads your local fetch log, so the first scheduled run does not
   re-download anything fetched in the last few hours.
5. **Start it**: the workflow must be on `main` (scheduled workflows only run from
   the default branch). Then *Actions → data-refresh → Run workflow* once to check it.

The job refuses to upload if the dataset is public.

## When it fails

| Job error | Meaning | What to do |
|---|---|---|
| exit 1, "download was rejected" | A file failed validation; it is in `quarantine/` in the dataset and the last good data is kept | Look at the log's `rejected_by_reason`. Usually transient; the next due run retries. |
| `unavailable` / `skipped` (exit 0) | CelesTrak was down (502/503/504), or a cool-down after that is running | Nothing: the job resumes by itself. |
| exit 2, "CelesTrak refused" | CelesTrak returned a non-200 other than an outage, or 6 outages in a row (or an earlier refusal's STOP file exists). **No further requests are made.** | Read `state/STOP_CELESTRAK` in the dataset, check CelesTrak's [usage policy](https://celestrak.org/usage-policy.php) and the URL, then delete that file in the dataset to resume. |
| "Set secret HF_TOKEN..." | Setup incomplete | Steps 2-3 above. |
| "dataset ... is public" | The dataset's visibility is public | Make it private in the dataset settings. |

While the STOP file exists, every hourly run fails without contacting CelesTrak.
The STOP file records the URL, the status and the start of CelesTrak's message, which
says why (for a 403, usually "GP data has not updated since your last successful
download": someone on the same IP downloaded it first).

## Limits

- Public repositories get GitHub Actions minutes free; each run takes a minute or two.
- GitHub disables scheduled workflows in a public repo after 60 days without
  repository activity. Any commit or a manual run re-enables it.
- Storage grows by roughly 1 MB per GP snapshot (12 a day) plus SATCAT twice a day:
  on the order of 0.5-1 GB a year.
