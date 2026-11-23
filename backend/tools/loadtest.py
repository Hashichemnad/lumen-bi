"""A simple load test: many people opening dashboards at the same time.

Each virtual person signs in once, then, for the duration, repeatedly opens a dashboard and loads every chart
tile on it (as the browser does, all tiles at once), with a short pause between views. Reports requests per
second, response-time percentiles per kind of request, and errors.

    python -m tools.loadtest --base http://localhost:8080 --email viewer@company.ae --password ... \
        --users 50 --seconds 120 [--dashboard 3] [--no-cache]

Use accounts that are allowed to sign in many times (rate limits apply per address: run it from a machine
whose address is not limited, or raise LOGIN_PER_MINUTE for the test). --no-cache adds a random filter
value so the query cache cannot answer (worst case).
"""

import argparse
import asyncio
import random
import statistics
import time
from collections import defaultdict

import httpx

H = {"X-Requested-With": "lumen"}


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = max(0, min(len(values) - 1, round(p / 100 * (len(values) - 1))))
    return values[k]


async def person(args, stats, errors, stop: float):
    try:
        await _person(args, stats, errors, stop)
    except httpx.HTTPError as exc:
        errors[exc.__class__.__name__] += 1


async def _person(args, stats, errors, stop: float):
    async with httpx.AsyncClient(base_url=args.base, timeout=60, headers=H) as c:
        r = await c.post("/api/v1/auth/login", json={"email": args.email, "password": args.password})
        if r.status_code != 200 or "two_factor" in r.text:
            errors["login"] += 1
            return
        dashboards = (await c.get("/api/v1/dashboards")).json()
        ids = [args.dashboard] if args.dashboard else [d["id"] for d in dashboards]
        if not ids:
            errors["no dashboards"] += 1
            return
        while time.monotonic() < stop:
            did = random.choice(ids)
            t0 = time.perf_counter()
            r = await c.get(f"/api/v1/dashboards/{did}")
            stats["dashboard"].append(time.perf_counter() - t0)
            if r.status_code != 200:
                errors[f"dashboard {r.status_code}"] += 1
                continue
            d = r.json()
            tiles = [i["id"] for tab in d["config"]["tabs"] for i in tab["items"] if i["kind"] == "chart"]

            async def tile(tid: str, did: int = did):
                body = {"item": tid, "filters": [], "params": {}}
                if args.no_cache:
                    body["params"] = {"__nocache": random.random()}
                t1 = time.perf_counter()
                try:
                    tr = await c.post(f"/api/v1/dashboards/{did}/query", json=body)
                except httpx.HTTPError as exc:
                    errors[exc.__class__.__name__] += 1
                    return
                stats["tile query"].append(time.perf_counter() - t1)
                if tr.status_code != 200:
                    errors[f"tile {tr.status_code}"] += 1

            await asyncio.gather(*(tile(t) for t in tiles))
            stats["whole dashboard"].append(time.perf_counter() - t0)
            await asyncio.sleep(random.uniform(1, 3))  # reading


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--users", type=int, default=20)
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--dashboard", type=int)
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()
    stats: dict[str, list[float]] = defaultdict(list)
    errors: dict[str, int] = defaultdict(int)
    started = time.monotonic()
    stop = started + args.seconds

    async def staggered(i: int):
        await asyncio.sleep(i * 0.2)  # people arrive over a few seconds
        await person(args, stats, errors, stop)

    await asyncio.gather(*(staggered(i) for i in range(args.users)))
    took = time.monotonic() - started
    total = sum(len(v) for k, v in stats.items() if k != "whole dashboard")
    print(f"{args.users} people, {took:.0f} s, {total} requests, {total / took:.1f} requests/s")
    print(f"{'':18} {'count':>7} {'median':>8} {'p95':>8} {'p99':>8} {'max':>8}  (seconds)")
    for k, v in stats.items():
        print(
            f"{k:18} {len(v):7} {statistics.median(v):8.3f} {pct(v, 95):8.3f} {pct(v, 99):8.3f} {max(v):8.3f}"
        )
    print("errors:", dict(errors) or "none")


if __name__ == "__main__":
    asyncio.run(main())
