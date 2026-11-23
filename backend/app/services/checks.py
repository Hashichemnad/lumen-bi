"""Data quality and freshness checks. They run on the dataset's whole current snapshot (not filtered by
anyone's row-level security), after every successful sync, on demand, and (freshness) every 15 minutes.
When a check starts failing, the dataset's owner gets an email; dashboards show the problem."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import SessionLocal
from app.models import DataCheck, Dataset, User
from app.query import duck
from app.services import snapshots
from app.services.email import send_email

log = logging.getLogger("lumen.checks")
KINDS = {"freshness", "row_count", "not_null", "unique", "range", "allowed"}


class CheckError(Exception):
    pass


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def validate(ds: Dataset, kind: str, config: dict) -> dict:
    """Clean a check's settings; columns must be columns of the dataset."""
    if kind not in KINDS:
        raise CheckError("Unknown kind of check")
    columns = {c["name"]: c.get("type", "") for c in ds.columns or []}

    def column(required: bool = True) -> str | None:
        name = config.get("column") or None
        if name is None and not required:
            return None
        if name not in columns:
            raise CheckError("Choose a column of this dataset")
        return name

    def number(key: str, required: bool = False) -> float | None:
        v = config.get(key)
        if v in (None, ""):
            if required:
                raise CheckError("Enter a number")
            return None
        try:
            return float(v)
        except (TypeError, ValueError) as exc:
            raise CheckError("Enter a number") from exc

    if kind == "freshness":
        hours = number("hours", True)
        if not hours or hours <= 0:
            raise CheckError("Enter how many hours old the data may be")
        return {"hours": hours, "column": column(False)}
    if kind == "row_count":
        lo, hi = number("min"), number("max")
        if lo is None and hi is None:
            raise CheckError("Enter a minimum, a maximum or both")
        return {"min": lo, "max": hi}
    if kind == "not_null":
        pct = number("max_percent") or 0
        return {"column": column(), "max_percent": max(0.0, min(100.0, pct))}
    if kind == "unique":
        return {"column": column()}
    if kind == "range":
        lo, hi = number("min"), number("max")
        if lo is None and hi is None:
            raise CheckError("Enter a minimum, a maximum or both")
        return {"column": column(), "min": lo, "max": hi}
    values = [str(v) for v in (config.get("values") or []) if str(v).strip()][:500]
    if not values:
        raise CheckError("List the allowed values")
    return {"column": column(), "values": values}


def _scalar(sql: str, params: list | None = None) -> Any:
    _, rows = duck.run(sql, params or [])
    return rows[0][0] if rows else None


def evaluate(ds: Dataset, check: DataCheck, now: datetime | None = None) -> tuple[str, str, str]:
    """(status, value, message) for one check."""
    now = now or datetime.now(UTC)
    cfg = check.config or {}
    path = snapshots.snapshot_path(ds.id, ds.current_version)
    if check.kind == "freshness" and not cfg.get("column"):
        if not ds.last_synced_at:
            return "fail", "never", "The data has never been synced"
        synced = ds.last_synced_at if ds.last_synced_at.tzinfo else ds.last_synced_at.replace(tzinfo=UTC)
        age = (now - synced).total_seconds() / 3600
        ok = age <= cfg["hours"]
        return ("pass" if ok else "fail"), f"{age:.1f} h", "" if ok else f"Last synced {age:.0f} hours ago"
    if path is None:
        return "fail", "", "There is no data yet"
    src = f"read_parquet({snapshots._lit(path)})"
    col = _q(cfg["column"]) if cfg.get("column") else None
    if check.kind == "freshness":
        newest = _scalar(f"SELECT max(CAST({col} AS TIMESTAMP)) FROM {src}")
        if newest is None:
            return "fail", "", "The column has no dates"
        newest = newest if newest.tzinfo else newest.replace(tzinfo=UTC)
        age = (now - newest).total_seconds() / 3600
        ok = age <= cfg["hours"]
        return (
            ("pass" if ok else "fail"),
            f"{age:.1f} h",
            "" if ok else f"The newest row is {age:.0f} hours old",
        )
    if check.kind == "row_count":
        n = int(_scalar(f"SELECT count(*) FROM {src}") or 0)
        bad = (cfg.get("min") is not None and n < cfg["min"]) or (
            cfg.get("max") is not None and n > cfg["max"]
        )
        return ("fail" if bad else "pass"), f"{n:,}", f"{n:,} rows" if bad else ""
    if check.kind == "not_null":
        total, nulls = duck.run(f"SELECT count(*), count(*) - count({col}) FROM {src}", [])[1][0]
        pct = (nulls / total * 100) if total else 0.0
        bad = pct > cfg["max_percent"]
        return (
            ("fail" if bad else "pass"),
            f"{pct:.1f}%",
            f"{nulls:,} empty values ({pct:.1f}%)" if bad else "",
        )
    if check.kind == "unique":
        dup = int(_scalar(f"SELECT count(*) - count(DISTINCT {col}) FROM {src} WHERE {col} IS NOT NULL") or 0)
        return ("fail" if dup else "pass"), f"{dup:,}", f"{dup:,} repeated values" if dup else ""
    if check.kind == "range":
        conds = []
        params: list[Any] = []
        if cfg.get("min") is not None:
            conds.append(f"TRY_CAST({col} AS DOUBLE) < ?")
            params.append(cfg["min"])
        if cfg.get("max") is not None:
            conds.append(f"TRY_CAST({col} AS DOUBLE) > ?")
            params.append(cfg["max"])
        n = int(_scalar(f"SELECT count(*) FROM {src} WHERE {' OR '.join(conds)}", params) or 0)
        return ("fail" if n else "pass"), f"{n:,}", f"{n:,} values out of range" if n else ""
    # allowed values
    placeholders = ", ".join("?" for _ in cfg["values"])
    n = int(
        _scalar(
            f"SELECT count(*) FROM {src} "
            f"WHERE {col} IS NOT NULL AND CAST({col} AS VARCHAR) NOT IN ({placeholders})",
            list(cfg["values"]),
        )
        or 0
    )
    return ("fail" if n else "pass"), f"{n:,}", f"{n:,} rows with other values" if n else ""


