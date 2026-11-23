"""Parquet snapshot store.

Layout: DATA_DIR/datasets/<dataset id>/v<version>/data.parquet

A sync writes the new snapshot into a hidden temporary folder, then renames it to v<n> (atomic on the same
filesystem) and only then points the dataset at it. Readers keep using the old version until the switch,
and a failed sync leaves the live data untouched. The last N versions are kept for rollback.
"""

import re
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from app.connectors.base import DELETED_COLUMN, ColumnInfo, lumen_type
from app.core.config import get_settings

VERSION_DIR = re.compile(r"^v(\d+)$")


def dataset_dir(dataset_id: int) -> Path:
    return get_settings().data_dir.resolve() / "datasets" / str(int(dataset_id))


def snapshot_path(dataset_id: int, version: int) -> Path | None:
    if version <= 0:
        return None
    path = dataset_dir(dataset_id) / f"v{version}" / "data.parquet"
    return path if path.is_file() else None


def versions(dataset_id: int) -> list[int]:
    root = dataset_dir(dataset_id)
    if not root.is_dir():
        return []
    return sorted(int(m.group(1)) for p in root.iterdir() if (m := VERSION_DIR.match(p.name)))


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lit(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


class SnapshotResult:
    def __init__(self, version: int, rows_read: int, rows_total: int, columns: list[ColumnInfo]):
        self.version = version
        self.rows_read = rows_read
        self.rows_total = rows_total
        self.columns = columns


def max_value(path: Path, column: str) -> Any:
    """Largest value of a column in a snapshot (the starting point for an incremental sync)."""
    con = duckdb.connect()
    try:
        names = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet({_lit(path)})").fetchall()]
        if column not in names:
            return None
        return con.execute(f"SELECT max({_q(column)}) FROM read_parquet({_lit(path)})").fetchone()[0]
    finally:
        con.close()


def write_snapshot(
    dataset_id: int,
    batches: Iterable[pa.RecordBatch],
    *,
    merge_with: Path | None = None,
    primary_key: list[str] | None = None,
) -> SnapshotResult | None:
    """Write batches as the next version. With merge_with, new rows replace old rows that have the same
    primary key (or are appended when there is no key). Returns None when an incremental sync found nothing
    new (the current version stays live)."""
    root = dataset_dir(dataset_id)
    root.mkdir(parents=True, exist_ok=True)
    existing = versions(dataset_id)
    version = (max(existing) if existing else 0) + 1
    tmp = root / f".v{version}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    parts = tmp / "parts"
    parts.mkdir(parents=True)

    try:
        rows_read = 0
        part_files: list[Path] = []
        for i, batch in enumerate(batches):
            if batch.num_rows == 0 and part_files:
                continue
            part = parts / f"part-{i:05d}.parquet"
            pq.write_table(pa.Table.from_batches([batch]), part)
            part_files.append(part)
            rows_read += batch.num_rows

        if merge_with is not None and rows_read == 0:
            shutil.rmtree(tmp)
            return None

        out = tmp / "data.parquet"
        con = duckdb.connect()
        try:
            # union_by_name: batches may disagree on types (e.g. an all-null batch); DuckDB unifies them
            new = f"read_parquet({_lit(parts / '*.parquet')}, union_by_name = true)"
            names = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {new}").fetchall()]
            # Rows the source reports as deleted: never kept, but their keys still remove older versions
            live = (
                f"(SELECT * EXCLUDE ({_q(DELETED_COLUMN)}) FROM {new} "
                f"WHERE NOT coalesce({_q(DELETED_COLUMN)}, false))"
                if DELETED_COLUMN in names
                else new
            )
            if merge_with is not None:
                old = f"read_parquet({_lit(merge_with)})"
                keys = [k for k in (primary_key or []) if k]
                if keys:
                    using = ", ".join(_q(k) for k in keys)
                    query = (
                        f"SELECT * FROM {live} UNION ALL BY NAME "
                        f"SELECT * FROM (SELECT * FROM {old} "
                        f"ANTI JOIN (SELECT {using} FROM {new}) USING ({using}))"
                    )
                else:
                    query = f"SELECT * FROM {old} UNION ALL BY NAME SELECT * FROM {live}"
            else:
                query = f"SELECT * FROM {live}"
            con.execute(f"COPY ({query}) TO {_lit(out)} (FORMAT parquet, COMPRESSION zstd)")
        finally:
            con.close()
        shutil.rmtree(parts)

        meta = pq.ParquetFile(out)
        columns = [ColumnInfo(f.name, lumen_type(f.type)) for f in meta.schema_arrow]
        rows_total = meta.metadata.num_rows
        tmp.rename(root / f"v{version}")  # the atomic switch on disk
        return SnapshotResult(version, rows_read, rows_total, columns)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def prune(dataset_id: int, current: int, keep: int | None = None) -> None:
    keep = keep or get_settings().snapshots_to_keep
    root = dataset_dir(dataset_id)
    old = [v for v in versions(dataset_id) if v != current]
    for v in sorted(old)[: max(0, len(old) - (keep - 1))]:
        shutil.rmtree(root / f"v{v}", ignore_errors=True)
    for leftover in root.glob(".v*.tmp"):  # from a crashed sync
        shutil.rmtree(leftover, ignore_errors=True)


def delete_all(dataset_id: int) -> None:
    shutil.rmtree(dataset_dir(dataset_id), ignore_errors=True)


def preview(path: Path, limit: int = 100) -> tuple[list[str], list[list[Any]]]:
    con = duckdb.connect()
    try:
        # Show times in the organisation's time zone, whatever the server's own zone is
        con.execute("SET TimeZone = ?", [get_settings().timezone])
        cur = con.execute(f"SELECT * FROM read_parquet({_lit(path)}) LIMIT {int(limit)}")
        names = [d[0] for d in cur.description]
        return names, [list(r) for r in cur.fetchall()]
    finally:
        con.close()


def schema_changes(old: list[dict], new: list[ColumnInfo]) -> dict:
    before = {c["name"]: c["type"] for c in old}
    after = {c.name: c.type for c in new}
    changes = {
        "added": [n for n in after if n not in before],
        "removed": [n for n in before if n not in after],
        "retyped": [
            {"name": n, "from": before[n], "to": after[n]}
            for n in after
            if n in before and before[n] != after[n]
        ],
    }
    return {k: v for k, v in changes.items() if v}
