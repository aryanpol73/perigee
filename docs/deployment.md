# Deploying Perigee

Four workflows run everything once the setup below is done:

| Workflow | When | What |
|---|---|---|
| `ci.yml` | every push and pull request | lint, types, tests (coverage gate 80%), web build and browser tests, the API image built and started, dependency audits, secret scan |
| `deploy.yml` | after `ci` passes on main (or by hand, for rollback) | API code to the Hugging Face Space, web app to Cloudflare Pages, smoke tests |
| `data-refresh.yml` | hourly | CelesTrak snapshots into the private archive (docs/data-job.md) |
| `publish.yml` | daily 04:41 UTC | rebuild, screen, score, publish results, restart the API |
| `watchdog.yml` | every 3 h | site and API up, results under 36 h old, scheduled jobs still succeeding |

Why it is built this way: docs/decisions/0003-deployment.md. When something
breaks: docs/runbook.md.

## One-time setup

Never paste a token into a chat, an issue or a committed file. Tokens go only into
GitHub's secret store (and, for local one-off commands, a hidden prompt in your shell).

### 1. Hugging Face

The hourly data job (docs/data-job.md) already uses a **private** dataset (e.g.
`perigee-snapshots`) and a token `HF_TOKEN` that can write only that dataset. Add:

1. **A public dataset for results**: <https://huggingface.co/new-dataset>, name
   `perigee-results`, **Public**. Only derived results are ever uploaded to it.
2. **A Docker Space for the API**: <https://huggingface.co/new-space>, name
   `perigee-api`, SDK **Docker**, template *Blank*, hardware *CPU basic (free)*.
   Its URL is `https://<user>-perigee-api.hf.space`.
3. **A second fine-grained token** (`HF_DEPLOY_TOKEN`):
   <https://huggingface.co/settings/tokens>, with **write** access to exactly the
   `perigee-results` dataset and the `perigee-api` Space, nothing else.
4. **Upload the trained model to the private archive** (once, from your machine;
   the daily job pulls it from there). In PowerShell from the project folder:

   ```powershell
   $s = Read-Host -AsSecureString "HF token for the PRIVATE archive"
   $env:HF_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
       [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
   $env:PERIGEE_HF_DATASET = "<user>/perigee-snapshots"
   .\.venv\Scripts\python.exe -m src.ops.archive_sync push-models
   ```

### 2. Cloudflare Pages

1. <https://dash.cloudflare.com> → *Workers & Pages* → *Create* → *Pages* →
   *Upload assets* (Direct Upload, not Git), project name `perigee`. Upload any
   placeholder file once; the workflow publishes the real site. The URL is
   `https://perigee.pages.dev`.
2. An **API token**: *My Profile* → *API Tokens* → *Create token* → template
   *Edit Cloudflare Workers*, or a custom token with **Account → Cloudflare Pages →
   Edit** only.
3. Your **account ID**: shown on the *Workers & Pages* overview page.

### 3. Sentry (optional, free tier)

Create two projects at <https://sentry.io>: *Python (FastAPI)* for the API and
*Browser JavaScript* for the site. Copy each project's DSN. A DSN only allows
sending events, so it is not a real secret, but keep it out of the repository.

### 4. GitHub: secrets and variables

Repository *Settings → Secrets and variables → Actions*.

**Secrets**

| Name | Value |
|---|---|
| `HF_TOKEN` | already set for the data job: writes the private archive only |
| `HF_DEPLOY_TOKEN` | the token from step 1.3 |
| `CLOUDFLARE_API_TOKEN` | the token from step 2.2 |
| `CLOUDFLARE_ACCOUNT_ID` | from step 2.3 |
| `SENTRY_DSN` | optional: the API project's DSN |

**Variables**

| Name | Example |
|---|---|
| `PERIGEE_HF_DATASET` | `<user>/perigee-snapshots` (already set) |
| `PERIGEE_RESULTS_REPO` | `<user>/perigee-results` |
| `PERIGEE_HF_SPACE` | `<user>/perigee-api` |
| `PERIGEE_API_URL` | `https://<user>-perigee-api.hf.space` |
| `PERIGEE_WEB_URL` | `https://perigee.pages.dev` |
| `CF_PAGES_PROJECT` | `perigee` |
| `SENTRY_DSN_WEB` | optional: the browser project's DSN |

Then *Settings → Environments → New environment* `production` (the deploy jobs
use it). Optionally add yourself as a required reviewer there to approve each deploy.

### 5. Protect main

*Settings → Branches → Add branch ruleset* for `main`: require a pull request,
require the status checks `python`, `web`, `docker` and `security` to pass, block
force pushes.

### 6. First run, in this order

1. Merge the Phase 7 pull request. `ci` runs and `deploy` follows. The first time
   it ends with a warning that the API has no results yet: nothing is published.
2. *Actions → publish → Run workflow*. It builds and uploads the first results
   bundle and restarts the Space (about 15-25 minutes).
3. *Actions → deploy → Run workflow* with `ref` = `main`. Both jobs should end
   green: the API reports the deployed commit, the site answers with its headers.
4. Open the site.

### 7. Uptime monitoring

At <https://uptimerobot.com> (free): two HTTP(s) monitors at 5-minute intervals:

- `https://<user>-perigee-api.hf.space/v1/health` (it returns 503 when data is
  stale, so a stale API shows as down);
- `https://perigee.pages.dev`.

Add your email as the alert contact and use *Send test notification* once. A
5-minute ping also keeps the free Space from sleeping.

## Phase 7 is done when

- a push to main deploys automatically (step 6 shows it);
- `data-refresh` and `publish` run unattended for three consecutive days
  (`watchdog` stays green);
- the uptime monitor is green.
