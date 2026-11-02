# Deploying Perigee

Production is one static site on Cloudflare Pages: the app, its data files and the
simulator, which runs in the visitor's browser (Python compiled to WebAssembly).
There is no server to host, wake or scale. GitHub Actions does all the computing.

| Workflow | When | What |
|---|---|---|
| `ci.yml` | every push and pull request | lint, types, tests (coverage gate 80%), parity of the static site with the API, browser tests on the dev app **and on the production static build**, the API image built and started, dependency audits, secret scan |
| `deploy.yml` | after `ci` passes on main (or by hand, for rollback) | rebuilds the site with the new code and the data the live site serves, publishes it, smoke test |
| `data-refresh.yml` | hourly (GitHub runs it every 4-8 h) | CelesTrak snapshots into the private archive (docs/data-job.md) |
| `publish.yml` | daily 04:41 UTC | rebuild, screen, score, risk index, export the data, publish the site with it, smoke test |
| `watchdog.yml` | every 3 h | site up, its results under 36 h old, scheduled jobs still succeeding |
| `android.yml` | by hand | the Android app: build, sign, check against the site's asset links, GitHub release (docs/mobile-app.md) |

Why it is built this way: docs/decisions/0003-deployment.md. When something
breaks: docs/runbook.md. The FastAPI server (`python -m src.main api`, the
`Dockerfile`) is still the local development backend and can be self-hosted, but
production does not need it.

## One-time setup

Never paste a token into a chat, an issue or a committed file. Tokens go only into
GitHub's secret settings. Replace `yourname` with your Hugging Face username.

### A. Upload the trained model to the private archive (once, from your machine)

The hourly data job (docs/data-job.md) already has a **private** Hugging Face dataset
(e.g. `yourname/perigee-snapshots`) and the secret `HF_TOKEN`. The daily job also needs
the trained model from there. PowerShell, in the project folder, one line at a time:

```powershell
$s = Read-Host -AsSecureString "HF token for the PRIVATE dataset"
$env:HF_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
$env:PERIGEE_HF_DATASET = "yourname/perigee-snapshots"
.\.venv\Scripts\python.exe -m src.ops.archive_sync push-models
```

It ends with "Pushed N model file(s)". The model stays private, like the raw data.

### B. Cloudflare Pages

1. <https://dash.cloudflare.com> (free account, no card) → *Workers & Pages* →
   *Create* → *Pages* → **Use direct upload** (not "Connect to Git"). Project name
   `perigee`; upload any small file once to create it. The site's address is shown,
   e.g. `https://perigee.pages.dev` (if the name was taken, Cloudflare gives another;
   use that one below).
2. **API token**: *My Profile* (top right) → *API Tokens* → *Create Token* → *Create
   Custom Token*. Permissions: **Account · Cloudflare Pages · Edit**; account
   resources: your account. *Continue to summary* → *Create Token*; copy it.
3. **Account ID**: on the *Workers & Pages* overview page, right-hand side.

### C. GitHub: secrets and variables

Repository → *Settings* → *Secrets and variables* → *Actions*.

**Secrets** tab, *New repository secret*:

| Name | Value |
|---|---|
| `HF_TOKEN` | already there (private archive) |
| `CLOUDFLARE_API_TOKEN` | the token from B2 |
| `CLOUDFLARE_ACCOUNT_ID` | the ID from B3 |

**Variables** tab, *New repository variable*:

| Name | Value |
|---|---|
| `PERIGEE_HF_DATASET` | already there (`yourname/perigee-snapshots`) |
| `PERIGEE_WEB_URL` | the address from B1, e.g. `https://perigee.pages.dev` (no trailing slash) |
| `CF_PAGES_PROJECT` | `perigee` |
| `SENTRY_DSN_WEB` | optional: a Sentry *Browser JavaScript* project's DSN |

Then *Settings* → *Environments* → *New environment* → `production` → *Configure
environment* (nothing else needed; add yourself as a required reviewer if you want to
approve each deploy).

### D. Protect main

*Settings* → *Rules* → *Rulesets* → *New branch ruleset* for `main`: require a pull
request, require the status checks `python`, `web`, `docker`, `security`, block force
pushes.

### E. First run

1. *Actions* → **publish** → *Run workflow*. About 15-25 minutes: it computes the
   results, builds the site with them and publishes it, then checks it.
2. Open the site.

From then on `publish` refreshes the data daily, and a merge to main redeploys the
code automatically after CI (keeping the current data).

### F. Uptime monitoring

<https://uptimerobot.com> (free): two *HTTP(s)* monitors, 5-minute interval:

- `PERIGEE_WEB_URL` (the site);
- `PERIGEE_WEB_URL` + `/data/index.json` (its data).

Add your email as the alert contact and send one test notification. Freshness (data
under 36 h old) is the watchdog's job.

## Phase 7 is done when

- a push to main deploys automatically (a merge after step E shows it);
- `data-refresh` and `publish` run unattended for three consecutive days (`watchdog`
  stays green);
- the uptime monitor is green.
