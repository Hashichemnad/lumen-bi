# Load test

`backend/tools/loadtest.py` simulates people opening dashboards: each virtual person signs in, opens a
dashboard and loads all its chart tiles at once (as the browser does), reads for 1–3 seconds, and repeats.

```bash
cd backend
python -m tools.loadtest --base https://lumen.company.ae --email loadtest@company.ae --password '…' \
    --users 50 --seconds 120            # add --no-cache for the worst case (no cached results)
```

Rate limits apply per address: run it from a machine whose address is not shared with real users, or raise
`LOGIN_PER_MINUTE` for the test.

## Result on the development machine (23 Nov 2026)

One API process (Docker runs four), 2 CPU cores, 7 GB of memory, PostgreSQL on the same machine, the sample
"Gulf sales" (1,500 rows) and "Service tickets" (2,000 rows) dashboards, 30 people for 60 seconds:

| | requests | median | 95th percentile | 99th percentile |
| --- | --- | --- | --- | --- |
| With the result cache | 5,863 (92 per second) | | | |
| – open a dashboard | 687 | 0.23 s | 0.56 s | 0.79 s |
| – one chart tile | 5,176 | 0.21 s | 0.49 s | 0.65 s |
| – whole dashboard with all tiles | 687 | 0.51 s | 1.07 s | 1.40 s |
| Without the cache (`--no-cache`) | 5,559 (87 per second) | | | |
| – one chart tile | 4,905 | 0.26 s | 0.53 s | 0.64 s |
| – whole dashboard with all tiles | 654 | 0.65 s | 1.16 s | 1.34 s |

No errors (two connections were cut when the test stopped). 30 people constantly clicking is far more than
30 people at work, so one small server serves a few hundred users of this size of data comfortably.

## Sizing

- Queries run in DuckDB inside the API processes: more cores = more dashboards at once. `DUCKDB_MEMORY_LIMIT`
  per process × 4 processes should leave room for PostgreSQL and the workers.
- Large datasets (tens of millions of rows) are mostly bounded by disk and memory; time a heavy dashboard
  with `--no-cache` on the real server before go-live.
- Exports, PDF reports and syncs run in the Celery worker, not in the API processes.
