"""Concurrent load test of a running API (build guide 10.6: p95 under 200 ms for reads).

Runs N concurrent readers for a fixed time, first on their own and then while
two clients run the simulator back to back, for two request mixes: "repeated"
(the app's usual queries, mostly answered from the response cache) and
"uncached" (random pages and objects, so the cache rarely helps). Prints latency
percentiles per scenario. Start the API with a high rate limit first:

  PERIGEE_API_RATE_LIMIT_PER_MIN=1000000 PERIGEE_API_SIMULATE_PER_MIN=1000000 python -m src.main api --port 8001
  python scripts/load_test.py --url http://127.0.0.1:8001
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import time

import httpx

READS = [
    "/v1/meta",
    "/v1/conjunctions?limit=50",
    "/v1/conjunctions?sort=tca&max_miss_km=1&limit=50",
    "/v1/conjunctions?min_probability=0.5&limit=50",
    "/v1/objects?q=STARLINK&limit=50",
    "/v1/objects/25544",
    "/v1/shells",
]


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else float("nan")


def uncached_path(rng: random.Random, norads: list[int]) -> str:
    """A request unlikely to have been asked before (random page or object), so the cache misses."""
    kind = rng.randrange(3)
    if kind == 0:
        sort = rng.choice(["probability", "tca", "miss"])
        return f"/v1/conjunctions?sort={sort}&limit=50&offset={rng.randrange(0, 90_000)}"
    if kind == 1:
        return f"/v1/objects/{rng.choice(norads)}"
    return f"/v1/conjunctions?norad_id={rng.choice(norads)}&limit=50"


async def reader(client: httpx.AsyncClient, stop: float, out: list[float], errors: list[int], k: int,
                 norads: list[int] | None) -> None:
    i = k
    rng = random.Random(k)
    while time.monotonic() < stop:
        path = uncached_path(rng, norads) if norads else READS[i % len(READS)]
        i += 1
        t = time.perf_counter()
        r = await client.get(path)
        out.append((time.perf_counter() - t) * 1000)
        if r.status_code != 200:
            errors.append(r.status_code)


async def simulator(client: httpx.AsyncClient, stop: float, out: list[float], errors: list[int]) -> None:
    while time.monotonic() < stop:
        t = time.perf_counter()
        r = await client.post("/v1/simulate", json={"max_points": 1000}, timeout=60)
        out.append((time.perf_counter() - t) * 1000)
        if r.status_code != 200:
            errors.append(r.status_code)


async def scenario(url: str, readers: int, sims: int, seconds: float, uncached: bool) -> dict:
    reads, sim_times, errors = [], [], []
    limits = httpx.Limits(max_connections=readers + sims + 4)
    async with httpx.AsyncClient(base_url=url, timeout=30, limits=limits) as client:
        await client.get("/v1/meta")  # warm up
        norads = None
        if uncached:
            page = (await client.get("/v1/objects", params={"limit": 500})).json()
            norads = [o["norad_id"] for o in page["items"]]
        stop = time.monotonic() + seconds
        tasks = [reader(client, stop, reads, errors, k, norads) for k in range(readers)]
        tasks += [simulator(client, stop, sim_times, errors) for _ in range(sims)]
        await asyncio.gather(*tasks)
    return {
        "mix": "uncached" if uncached else "repeated",
        "readers": readers,
        "simulators": sims,
        "requests": len(reads),
        "rps": len(reads) / seconds,
        "p50": statistics.median(reads) if reads else float("nan"),
        "p95": pct(reads, 0.95),
        "p99": pct(reads, 0.99),
        "sim_runs": len(sim_times),
        "sim_p50": statistics.median(sim_times) if sim_times else float("nan"),
        "errors": len(errors),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("--readers", type=int, default=20)
    parser.add_argument("--seconds", type=float, default=30)
    args = parser.parse_args()
    print("| Requests | Readers | Simulators | Reads | Reads/s | p50 ms | p95 ms | p99 ms | Sim runs | Sim p50 ms | Errors |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for uncached in (False, True):
        for sims in (0, 2):
            r = asyncio.run(scenario(args.url, args.readers, sims, args.seconds, uncached))
            print(
                f"| {r['mix']} | {r['readers']} | {r['simulators']} | {r['requests']:,} | {r['rps']:.0f} | {r['p50']:.0f} | {r['p95']:.0f} | "
                f"{r['p99']:.0f} | {r['sim_runs']} | {r['sim_p50']:.0f} | {r['errors']} |"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
