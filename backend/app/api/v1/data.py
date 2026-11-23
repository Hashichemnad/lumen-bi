"""Connections, datasets, sync runs and previews (Creators and Admins)."""

import re
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.connectors import REGISTRY, Connector, ConnectorError, connector_class
from app.connectors.files import FORMATS
from app.core.config import get_settings
from app.db import get_db
from app.models import Connection, Dataset, Role, SyncRun, User
from app.query import duck, engine, rls
from app.schemas import (
    ColumnOut,
    ConnectionIn,
    ConnectionOut,
    ConnectionTestIn,
    ConnectionTypeOut,
    DatasetIn,
    DatasetOut,
    DatasetRef,
    ObjectOut,
    PreviewOut,
    SyncRunOut,
)
from app.semantic import derived, model
from app.semantic.formula import FormulaError, quote_ident
from app.services import audit, snapshots
from app.services.sync import (
    active_run,
    build_connector,
    enqueue,
    next_run,
    secrets_of,
    store_secrets,
    valid_cron,
)

router = APIRouter(tags=["data"])
require_creator = require_role(Role.creator)
MIN_SCHEDULE_GAP = timedelta(minutes=5)


def _bad(message: str) -> HTTPException:
    return HTTPException(422, message)


def _can_edit(user: User, owner_id: int | None) -> None:
    if not (user.has_role(Role.admin) or owner_id == user.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner or an admin can change this")


# ------------------------------------------------------------------ connections
def _get_conn(db: Session, connection_id: int) -> Connection:
    conn = db.get(Connection, connection_id)
    if not conn:
        raise HTTPException(404, "Connection not found")
    return conn


def _conn_out(db: Session, conn: Connection) -> ConnectionOut:
    out = ConnectionOut.model_validate(conn)
    out.secrets_set = sorted(k for k, v in secrets_of(conn).items() if v)
    owner = db.get(User, conn.owner_id) if conn.owner_id else None
    out.owner_name = owner.name or owner.email if owner else ""
    out.dataset_count = db.scalar(
        select(func.count()).select_from(Dataset).where(Dataset.connection_id == conn.id)
    )
    return out


def _prepare(body: ConnectionIn) -> tuple[type[Connector], dict[str, Any], dict[str, str] | None]:
    try:
        cls = connector_class(body.type)
    except ConnectorError as exc:
        raise _bad(str(exc)) from exc
    if cls.type == "file":
        raise _bad("Use the upload button to add a file")
    try:
        config = cls.validate_config(body.config)
    except ConnectorError as exc:
        raise _bad(str(exc)) from exc
    secrets = None
    if body.secrets is not None:
        secrets = {k: v for k, v in body.secrets.items() if k in cls.secret_fields}
    return cls, config, secrets


@router.get("/connection-types", response_model=list[ConnectionTypeOut])
def connection_types(_: User = Depends(require_creator)):
    return [
        ConnectionTypeOut(
            type=c.type,
            label=c.label,
            supports_incremental=c.supports_incremental,
            supports_sql=c.supports_sql,
            secret_fields=list(c.secret_fields),
        )
        for c in REGISTRY.values()
    ]


@router.get("/data-settings")
def data_settings(_: User = Depends(require_creator)):
    s = get_settings()
    return {"timezone": s.timezone, "max_upload_mb": s.max_upload_mb}


@router.get("/connections", response_model=list[ConnectionOut])
def list_connections(db: Session = Depends(get_db), _: User = Depends(require_creator)):
    return [_conn_out(db, c) for c in db.scalars(select(Connection).order_by(Connection.name))]


@router.post("/connections/test")
def test_unsaved_connection(
    body: ConnectionTestIn, db: Session = Depends(get_db), user: User = Depends(require_creator)
):
    """Test settings before saving. For an existing connection, stored secrets are used unless new ones
    are given."""
    cls, config, secrets = _prepare(body)
    if secrets is None and body.connection_id:
        existing = _get_conn(db, body.connection_id)
        _can_edit(user, existing.owner_id)
        secrets = secrets_of(existing)
    try:
        cls(config, secrets or {}).test_connection()
    except ConnectorError as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": "Connected successfully"}


@router.post("/connections", response_model=ConnectionOut, status_code=status.HTTP_201_CREATED)
def create_connection(
    body: ConnectionIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_creator)
):
    cls, config, secrets = _prepare(body)
    conn = Connection(name=body.name.strip(), type=cls.type, config=config, owner_id=user.id)
    store_secrets(conn, secrets or {})
    db.add(conn)
    db.flush()
    audit.record(
        db,
        "connection.create",
        user=user,
        request=request,
        object_type="connection",
        object_id=conn.id,
        details={"type": conn.type, "name": conn.name},
    )
    return _conn_out(db, conn)


