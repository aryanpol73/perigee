# API performance under load

Measured 2026-11-01 on the development laptop (Windows, one uvicorn process,
loopback network) against the full live bundle: 20,087 objects and 92,492 close
approaches. Target from the build guide: p95 under 200 ms for read endpoints.
Reproduce with `scripts/load_test.py` (instructions in its docstring).

## Results

20 concurrent readers, spread over 4 client processes (5 each), 30 s per row.
"Repeated" is the app's usual queries; "uncached" asks random pages and objects,
so the response cache rarely helps. "Simulators" are clients calling
`POST /v1/simulate` back to back (rate limit raised for the test).

| Requests | Simulators | Reads/s | p50 ms | p95 ms | p99 ms | Errors | Simulation time |
|---|---|---|---|---|---|---|---|
| uncached | 0 | 606 | 28 | 67 | n/a | 0 | n/a |
| repeated | 2 | 642 | 29 | 52 | 89 | 0 | 1.7 s (p50) |
| uncached | 2 | 575 | 29 | 71 | 104 | 0 | 2.3 s (p50) |

Server-side handling time (from the API's request logs, all reads): p50 0.7 ms,
p95 7.9 ms. Almost all of the client-side latency above is waiting for the single
process, not computing.

## What changed to get there

A first run (one client process, no response cache, sorting per request) gave
p95 448 ms at 95 reads/s. Two causes were found and fixed:

- Every conjunction request re-sorted 92,492 rows. The store now precomputes the
  three sort orders once at load; a request only filters a presorted index.
- Identical requests were recomputed. The bundle never changes while the process
  runs, so successful GET responses are cached by URL (up to 4,096 entries),
  inside the gzip layer and behind the rate limiter.
- Simulations now run one at a time (`anyio.CapacityLimiter(1)`), so two heavy
  requests cannot take every worker thread from the readers. The per-IP simulator
  limit is `PERIGEE_API_SIMULATE_PER_MIN` (default 10).

The single-client-process load generator itself saturated its CPU, which hid how
fast the server was; the 4-process numbers are the honest ones.

## Caveats

- One laptop, loopback: a free cloud host has fewer and slower CPUs. Measure again
  after deployment (Phase 7).
- A sleeping free host adds a cold start of up to a minute; the app says so.
- Simulations queue: with sustained simulator traffic each one waits for the
  previous. The rate limit keeps that bounded per client.
