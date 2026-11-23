"""Running syncs: one SyncRun per attempt; runs in a Celery worker (or inline in development/tests)."""

import json
import logging
import threading
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors import Connector, ConnectorError, connector_class
from app.core.config import get_settings
from app.core.crypto import decrypt, encrypt
from app.db import SessionLocal
from app.models import Connection, Dataset, SyncRun, User
from app.services import snapshots
from app.services.email import send_email

log = logging.getLogger("lumen.sync")
STALE_RUN = timedelta(hours=6)  # a "running" run older than this is treated as crashed


# ------------------------------------------------------------------ connection helpers
def secrets_of(conn: Connection) -> dict[str, str]:
    return json.loads(decrypt(conn.secrets_enc)) if conn.secrets_enc else {}


def store_secrets(conn: Connection, secrets: dict[str, str]) -> None:
    conn.secrets_enc = encrypt(json.dumps(secrets)) if secrets else ""


def build_connector(conn: Connection | None) -> Connector:
    if conn is None:
        raise ConnectorError("This dataset has no connection.")
    return connector_class(conn.type)(conn.config or {}, secrets_of(conn))


# ------------------------------------------------------------------ schedules
def next_run(cron: str | None, after: datetime | None = None) -> datetime | None:
    if not cron:
        return None
    tz = ZoneInfo(get_settings().timezone)
    base = (after or datetime.now(UTC)).astimezone(tz)
    return croniter(cron, base).get_next(datetime).astimezone(UTC)


def valid_cron(cron: str) -> bool:
    return croniter.is_valid(cron) and len(cron.split()) == 5


# ------------------------------------------------------------------ queueing
def active_run(db: Session, dataset_id: int) -> SyncRun | None:
    cutoff = datetime.now(UTC) - STALE_RUN
    return db.scalar(
        select(SyncRun)
        .where(
            SyncRun.dataset_id == dataset_id,
            SyncRun.status.in_(["queued", "running"]),
            SyncRun.created_at > cutoff,
        )
        .order_by(SyncRun.id.desc())
    )


def enqueue(db: Session, dataset: Dataset, trigger: str, user: User | None = None) -> SyncRun:
    """Create a queued run and hand it to the job runner. Returns the existing run if one is active."""
    existing = active_run(db, dataset.id)
    if existing:
        return existing
    run = SyncRun(
        dataset_id=dataset.id, trigger=trigger, mode=dataset.sync_mode, requested_by=user.id if user else None
    )
    db.add(run)
    db.commit()
    dispatch(run.id)
    return run


def dispatch(run_id: int) -> None:
    mode = get_settings().jobs_mode
    if mode == "eager":
        execute(run_id)
    elif mode == "inline":
        threading.Thread(target=execute, args=(run_id,), daemon=True).start()
    else:
        from app.worker import sync_dataset

        sync_dataset.delay(run_id)


# ------------------------------------------------------------------ the sync itself
def execute(run_id: int) -> None:
    """Run one sync. Never raises: the outcome is recorded on the SyncRun."""
    with SessionLocal() as db:
        run = db.get(SyncRun, run_id)
        if run is None or run.status != "queued":
            return
        dataset = db.get(Dataset, run.dataset_id)
        if dataset is None:
            return
        run.status, run.started_at = "running", datetime.now(UTC)
        db.commit()
        try:
            _sync(db, dataset, run)
        except Exception as exc:
            db.rollback()
            message = (
                str(exc) if isinstance(exc, ConnectorError) else f"Unexpected error ({type(exc).__name__})"
            )
            if not isinstance(exc, ConnectorError):
                log.exception("Sync of dataset %s failed", dataset.id)
            run = db.get(SyncRun, run_id)
            dataset = db.get(Dataset, run.dataset_id)
            run.status, run.error, run.finished_at = "failed", message, datetime.now(UTC)
            dataset.last_status = "failed"
            db.commit()
            _notify_failure(db, dataset, run)


def _sync(db: Session, dataset: Dataset, run: SyncRun) -> None:
    from app.semantic import derived
    from app.semantic.model import reconcile_fields

    current = snapshots.snapshot_path(dataset.id, dataset.current_version)
    if dataset.kind in derived.DERIVED_KINDS:
        # Joins and unions are rebuilt in full from their parents' current snapshots
        run.mode = "full"
        db.commit()
        batches = derived.extract(db, dataset)
    else:
        connector = build_connector(dataset.connection)
        incremental = (
            dataset.sync_mode == "incremental"
            and dataset.incremental_column
            and current is not None
            and connector.supports_incremental
        )
        since = snapshots.max_value(current, dataset.incremental_column) if incremental else None
        run.mode = "incremental" if incremental and since is not None else "full"
        db.commit()
        batches = connector.extract(
            dataset.source,
            since=since if run.mode == "incremental" else None,
            incremental_column=dataset.incremental_column,
            # With a primary key, re-reading rows at the last timestamp is safe (they replace themselves)
            # and avoids missing rows written in the same second as the previous sync
            inclusive=bool(dataset.primary_key),
        )
    result = snapshots.write_snapshot(
        dataset.id,
        batches,
        merge_with=current if run.mode == "incremental" else None,
        primary_key=dataset.primary_key,
    )
    now = datetime.now(UTC)
    if result is None:  # incremental: nothing new
        run.details = {"note": "No new or changed rows"}
        run.rows_total = dataset.row_count
    else:
        changes = snapshots.schema_changes(dataset.columns or [], result.columns) if dataset.columns else {}
        if changes:
            run.details = {"schema_changes": changes}
        run.version, run.rows_read, run.rows_total = result.version, result.rows_read, result.rows_total
        dataset.current_version = result.version  # the switch: queries now read the new snapshot
        dataset.row_count = result.rows_total
        dataset.columns = [c.dict() for c in result.columns]
    run.status, run.finished_at = "success", now
    dataset.last_status, dataset.last_synced_at = "success", now
    reconcile_fields(db, dataset)  # new columns get fields; labels/formats people set are kept
    db.commit()
    snapshots.prune(dataset.id, dataset.current_version)
    if result is not None:
        # Joined/combined datasets built on this one are refreshed with the new data
        for child in derived.dependents(db, dataset.id):
            enqueue(db, child, "upstream")


def _notify_failure(db: Session, dataset: Dataset, run: SyncRun) -> None:
    owner = db.get(User, dataset.owner_id) if dataset.owner_id else None
    if not owner or not owner.is_active:
        return
    s = get_settings()
    try:
        send_email(
            owner.email,
            f"Data sync failed: {dataset.name}",
            f'The sync of dataset "{dataset.name}" failed at {run.finished_at:%Y-%m-%d %H:%M} UTC.\n\n'
            f"Reason: {run.error}\n\nOpen it: {s.public_url.rstrip('/')}/data/datasets/{dataset.id}\n",
        )
    except Exception:
        log.exception("Could not send sync failure email")


# ------------------------------------------------------------------ scheduler (called every minute by beat)
def dispatch_due(now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    started = 0
    with SessionLocal() as db:
        due = db.scalars(
            select(Dataset).where(Dataset.schedule.is_not(None), Dataset.next_run_at <= now)
        ).all()
        for ds in due:
            ds.next_run_at = next_run(ds.schedule, now)
            db.commit()
            if active_run(db, ds.id) is None:
                enqueue(db, ds, "schedule")
                started += 1
    return started