@router.get("/connections/{connection_id}", response_model=ConnectionOut)
def get_connection(connection_id: int, db: Session = Depends(get_db), _: User = Depends(require_creator)):
    return _conn_out(db, _get_conn(db, connection_id))


@router.put("/connections/{connection_id}", response_model=ConnectionOut)
def update_connection(
    connection_id: int,
    body: ConnectionIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    conn = _get_conn(db, connection_id)
    _can_edit(user, conn.owner_id)
    if conn.type == "file":
        conn.name = body.name.strip()
    else:
        if body.type != conn.type:
            raise _bad("The type of a connection cannot be changed")
        _, config, secrets = _prepare(body)
        conn.name, conn.config = body.name.strip(), config
        if secrets is not None:
            merged = {**secrets_of(conn), **{k: v for k, v in secrets.items() if v}}
            store_secrets(conn, merged)
    audit.record(
        db,
        "connection.update",
        user=user,
        request=request,
        object_type="connection",
        object_id=conn.id,
        details={"secrets_changed": body.secrets is not None},
    )
    return _conn_out(db, conn)


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connection(
    connection_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_creator)
):
    conn = _get_conn(db, connection_id)
    _can_edit(user, conn.owner_id)
    dataset_ids = [d.id for d in conn.datasets]
    used_by = {
        d.name for ds_id in dataset_ids for d in derived.dependents(db, ds_id) if d.id not in dataset_ids
    }
    if used_by:
        raise HTTPException(
            409,
            f"Datasets from this connection are used by {', '.join(sorted(used_by))}. "
            "Delete or change those first.",
        )
    upload_dir = Path(conn.config["path"]).parent if conn.type == "file" and conn.config.get("path") else None
    name = conn.name
    db.delete(conn)
    audit.record(
        db,
        "connection.delete",
        user=user,
        request=request,
        object_type="connection",
        object_id=connection_id,
        details={"name": name, "datasets_deleted": len(dataset_ids)},
    )
    for ds_id in dataset_ids:
        snapshots.delete_all(ds_id)
    if upload_dir and upload_dir.parent == _uploads_root():
        shutil.rmtree(upload_dir, ignore_errors=True)


@router.post("/connections/{connection_id}/test")
def test_connection(connection_id: int, db: Session = Depends(get_db), _: User = Depends(require_creator)):
    try:
        build_connector(_get_conn(db, connection_id)).test_connection()
    except ConnectorError as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": "Connected successfully"}


@router.get("/connections/{connection_id}/objects", response_model=list[ObjectOut])
def list_objects(connection_id: int, db: Session = Depends(get_db), _: User = Depends(require_creator)):
    try:
        return [ObjectOut(**o.dict()) for o in build_connector(_get_conn(db, connection_id)).list_objects()]
    except ConnectorError as exc:
        raise _bad(str(exc)) from exc


