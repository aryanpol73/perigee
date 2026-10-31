# Scheduled data job

`.github/workflows/data-refresh.yml` runs every hour on GitHub Actions and keeps a
dated archive of CelesTrak snapshots in a **private** Hugging Face dataset. The ML
stage needs weeks of history, and CelesTrak history cannot be downloaded later, so
this should run continuously from as early as possible.

## How a run works

1. Pull `state/` (fetch log, STOP file) and the newest raw snapshot of each source
   from the dataset (`python -m src.ops.archive_sync pull`).
2. Rebuild the processed tables offline. This gives the count-drop check its baseline.
3. `python -m src.main data`: download whatever is due. The fetch log enforces
   CelesTrak's intervals (GP 2 h, SATCAT 12 h, space weather 24 h), so hourly runs
   never poll more often than the policy allows, and cron drift never skips an update.
4. Push new raw snapshots, quarantined (rejected) downloads and `state/`
   (`python -m src.ops.archive_sync push`). Raw snapshots are immutable; push only adds.
5. The job fails (and GitHub emails you) if a download was rejected (exit 1) or
   CelesTrak refused a request (exit 2).

Processed Parquet tables are not stored; they are rebuilt from raw.

## One-time setup

1. **Create the dataset** on Hugging Face: <https://huggingface.co/new-dataset>,
   name it e.g. `perigee-snapshots`, and choose **Private**.
2. **Create a token**: <https://huggingface.co/settings/tokens>, type
   *Fine-grained*, with *write* access to that one dataset only.
3. **Add both to GitHub**: repo *Settings → Secrets and variables → Actions*.
   - *Secrets* tab, new repository secret `HF_TOKEN` = the token.
   - *Variables* tab, new repository variable `PERIGEE_HF_DATASET` =
     `<your-hf-username>/perigee-snapshots`.
4. **Seed it with the snapshots already on your machine** (optional but worth it:
   they are the oldest history you have). In PowerShell, from the project folder:

   ```powershell
   $env:HF_TOKEN = Read-Host -MaskInput "HF token"
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
| exit 2, "CelesTrak refused" | CelesTrak returned a non-200 (or an earlier refusal's STOP file exists). **No further requests are made.** | Read `state/STOP_CELESTRAK` in the dataset, check CelesTrak's [usage policy](https://celestrak.org/usage-policy.php) and the URL, then delete that file in the dataset to resume. |
| "Set secret HF_TOKEN..." | Setup incomplete | Steps 2-3 above. |
| "dataset ... is public" | The dataset's visibility is public | Make it private in the dataset settings. |

While the STOP file exists, every hourly run fails without contacting CelesTrak,
so you will get a failure email each hour until it is dealt with.

## Limits

- Public repositories get GitHub Actions minutes free; each run takes a minute or two.
- GitHub disables scheduled workflows in a public repo after 60 days without
  repository activity. Any commit or a manual run re-enables it.
- Storage grows by roughly 1 MB per GP snapshot (12 a day) plus SATCAT twice a day:
  on the order of 0.5-1 GB a year.
