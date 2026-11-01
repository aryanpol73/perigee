# Deploying Perigee

| Workflow | When | What |
|---|---|---|
| `ci.yml` | every push and pull request | lint, types, tests (coverage gate 80%), web build and browser tests, the API image built and started, dependency audits, secret scan |
| `deploy.yml` | after `ci` passes on main (or by hand, for rollback) | API to Render (deploy hook, exact commit), web app to Cloudflare Pages, smoke tests |
| `data-refresh.yml` | hourly (GitHub runs it every 4-8 h) | CelesTrak snapshots into the private archive (docs/data-job.md) |
| `publish.yml` | daily 04:41 UTC | rebuild, screen, score, results to the public dataset, redeploy the API to load them |
| `watchdog.yml` | every 3 h | site and API up, results under 36 h old, scheduled jobs still succeeding |

Where things run: the API is a free Render web service built from the `Dockerfile`
(`render.yaml`); the site is on Cloudflare Pages; results are a public Hugging Face
dataset; raw data stays in the private one. Why: docs/decisions/0003-deployment.md.
When something breaks: docs/runbook.md.

## One-time setup

Never paste a token into a chat, an issue or a committed file. Tokens go only into
GitHub's (or Render's) secret settings. Replace `yourname` below with your Hugging
Face username.

### A. Hugging Face: results dataset and its token

The hourly data job (docs/data-job.md) already has a **private** dataset (e.g.
`yourname/perigee-snapshots`) and the secret `HF_TOKEN` that can write only it.

1. **Results dataset**: <https://huggingface.co/new-dataset>, name `perigee-results`,
   **Public**, *Create dataset*. Leave it empty; `publish` fills it with derived
   results only.
2. **Deploy token**: <https://huggingface.co/settings/tokens> → *Create new token* →
   *Fine-grained*, name `perigee-deploy`. Under *Repositories permissions* add
   `yourname/perigee-results` and tick **Write**; nothing else. Copy it (shown once).
3. **Upload the trained model to the private dataset** (once, from your machine; the
   daily job pulls it from there). PowerShell, in the project folder, one line at a time:

   ```powershell
   $s = Read-Host -AsSecureString "HF token for the PRIVATE dataset"
   $env:HF_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
   $env:PERIGEE_HF_DATASET = "yourname/perigee-snapshots"
   .\.venv\Scripts\python.exe -m src.ops.archive_sync push-models
   ```

   It ends with "Pushed N model file(s)".

### B. Render: the API

1. Sign up at <https://render.com> (GitHub sign-in is easiest; no card is needed for
   the free plan). Allow Render to read the `perigee` repository when asked.
2. *New +* → **Blueprint** → pick the `perigee` repository, branch `main`. Render
   reads `render.yaml` and shows one free web service, `perigee-api`.
3. It asks for three values:
   - `PERIGEE_RESULTS_REPO` = `yourname/perigee-results`
   - `PERIGEE_API_CORS_ORIGINS` = your site's address from step C1, e.g.
     `https://perigee.pages.dev` (no trailing slash)
   - `SENTRY_DSN` = leave empty (optional)

   Click *Apply*. The first build takes a few minutes; until `publish` has run, the
   API answers "No results published yet", which is expected.
4. The service's address is shown at the top of its page, e.g.
   `https://perigee-api.onrender.com` (yours may have a suffix). This is
   `PERIGEE_API_URL`.
5. **Deploy hook**: service → *Settings* → *Deploy Hook* → copy the URL. It is a
   secret: anyone with it can trigger a deploy.

### C. Cloudflare Pages: the site

1. <https://dash.cloudflare.com> → *Workers & Pages* → *Create* → *Pages* → **Use
   direct upload** (not Git). Project name `perigee`; upload any small file once.
   The address is `https://perigee.pages.dev` (if the name is taken, use the one
   Cloudflare gives you everywhere below, and in Render's `PERIGEE_API_CORS_ORIGINS`).
2. **API token**: *My Profile* → *API Tokens* → *Create Token* → *Create Custom
   Token*: permission **Account · Cloudflare Pages · Edit**, your account. Copy it.
3. **Account ID**: on the *Workers & Pages* overview page, right-hand side.

### D. GitHub: secrets and variables

Repository → *Settings* → *Secrets and variables* → *Actions*.

**Secrets** tab, *New repository secret*:

| Name | Value |
|---|---|
| `HF_TOKEN` | already there (private archive only) |
| `HF_DEPLOY_TOKEN` | token from A2 |
| `RENDER_DEPLOY_HOOK` | URL from B5 |
| `CLOUDFLARE_API_TOKEN` | token from C2 |
| `CLOUDFLARE_ACCOUNT_ID` | ID from C3 |
| `SENTRY_DSN` | optional, leave out |

**Variables** tab, *New repository variable*:

| Name | Value |
|---|---|
| `PERIGEE_HF_DATASET` | already there (`yourname/perigee-snapshots`) |
| `PERIGEE_RESULTS_REPO` | `yourname/perigee-results` |
| `PERIGEE_API_URL` | from B4, e.g. `https://perigee-api.onrender.com` |
| `PERIGEE_WEB_URL` | from C1, e.g. `https://perigee.pages.dev` |
| `CF_PAGES_PROJECT` | `perigee` |
| `SENTRY_DSN_WEB` | optional, leave out |

No trailing slashes, no spaces.

Then *Settings* → *Environments* → *New environment* → `production` → *Configure
environment* (nothing else needed; add yourself as a required reviewer if you want
to approve each deploy).

### E. Protect main

*Settings* → *Rules* → *Rulesets* → *New branch ruleset* for `main`: require a pull
request, require the status checks `python`, `web`, `docker`, `security`, block
force pushes.

### F. First run, in this order

1. *Actions* → **publish** → *Run workflow*. About 15-25 minutes: it builds the first
   results bundle, uploads it and redeploys the API, then checks the API serves it.
2. *Actions* → **deploy** → *Run workflow* (`ref` = `main`). The API job checks the
   deployed commit and CORS; the web job publishes the site and checks its headers.
3. Open the site.

From then on a merge to main deploys by itself after CI, and `publish` runs daily.

### G. Uptime monitoring

<https://uptimerobot.com> (free): two *HTTP(s)* monitors, 5-minute interval:

- `PERIGEE_API_URL` + `/v1/health` (503 when data is stale, so stale shows as down);
- `PERIGEE_WEB_URL`.

Add your email as the alert contact and send one test notification. The 5-minute ping
also keeps the free Render service from sleeping (it sleeps after 15 idle minutes and
takes about a minute to wake). One service awake all month uses ~744 of Render's 750
free hours.

## Phase 7 is done when

- a push to main deploys automatically (a merge after step F shows it);
- `data-refresh` and `publish` run unattended for three consecutive days (`watchdog`
  stays green);
- the uptime monitor is green.