@router.post("/connections/{connection_id}/schema", response_model=list[ColumnOut])
def source_schema(
    connection_id: int,
    source: dict[str, str],
    db: Session = Depends(get_db),
    _: User = Depends(require_creator),
):
    """Columns of a table/sheet or custom query, for choosing the incremental column and key."""
    try:
        return [
            ColumnOut(**c.dict()) for c in build_connector(_get_conn(db, connection_id)).get_schema(source)
        ]
    except ConnectorError as exc:
        raise _bad(str(exc)) from exc


# ------------------------------------------------------------------ file uploads
SAFE_NAME = re.compile(r"[^A-Za-z0-9._ ()-]+")


def _uploads_root() -> Path:
    root = get_settings().data_dir.resolve() / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


async def _store_upload(file: UploadFile) -> dict[str, str]:
    original = Path(file.filename or "upload").name
    fmt = FORMATS.get(Path(original).suffix.lower())
    if not fmt:
        raise _bad("Upload a CSV, Excel (.xlsx) or Parquet file")
    limit = get_settings().max_upload_mb * 1024 * 1024
    folder = _uploads_root() / uuid.uuid4().hex
    folder.mkdir()
    target = folder / (SAFE_NAME.sub("_", original) or f"upload{Path(original).suffix}")
    size = 0
    try:
        with target.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise _bad(f"The file is larger than {get_settings().max_upload_mb} MB")
                out.write(chunk)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    config = {
        "filename": original,
        "path": str(target),
        "format": fmt,
        "size": size,
        "uploaded_at": datetime.now(UTC).isoformat(),
    }
    try:
        connector_class("file")(config, {}).list_objects()  # can we open it?
    except ConnectorError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise _bad(str(exc)) from exc
    return config


