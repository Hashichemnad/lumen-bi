"""One DuckDB instance per process, shared by all queries.

Each query gets its own cursor (a separate connection to the same in-memory database), so queries run in
parallel and share DuckDB's caches. Queries that run too long are interrupted.
"""

import threading
from typing import Any

import duckdb

from app.core.config import get_settings

_lock = threading.Lock()
_db: duckdb.DuckDBPyConnection | None = None


class QueryTimeout(Exception):
    pass


def _instance() -> duckdb.DuckDBPyConnection:
    global _db
    with _lock:
        if _db is None:
            settings = get_settings()
            config: dict[str, Any] = {}
            if settings.duckdb_memory_limit:
                config["memory_limit"] = settings.duckdb_memory_limit
            if settings.duckdb_threads:
                config["threads"] = settings.duckdb_threads
            _db = duckdb.connect(":memory:", config=config)
        return _db


def run(sql: str, params: list[Any] | None = None, timeout: float | None = None):
    """Run a query and return (column names, rows)."""
    cur = _instance().cursor()
    timeout = timeout if timeout is not None else get_settings().query_timeout_seconds
    timer = threading.Timer(timeout, cur.interrupt) if timeout else None
    try:
        cur.execute("SET TimeZone = ?", [get_settings().timezone])
        if timer:
            timer.start()
        try:
            cur.execute(sql, params or [])
            names = [d[0] for d in cur.description]
            rows = cur.fetchall()
        except duckdb.InterruptException as exc:
            raise QueryTimeout("The query took too long and was stopped") from exc
        return names, rows
    finally:
        if timer:
            timer.cancel()
        cur.close()


def stream(sql: str, params: list[Any] | None = None, batch: int = 10_000):
    """Yield (column names) then lists of rows, for large exports."""
    cur = _instance().cursor()
    try:
        cur.execute("SET TimeZone = ?", [get_settings().timezone])
        cur.execute(sql, params or [])
        yield [d[0] for d in cur.description]
        while rows := cur.fetchmany(batch):
            yield rows
    finally:
        cur.close()
