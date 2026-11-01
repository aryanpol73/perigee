# Threat model

Perigee is a public, read-only site with no accounts and no personal data. What is
worth protecting: the credentials that can write the archive, the results and the
deployments; the honesty of what the site says; and the free services' quotas.

## Assets

| Asset | Where | Why it matters |
|---|---|---|
| `HF_TOKEN` | GitHub secret | Writes the private raw archive (CelesTrak snapshots, models trained on Space-Track history) |
| `HF_DEPLOY_TOKEN` | GitHub secret | Writes the public results dataset and the API Space: whoever has it controls what the site shows |
| `CLOUDFLARE_API_TOKEN` | GitHub secret | Publishes the site |
| Raw archive | private Hugging Face dataset | Space-Track data must not be redistributed; CelesTrak history cannot be downloaded again |
| CelesTrak access | the fetch log and STOP file | Polling too often gets the project's IP blocked |
| The results | public dataset, API | People may take them as more than estimates |

## Threats and what stops them

| Who / what | Could | Stopped by |
|---|---|---|
| Anyone on the internet | Flood the API or the simulator | Per-IP rate limits (120/min; simulator 10/min), one simulation at a time, hard input caps and a 15 s limit, bounded page sizes, response cache. The free Space is the blast radius: at worst it slows or sleeps; no bill. |
| The same, spoofing `X-Forwarded-For` | Dodge the per-IP limit | Partly: behind the Space's proxy the client address comes from that header, which a client can forge. The global limits (one simulation at a time, cached reads) still hold. Accepted for a free demo. |
| A crafted request | Trigger errors or leak internals | Strict Pydantic bounds on every parameter, one error shape with a request id and no stack traces, read-only data in memory (no SQL built from input). |
| A malicious pull request from a fork | Read secrets or deploy | `pull_request` runs get no secrets; `deploy` runs only after CI on a push to main or by hand; workflows default to read-only permissions. |
| A compromised third-party Action or package | Run code in CI with secrets | Actions pinned to full commit hashes; dependencies pinned and audited in CI (`pip-audit`, `npm audit`); Dependabot proposes updates for review. The deploy job gets only the deploy token. |
| A leaked token | Overwrite results or the archive | Fine-grained tokens, each limited to the repositories it needs; `gitleaks` scans the full history in CI; rotate any token that ever appeared outside the secret store. |
| A mistake (or a bad merge) | Publish raw Space-Track data | Results uploads accept only the bundle's whitelisted files; the archive code refuses a public dataset; the two datasets use different tokens. |
| A bug in our job | Hammer CelesTrak | The fetch log enforces CelesTrak's intervals; any refusal writes a STOP file that halts all requests until a human removes it. |
| A third-party script on the site | Exfiltrate or deface | No third-party scripts: the content security policy allows scripts only from the site itself, and connections only to the API, NASA's imagery tiles and (if set) Sentry. `frame-ancestors 'none'` prevents framing. |
| Over-trust in the output | Treat a risk estimate as a collision warning | Wording never says "collision prediction"; every page, API response and the model card say estimates from public data, not for operational use; provisional scores are flagged. |

## Browser policy notes

The policy allows `'unsafe-eval'` and `'wasm-unsafe-eval'` because Cesium needs
both (its widgets compile bindings with `new Function`; its decoders are
WebAssembly). Checked by removing them: the globe stops. Scripts still load only
from the site's origin, so this does not open the page to injected script files.

## Privacy

No accounts, cookies or analytics. Sentry, when enabled, receives error reports
without personal data (the SDK defaults) and no performance traces from browsers.
The API logs the request path, status and timing with a request id; client IP
addresses are kept only in memory for the rate limiter.