@router.post("/connections/upload", response_model=ConnectionOut, status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    config = await _store_upload(file)
    conn = Connection(name=name.strip() or config["filename"], type="file", config=config, owner_id=user.id)
    db.add(conn)
    db.flush()
    audit.record(
        db,
        "connection.upload",
        user=user,
        request=request,
        object_type="connection",
        object_id=conn.id,
        details={"filename": config["filename"], "size": config["size"]},
    )
    return _conn_out(db, conn)


@router.put("/connections/{connection_id}/file", response_model=ConnectionOut)
async def replace_file(
    connection_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    conn = _get_conn(db, connection_id)
    _can_edit(user, conn.owner_id)
    if conn.type != "file":
        raise _bad("Only file connections take uploads")
    old_dir = Path(conn.config.get("path", "")).parent
    conn.config = await _store_upload(file)
    audit.record(
        db,
        "connection.upload",
        user=user,
        request=request,
        object_type="connection",
        object_id=conn.id,
        details={"filename": conn.config["filename"], "replaced": True},
    )
    if old_dir.parent == _uploads_root():
        shutil.rmtree(old_dir, ignore_errors=True)
    for ds in conn.datasets:  # refresh every dataset built on this file
        enqueue(db, ds, "upload", user)
    return _conn_out(db, conn)


# ------------------------------------------------------------------ datasets
def _get_ds(db: Session, dataset_id: int) -> Dataset:
    ds = db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    return ds


def _ds_out(db: Session, ds: Dataset) -> DatasetOut:
    out = DatasetOut.model_validate(ds)
    if ds.connection is not None:
        out.connection_name, out.connection_type = ds.connection.name, ds.connection.type
    for pid in derived.parents(ds.source or {}):
        parent = db.get(Dataset, pid)
        if parent:
            out.parents.append(DatasetRef(id=parent.id, name=parent.name))
    out.dependents = [DatasetRef(id=d.id, name=d.name) for d in derived.dependents(db, ds.id)]
    owner = db.get(User, ds.owner_id) if ds.owner_id else None
    out.owner_name = owner.name or owner.email if owner else ""
    run = active_run(db, ds.id)
    out.active_run = SyncRunOut.model_validate(run) if run else None
    return out


def _validate_dataset(db: Session, body: DatasetIn, self_id: int | None = None) -> Connection | None:
    kind = derived.kind_of(body.source)
    if kind in derived.DERIVED_KINDS:
        # Built from other datasets: always a full rebuild, refreshed when a parent syncs
        try:
            body.source = derived.validate(db, body.source, self_id=self_id)
        except ConnectorError as exc:
            raise _bad(str(exc)) from exc
        body.connection_id, body.sync_mode, body.incremental_column = None, "full", None
        body.primary_key, body.schedule = [], None
        return None
    if body.connection_id is None:
        raise _bad("Choose a connection")
    conn = _get_conn(db, body.connection_id)
    cls = connector_class(conn.type)
    source = {
        k: v.strip()
        for k, v in body.source.items()
        if k in {"object", "sql"} and isinstance(v, str) and v.strip()
    }
    if len(source) != 1:
        raise _bad("Choose a table/sheet or enter a SQL query")
    if "sql" in source and not cls.supports_sql:
        raise _bad("This connection does not support SQL queries")
    body.source = source
    if body.sync_mode == "incremental":
        reason = cls.incremental_ready(conn.config or {})
        if reason:
            raise _bad(reason)
        if not body.incremental_column:
            raise _bad("Choose the column that tells which rows are new or changed (e.g. a modified date)")
    else:
        body.incremental_column = None
    if body.schedule is not None:
        body.schedule = body.schedule.strip() or None
    if body.schedule:
        if not valid_cron(body.schedule):
            raise _bad("The schedule is not a valid cron expression (5 fields)")
        first = next_run(body.schedule)
        if first and next_run(body.schedule, first) - first < MIN_SCHEDULE_GAP:
            raise _bad("Syncs can run at most every 5 minutes")
    return conn


@router.get("/datasets", response_model=list[DatasetOut])
def list_datasets(
    connection_id: int | None = None, db: Session = Depends(get_db), _: User = Depends(require_creator)
):
    stmt = select(Dataset).order_by(Dataset.name)
    if connection_id:
        stmt = stmt.where(Dataset.connection_id == connection_id)
    return [_ds_out(db, d) for d in db.scalars(stmt)]


@router.post("/datasets", response_model=DatasetOut, status_code=status.HTTP_201_CREATED)
def create_dataset(
    body: DatasetIn,
    request: Request,
    sync_now: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    _validate_dataset(db, body)
    ds = Dataset(
        name=body.name.strip(),
        description=body.description,
        kind=derived.kind_of(body.source),
        connection_id=body.connection_id,
        source=body.source,
        sync_mode=body.sync_mode,
        incremental_column=body.incremental_column,
        primary_key=body.primary_key,
        schedule=body.schedule,
        next_run_at=next_run(body.schedule),
        owner_id=user.id,
    )
    db.add(ds)
    db.flush()
    audit.record(
        db,
        "dataset.create",
        user=user,
        request=request,
        object_type="dataset",
        object_id=ds.id,
        details={"name": ds.name, "connection_id": ds.connection_id},
    )
    if sync_now:
        enqueue(db, ds, "manual", user)
        db.refresh(ds)
    return _ds_out(db, ds)


@router.get("/datasets/{dataset_id}", response_model=DatasetOut)
def get_dataset(dataset_id: int, db: Session = Depends(get_db), _: User = Depends(require_creator)):
    return _ds_out(db, _get_ds(db, dataset_id))


@router.put("/datasets/{dataset_id}", response_model=DatasetOut)
def update_dataset(
    dataset_id: int,
    body: DatasetIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    ds = _get_ds(db, dataset_id)
    _can_edit(user, ds.owner_id)
    if derived.kind_of(body.source) != ds.kind:
        raise _bad("A dataset cannot change between a source, a join and a union")
    if ds.kind == "source" and body.connection_id != ds.connection_id:
        raise _bad("A dataset cannot be moved to another connection")
    _validate_dataset(db, body, self_id=ds.id)
    reshaped = (
        body.source != ds.source
        or body.incremental_column != ds.incremental_column
        or body.primary_key != ds.primary_key
    )
    ds.name, ds.description, ds.source = body.name.strip(), body.description, body.source
    ds.sync_mode, ds.incremental_column, ds.primary_key = (
        body.sync_mode,
        body.incremental_column,
        body.primary_key,
    )
    if body.schedule != ds.schedule:
        ds.schedule, ds.next_run_at = body.schedule, next_run(body.schedule)
    audit.record(
        db,
        "dataset.update",
        user=user,
        request=request,
        object_type="dataset",
        object_id=ds.id,
        details={"reshaped": reshaped},
    )
    if reshaped and ds.current_version:
        # What is extracted changed: rebuild the snapshot from scratch rather than merging into the old one
        ds.current_version, ds.columns, ds.row_count = 0, [], 0
        db.commit()
        enqueue(db, ds, "settings", user)
    return _ds_out(db, ds)


@router.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(
    dataset_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_creator)
):
    ds = _get_ds(db, dataset_id)
    _can_edit(user, ds.owner_id)
    used_by = derived.dependents(db, ds.id)
    if used_by:
        names = ", ".join(f'"{d.name}"' for d in used_by)
        raise HTTPException(
            409,
            f"{names} {'is' if len(used_by) == 1 else 'are'} built from this dataset. "
            "Delete or change that first.",
        )
    guarded = rls.datasets_using_as_access_table(db, ds.id)
    if guarded:
        names = ", ".join(f'"{d.name}"' for d in guarded)
        raise HTTPException(
            409, f"This dataset is the access table for row-level security on {names}. Change that first."
        )
    name = ds.name
    db.delete(ds)
    audit.record(
        db,
        "dataset.delete",
        user=user,
        request=request,
        object_type="dataset",
        object_id=dataset_id,
        details={"name": name},
    )
    snapshots.delete_all(dataset_id)


@router.post("/datasets/{dataset_id}/sync", response_model=SyncRunOut, status_code=status.HTTP_202_ACCEPTED)
def sync_now(
    dataset_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_creator)
):
    ds = _get_ds(db, dataset_id)
    run = enqueue(db, ds, "manual", user)
    audit.record(db, "dataset.sync", user=user, request=request, object_type="dataset", object_id=ds.id)
    db.refresh(run)
    return SyncRunOut.model_validate(run)


@router.get("/datasets/{dataset_id}/runs", response_model=list[SyncRunOut])
def sync_runs(
    dataset_id: int,
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(require_creator),
):
    _get_ds(db, dataset_id)
    rows = db.scalars(
        select(SyncRun).where(SyncRun.dataset_id == dataset_id).order_by(SyncRun.id.desc()).limit(limit)
    )
    return [SyncRunOut.model_validate(r) for r in rows]


@router.get("/datasets/{dataset_id}/preview", response_model=PreviewOut)
def preview(
    dataset_id: int,
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    """First rows of the live snapshot, with row-level security applied (Admins see every row)."""
    ds = _get_ds(db, dataset_id)
    if not snapshots.snapshot_path(ds.id, ds.current_version):
        raise HTTPException(409, "This dataset has no data yet. Run a sync first.")
    try:
        with_base, params, ctx, restricted = engine.base_sql(db, ds, rls.principal_for(db, user))
    except engine.QueryError as exc:
        raise _bad(exc.message) from exc
    # Row-level calculated, group and bin fields are shown too (aggregates need a chart)
    extra = ""
    for f in model.fields_of(db, ds.id):
        if f.kind != "column" and not f.error and not f.is_aggregate:
            try:
                extra += f", {model.compile_field(ctx, f.name).sql} AS {quote_ident(f.name)}"
            except FormulaError:
                continue
    columns, rows = duck.run(f"{with_base} SELECT *{extra} FROM base LIMIT {int(limit)}", params)
    return PreviewOut(
        columns=columns,
        rows=[[model.jsonable(v) for v in r] for r in rows],
        version=ds.current_version,
        restricted=restricted,
    )
