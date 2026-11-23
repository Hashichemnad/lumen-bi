"""Datasets built from other datasets: joins and unions, materialised from the parents' snapshots.

join:  {"base": 12, "joins": [{"dataset": 13, "type": "left", "alias": "Account",
                               "on": [{"left": "AccountId", "right": "Id"}]}]}
       Base columns keep their names; joined columns are named "<alias>.<column>". A later join can match
       on an earlier joined column ("Account.OwnerId").
union: {"datasets": [3, 4, 5], "source_column": "Source"}  (stacks rows by column name; optional column
       saying which dataset each row came from)

Materialising (rather than joining at query time) keeps dashboards fast and lets a joined dataset have its
own fields, formulas and row-level security, exactly like any other dataset.
"""

from collections.abc import Iterator
from typing import Any

import duckdb
import pyarrow as pa
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import ConnectorError
from app.models import Dataset
from app.services import snapshots

JOIN_TYPES = {"inner": "INNER", "left": "LEFT", "full": "FULL OUTER"}
DERIVED_KINDS = {"join", "union"}


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def kind_of(source: dict) -> str:
    if "join" in source:
        return "join"
    if "union" in source:
        return "union"
    return "source"


def parents(source: dict) -> list[int]:
    if "join" in source:
        spec = source["join"]
        return [int(spec["base"])] + [int(j["dataset"]) for j in spec.get("joins", [])]
    if "union" in source:
        return [int(d) for d in source["union"].get("datasets", [])]
    return []


def ancestors(db: Session, dataset_id: int, seen: set[int] | None = None) -> set[int]:
    seen = seen if seen is not None else set()
    ds = db.get(Dataset, dataset_id)
    for pid in parents(ds.source or {}) if ds else []:
        if pid not in seen:
            seen.add(pid)
            ancestors(db, pid, seen)
    return seen


def dependents(db: Session, dataset_id: int) -> list[Dataset]:
    derived = db.scalars(select(Dataset).where(Dataset.kind.in_(DERIVED_KINDS))).all()
    return [d for d in derived if dataset_id in parents(d.source or {})]


def _columns(ds: Dataset) -> list[str]:
    return [c["name"] for c in ds.columns or []]


def _get(db: Session, dataset_id: Any, role: str) -> Dataset:
    try:
        ds = db.get(Dataset, int(dataset_id))
    except (TypeError, ValueError):
        ds = None
    if ds is None:
        raise ConnectorError(f"The {role} dataset was not found.")
    if not ds.columns:
        raise ConnectorError(f'"{ds.name}" has no data yet. Sync it first.')
    return ds


def validate(db: Session, source: dict, self_id: int | None = None) -> dict:
    """Check a join/union definition and return it normalised.

    Raises ConnectorError with a readable message."""
    if "join" in source:
        spec = source["join"] or {}
        base = _get(db, spec.get("base"), "base")
        joins = spec.get("joins") or []
        if not joins:
            raise ConnectorError("Add at least one dataset to join.")
        available = set(_columns(base))
        aliases: set[str] = set()
        out_joins = []
        for j in joins:
            right = _get(db, j.get("dataset"), "joined")
            jtype = j.get("type", "left")
            if jtype not in JOIN_TYPES:
                raise ConnectorError("Join type must be inner, left or full.")
            alias = str(j.get("alias") or right.name).strip().replace(".", " ")
            if not alias or alias in aliases:
                raise ConnectorError(f'Each joined dataset needs a unique name ("{alias}" is used twice).')
            on = j.get("on") or []
            if not on:
                raise ConnectorError(f'Choose the matching columns for "{alias}".')
            right_cols = set(_columns(right))
            for pair in on:
                if pair.get("left") not in available:
                    raise ConnectorError(f'Column "{pair.get("left")}" is not available to join on.')
                if pair.get("right") not in right_cols:
                    raise ConnectorError(f'"{right.name}" has no column "{pair.get("right")}".')
            aliases.add(alias)
            available |= {f"{alias}.{c}" for c in right_cols}
            out_joins.append(
                {
                    "dataset": right.id,
                    "type": jtype,
                    "alias": alias,
                    "on": [{"left": p["left"], "right": p["right"]} for p in on],
                }
            )
        normalised = {"join": {"base": base.id, "joins": out_joins}}
    elif "union" in source:
        spec = source["union"] or {}
        ids = [int(d) for d in spec.get("datasets") or []]
        if len(ids) < 2 or len(set(ids)) != len(ids):
            raise ConnectorError("Choose at least two different datasets to combine.")
        for d in ids:
            _get(db, d, "combined")
        normalised = {
            "union": {"datasets": ids, "source_column": str(spec.get("source_column") or "").strip()}
        }
    else:
        raise ConnectorError("Unknown kind of combined dataset.")

    if self_id is not None:
        for pid in parents(normalised):
            if pid == self_id or self_id in ancestors(db, pid):
                raise ConnectorError("A dataset cannot be built from itself.")
    return normalised


def _path(db: Session, dataset_id: int) -> str:
    ds = db.get(Dataset, dataset_id)
    path = snapshots.snapshot_path(dataset_id, ds.current_version) if ds else None
    if path is None:
        raise ConnectorError(f'"{ds.name if ds else dataset_id}" has no data yet. Sync it first.')
    return f"read_parquet({_lit(str(path))})"


def build_query(db: Session, dataset: Dataset) -> str:
    source = validate(db, dataset.source or {}, self_id=dataset.id)
    if "join" in source:
        spec = source["join"]
        base = db.get(Dataset, spec["base"])
        select_cols = [f"b.{_q(c)}" for c in _columns(base)]
        from_sql = f"{_path(db, base.id)} AS b"
        where = {c: f"b.{_q(c)}" for c in _columns(base)}
        for i, j in enumerate(spec["joins"], start=1):
            right = db.get(Dataset, j["dataset"])
            alias_sql = f"j{i}"
            cond = " AND ".join(f"{where[p['left']]} = {alias_sql}.{_q(p['right'])}" for p in j["on"])
            from_sql += f" {JOIN_TYPES[j['type']]} JOIN {_path(db, right.id)} AS {alias_sql} ON {cond}"
            for c in _columns(right):
                name = f"{j['alias']}.{c}"
                select_cols.append(f"{alias_sql}.{_q(c)} AS {_q(name)}")
                where[name] = f"{alias_sql}.{_q(c)}"
        return f"SELECT {', '.join(select_cols)} FROM {from_sql}"
    spec = source["union"]
    parts = []
    for d in spec["datasets"]:
        ds = db.get(Dataset, d)
        extra = f", {_lit(ds.name)} AS {_q(spec['source_column'])}" if spec["source_column"] else ""
        parts.append(f"SELECT *{extra} FROM {_path(db, d)}")
    return " UNION ALL BY NAME ".join(parts)


def extract(db: Session, dataset: Dataset, batch_size: int = 100_000) -> Iterator[pa.RecordBatch]:
    sql = build_query(db, dataset)
    con = duckdb.connect()
    try:
        try:
            rel = con.sql(sql)
            to_reader = getattr(rel, "to_arrow_reader", None) or rel.fetch_record_batch
            reader = to_reader(batch_size)
        except duckdb.Error as exc:
            raise ConnectorError(f"Could not combine the datasets: {str(exc).splitlines()[0]}") from exc
        yielded = False
        for batch in reader:
            yield batch
            yielded = True
        if not yielded:
            yield pa.RecordBatch.from_arrays(
                [pa.array([], f.type) for f in reader.schema], schema=reader.schema
            )
    finally:
        con.close()
