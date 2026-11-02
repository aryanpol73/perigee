# Threat model

Perigee is a public, read-only site with no accounts and no personal data. What is
worth protecting: the credentials that can write the archive, the results and the
deployments; the honesty of what the site says; and the free services' quotas.

## Assets

| Asset | Where | Why it matters |
|---|---|---|
| `HF_TOKEN` | GitHub secret | Writes the private raw archive (CelesTrak snapshots, models trained on Space-Track history) |
| `CLOUDFLARE_API_TOKEN` | GitHub secret | Publishes the site (code and data): whoever has it controls what the site shows. Scoped to Cloudflare Pages only. |
| Raw archive | private Hugging Face dataset | Space-Track data must not be redistributed; CelesTrak history cannot be downloaded again |
| CelesTrak access | the fetch log and STOP file | Polling too often gets the project's IP blocked |
| The results | the site's `/data` files | People may take them as more than estimates |

## Threats and what stops them

| Who / what | Could | Stopped by |
|---|---|---|
| Anyone on the internet | Flood the service | Production has no server to overload: Cloudflare serves static files from its CDN (free plan, unlimited bandwidth), and the simulator runs on each visitor's own computer, so a heavy user only slows themselves. |
| A crafted request | Trigger errors or leak internals | Nothing on the site runs server code. The in-browser query layer applies the API's own bounds (422 on out-of-range values); the self-hostable API keeps strict Pydantic bounds, rate limits, one error shape with a request id and no stack traces. |
| A malicious pull request from a fork | Read secrets or deploy | `pull_request` runs get no secrets; `deploy` runs only after CI on a push to main or by hand; workflows default to read-only permissions. |
| A compromised third-party Action or package | Run code in CI with secrets | Actions pinned to full commit hashes; dependencies pinned and audited in CI (`pip-audit`, `npm audit`); Dependabot proposes updates for review. The deploy jobs get only the Cloudflare token; the archive's `HF_TOKEN` is used only by the compute job. Pyodide's packages are checked against the sha256 in its lock file before they are served. |
| A leaked token | Overwrite results or the archive | Fine-grained tokens, each limited to the repositories it needs; `gitleaks` scans the full history in CI; rotate any token that ever appeared outside the secret store. |
| A mistake (or a bad merge) | Publish raw Space-Track data | The site's data is exported only from the derived results bundle (`src/results/static_site.py`); models and raw data stay in the private archive, and the archive code refuses a public dataset. |
| A bug in our job | Hammer CelesTrak | The fetch log enforces CelesTrak's intervals; any refusal writes a STOP file that halts all requests until a human removes it. |
| A third-party script on the site | Exfiltrate or deface | No third-party scripts: the content security policy allows scripts only from the site itself, and connections only to the site itself, NASA's imagery tiles and (if set) Sentry; the simulator's Python runtime is served by the site, not a CDN. `frame-ancestors 'none'` prevents framing. |
| Over-trust in the output | Treat a risk estimate as a collision warning | Wording never says "collision prediction"; every page, API response and the model card say estimates from public data, not for operational use; provisional scores are flagged. |

## Browser policy notes

The policy allows `'unsafe-eval'` and `'wasm-unsafe-eval'` because Cesium needs
both (its widgets compile bindings with `new Function`; its decoders are
WebAssembly); the browser simulator (Pyodide) also runs WebAssembly. Checked by removing them: the globe stops. Scripts still load only
from the site's origin, so this does not open the page to injected script files.

## Privacy

No accounts, cookies or analytics. Sentry, when enabled, receives error reports
without personal data (the SDK defaults) and no performance traces from browsers.
The API logs the request path, status and timing with a request id; client IP
addresses are kept only in memory for the rate limiter.