def run_checks(db: Session, ds: Dataset, kinds: set[str] | None = None) -> list[DataCheck]:
    """Run the dataset's checks; email the owner about checks that start failing."""
    newly_failed = []
    checks = db.scalars(
        select(DataCheck).where(DataCheck.dataset_id == ds.id, DataCheck.enabled.is_(True))
    ).all()
    for c in checks:
        if kinds and c.kind not in kinds:
            continue
        before = c.last_status
        try:
            status, value, message = evaluate(ds, c)
        except Exception as exc:  # noqa: BLE001 - a broken check must not stop the others
            log.exception("Check %s failed to run", c.id)
            status, value, message = "error", "", f"The check could not run ({type(exc).__name__})"
        c.last_status, c.last_value, c.last_message, c.last_run_at = status, value, message, datetime.now(UTC)
        if status in {"fail", "error"} and before not in {"fail", "error"}:
            newly_failed.append(c)
    db.commit()
    if newly_failed:
        _notify(db, ds, newly_failed)
    return list(checks)


def describe(c: DataCheck) -> str:
    cfg = c.config or {}
    col = cfg.get("column")
    if c.kind == "freshness":
        return f"Data at most {cfg.get('hours', 0):g} hours old" + (f" ({col})" if col else "")
    if c.kind == "row_count":
        lo, hi = cfg.get("min"), cfg.get("max")
        return (
            "Number of rows"
            + (f" ≥ {lo:g}" if lo is not None else "")
            + (f" ≤ {hi:g}" if hi is not None else "")
        )
    if c.kind == "not_null":
        return f"{col}: at most {cfg.get('max_percent', 0):g}% empty"
    if c.kind == "unique":
        return f"{col}: no repeated values"
    if c.kind == "range":
        lo, hi = cfg.get("min"), cfg.get("max")
        return f"{col} between {'…' if lo is None else f'{lo:g}'} and {'…' if hi is None else f'{hi:g}'}"
    if c.kind == "allowed":
        values = cfg.get("values", [])
        return f"{col}: only {', '.join(values[:5])}" + ("…" if len(values) > 5 else "")
    return c.kind


def _notify(db: Session, ds: Dataset, failed: list[DataCheck]) -> None:
    owner = db.get(User, ds.owner_id) if ds.owner_id else None
    if not owner or not owner.is_active:
        return
    lines = "\n".join(f"- {describe(c)}: {c.last_message}" for c in failed)
    url = f"{get_settings().public_url.rstrip('/')}/data/datasets/{ds.id}"
    try:
        send_email(
            owner.email,
            f"Data check failed: {ds.name}",
            f'Checks on "{ds.name}" failed:\n\n{lines}\n\n{url}\n',
        )
    except Exception:
        log.exception("Could not send check failure email")


def summary(db: Session, dataset_ids: list[int]) -> dict[int, dict]:
    """Per dataset: number of checks and failing ones (for dashboards)."""
    out: dict[int, dict] = {}
    if not dataset_ids:
        return out
    for c in db.scalars(
        select(DataCheck).where(DataCheck.dataset_id.in_(dataset_ids), DataCheck.enabled.is_(True))
    ):
        s = out.setdefault(c.dataset_id, {"checks": 0, "failing": 0, "messages": []})
        s["checks"] += 1
        if c.last_status in {"fail", "error"}:
            s["failing"] += 1
            s["messages"].append(f"{describe(c)}: {c.last_message}")
    return out


def run_freshness(now: datetime | None = None) -> int:
    """Every 15 minutes: freshness checks can start failing without any sync."""
    n = 0
    with SessionLocal() as db:
        ids = set(
            db.scalars(
                select(DataCheck.dataset_id).where(DataCheck.kind == "freshness", DataCheck.enabled.is_(True))
            )
        )
        for ds_id in ids:
            ds = db.get(Dataset, ds_id)
            if ds is not None:
                run_checks(db, ds, {"freshness"})
                n += 1
    return n


STALE = timedelta(minutes=15)
