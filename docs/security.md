# Security notes

## Dependency advisories

### npm: GHSA-g3hv-75wg-m249 in minimatch (moderate), reviewed 2026-11-01

`npm audit` in `web/` reports three moderate findings that are one advisory seen
through its dependency chain:

```
openapi-typescript 7.13.0        (devDependency: generates src/api-types.ts)
└─ @redocly/openapi-core 1.34.22
   └─ minimatch 5.1.9            GHSA-g3hv-75wg-m249, affects < 11.0.1
```

The advisory is a denial of service: a crafted glob pattern makes `minimatch`
expand patterns exponentially.

**Exposure in this project: none found.**

- `openapi-typescript` is a development tool. It runs only when a developer runs
  `npm run gen:api` on the committed schema `tests/fixtures/openapi.json`. It is not
  in the production dependency tree (`npm audit --omit=dev`: 0 vulnerabilities) and
  not in the built app (`web/dist` contains no `minimatch` or `redocly` code).
- In `@redocly/openapi-core`, `minimatch` is called only from `match()` in
  `lib/utils.js`, which compares fetched URLs with patterns from a Redocly config
  file (`resolve.http.headers`). The project has no Redocly config, and the
  generator reads a local file we control. No untrusted pattern reaches it.

**Why not upgrade or override.** No release of `openapi-typescript` (latest 7.13.0)
uses a `@redocly/openapi-core` without `minimatch` 5. `npm audit fix --force` offers
a downgrade to `openapi-typescript` 6.7.6, an older major with its own changes.
Overriding `minimatch` to 11 would break `@redocly/openapi-core`, which calls
`require("minimatch")` as a function, while 11 exports it as a named member.

**Decision.** Accept for now: development-only and not reachable with untrusted
input. Re-check when `openapi-typescript` moves to `@redocly/openapi-core` 2.x. CI
(Phase 7) should fail on any advisory in production dependencies
(`npm audit --omit=dev`, `pip-audit`) and report development-only advisories for review.
