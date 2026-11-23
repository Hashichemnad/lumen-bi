"""Versions (roll back), KPI status, comments and annotations, column masking and data checks.

GET  /dashboards/{id}/versions                  earlier versions (people who can edit)
POST /dashboards/{id}/versions/{vid}/restore    roll back (the current state is kept as a version)
GET  /charts/{id}/versions, POST /charts/{id}/versions/{vid}/restore
POST /dashboards/{id}/kpis                      KPI goals: actual, target, status (viewer's own data)
GET/POST /dashboards/{id}/comments              comments and chart annotations (anyone who can view)
PATCH/DELETE /comments/{id}                     the author; resolve: also people who manage the dashboard
GET/PUT /datasets/{id}/masks                    column masking (dataset owner or admin)
GET/POST /datasets/{id}/checks, PUT/DELETE /checks/{id}, POST /datasets/{id}/checks/run
"""

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, dashboard_viewer, require_role
from app.db import get_db
from app.models import (
    Chart,
    ColumnMask,
    Comment,
    Dashboard,
    DataCheck,
    Dataset,
    Group,
    ItemVersion,
    Role,
    User,
)
from app.query import masking, rls
from app.query.spec import QuerySpec
from app.services import audit, checks, kpi, versions
from app.services import dashboards as svc

router = APIRouter(tags=["governance"])
require_creator = require_role(Role.creator)


# ------------------------------------------------------------------ versions
def _version_out(db: Session, v: ItemVersion) -> dict:
    who = db.get(User, v.saved_by_id) if v.saved_by_id else None
    return {
        "id": v.id,
        "name": v.name,
        "saved_at": v.created_at,
        "saved_by": (who.name or who.email) if who else "",
        "tiles": len(svc.items(v.config or {})) if v.kind == "dashboard" else None,
    }


def _editable_dashboard(db: Session, dashboard_id: int, user: User) -> Dashboard:
    d = db.get(Dashboard, dashboard_id)
    level = svc.share_level(db, user, d) if d else None
    if d is None or level is None:
        raise HTTPException(404, "Dashboard not found")
    if not svc.can_edit(level):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can view this dashboard but not change it")
    return d


def _editable_chart(db: Session, chart_id: int, user: User) -> Chart:
    c = db.get(Chart, chart_id)
    level = svc.chart_level(db, user, c) if c else None
    if c is None or level is None:
        raise HTTPException(404, "Chart not found")
    if level not in {"edit", "manage"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can open this chart but not change it")
    return c


def _version(db: Session, kind: str, item_id: int, version_id: int) -> ItemVersion:
    v = db.get(ItemVersion, version_id)
    if v is None or v.kind != kind or v.item_id != item_id:
        raise HTTPException(404, "Version not found")
    return v


@router.get("/dashboards/{dashboard_id}/versions")
def dashboard_versions(dashboard_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    d = _editable_dashboard(db, dashboard_id, user)
    return [_version_out(db, v) for v in versions.history(db, d)]


@router.post("/dashboards/{dashboard_id}/versions/{version_id}/restore")
def restore_dashboard(
    dashboard_id: int,
    version_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    d = _editable_dashboard(db, dashboard_id, user)
    v = _version(db, "dashboard", d.id, version_id)
    try:
        config = svc.validate_config(db, user, v.config or {}, d.config)
    except svc.DashboardError as exc:
        raise HTTPException(exc.status, f"This version cannot be restored: {exc.message}") from exc
    name, description, saved_at = v.name, v.description, v.created_at
    db.delete(v)
    versions.keep_before_change(db, d, user, force=True)
    d.config, d.name, d.description, d.updated_at = config, name, description, datetime.now(UTC)
    audit.record(
        db,
        "dashboard.restore",
        user=user,
        request=request,
        object_type="dashboard",
        object_id=d.id,
        details={"name": d.name, "version_saved_at": saved_at.isoformat()},
        commit=False,
    )
    db.commit()
    return {"ok": True}


@router.get("/charts/{chart_id}/versions")
def chart_versions(chart_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    c = _editable_chart(db, chart_id, user)
    return [_version_out(db, v) for v in versions.history(db, c)]


@router.post("/charts/{chart_id}/versions/{version_id}/restore")
def restore_chart(
    chart_id: int,
    version_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    c = _editable_chart(db, chart_id, user)
    v = _version(db, "chart", c.id, version_id)
    ds = db.get(Dataset, v.dataset_id or c.dataset_id)
    if ds is None or not rls.can_query(db, user, ds):
        raise HTTPException(422, "This version uses a dataset you cannot use")
    try:
        QuerySpec(**{**(v.query or {}), "dataset": ds.id})
    except ValueError as exc:
        raise HTTPException(422, "This version cannot be restored: its query is no longer valid") from exc
    snapshot = (v.name, v.description, v.config, v.query, ds.id, v.created_at)
    db.delete(v)
    versions.keep_before_change(db, c, user, force=True)
    c.name, c.description, c.config, c.query, c.dataset_id, saved_at = snapshot
    audit.record(
        db,
        "chart.restore",
        user=user,
        request=request,
        object_type="chart",
        object_id=c.id,
        details={"name": c.name, "version_saved_at": saved_at.isoformat()},
        commit=False,
    )
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ KPI goals
class KpiIn(BaseModel):
    filters: dict[str, list[dict[str, Any]]] = {}  # chart tile id -> its current filters


@router.post("/dashboards/{dashboard_id}/kpis")
def dashboard_kpis(
    dashboard_id: int, body: KpiIn, db: Session = Depends(get_db), user: User = Depends(dashboard_viewer)
):
    d = db.get(Dashboard, dashboard_id)
    if d is None or svc.share_level(db, user, d) is None:
        raise HTTPException(404, "Dashboard not found")
    return kpi.evaluate(db, d, rls.principal_for(db, user), body.filters)


# ------------------------------------------------------------------ comments and annotations
class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    tile_id: str | None = Field(None, max_length=40)
    parent_id: int | None = None
    anchor: dict[str, Any] | None = None  # {"label": "Mar 2026"} pins it to a point of the chart


class CommentPatch(BaseModel):
    body: str | None = Field(None, min_length=1, max_length=5000)
    resolved: bool | None = None


def _viewable(db: Session, dashboard_id: int, user: User) -> tuple[Dashboard, str]:
    d = db.get(Dashboard, dashboard_id)
    level = svc.share_level(db, user, d) if d else None
    if d is None or level is None:
        raise HTTPException(404, "Dashboard not found")
    return d, level


def _comment_out(db: Session, c: Comment, user: User, level: str) -> dict:
    author = db.get(User, c.user_id) if c.user_id else None
    return {
        "id": c.id,
        "tile_id": c.tile_id,
        "parent_id": c.parent_id,
        "body": c.body,
        "anchor": c.anchor,
        "resolved": c.resolved,
        "author": (author.name or author.email) if author else "",
        "author_id": c.user_id,
        "created_at": c.created_at,
        "edited_at": c.edited_at,
        "can_edit": c.user_id == user.id,
        "can_delete": c.user_id == user.id or level == "manage",
    }


@router.get("/dashboards/{dashboard_id}/comments")
def list_comments(dashboard_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    d, level = _viewable(db, dashboard_id, user)
    rows = db.scalars(
        select(Comment).where(Comment.dashboard_id == d.id).order_by(Comment.created_at, Comment.id)
    )
    return [_comment_out(db, c, user, level) for c in rows]


@router.post("/dashboards/{dashboard_id}/comments", status_code=status.HTTP_201_CREATED)
def add_comment(
    dashboard_id: int, body: CommentIn, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    d, level = _viewable(db, dashboard_id, user)
    if body.tile_id and body.tile_id not in {i["id"] for i in svc.items(d.config or {})}:
        raise HTTPException(422, "That tile is not on this dashboard")
    if body.parent_id is not None:
        parent = db.get(Comment, body.parent_id)
        if parent is None or parent.dashboard_id != d.id or parent.parent_id is not None:
            raise HTTPException(422, "Reply to a comment on this dashboard")
    anchor = None
    if body.anchor:
        label = str(body.anchor.get("label") or "")[:200]
        if not label or not body.tile_id:
            raise HTTPException(422, "An annotation needs a chart and a point on it")
        anchor = {"label": label}
    c = Comment(
        dashboard_id=d.id,
        tile_id=body.tile_id,
        parent_id=body.parent_id,
        user_id=user.id,
        body=body.body.strip(),
        anchor=anchor,
    )
    db.add(c)
    db.commit()
    return _comment_out(db, c, user, level)


@router.patch("/comments/{comment_id}")
def change_comment(
    comment_id: int, body: CommentPatch, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    c = db.get(Comment, comment_id)
    if c is None:
        raise HTTPException(404, "Comment not found")
    _, level = _viewable(db, c.dashboard_id, user)
    if body.body is not None:
        if c.user_id != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the author can change a comment")
        c.body, c.edited_at = body.body.strip(), datetime.now(UTC)
    if body.resolved is not None:
        if c.user_id != user.id and level != "manage":
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Only the author or a manager can resolve a comment"
            )
        c.resolved = body.resolved
    db.commit()
    return _comment_out(db, c, user, level)


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_comment(comment_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    c = db.get(Comment, comment_id)
    if c is None:
        raise HTTPException(404, "Comment not found")
    _, level = _viewable(db, c.dashboard_id, user)
    if c.user_id != user.id and level != "manage":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the author or a manager can delete a comment")
    db.delete(c)
    db.commit()


# ------------------------------------------------------------------ column masking
class MaskIn(BaseModel):
    column: str
    method: Literal["full", "partial", "hash"] = "full"
    exempt_groups: list[int] = []


def _manageable_dataset(db: Session, dataset_id: int, user: User) -> Dataset:
    ds = db.get(Dataset, dataset_id)
    if ds is None:
        raise HTTPException(404, "Dataset not found")
    if not (user.has_role(Role.admin) or ds.owner_id == user.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the dataset's owner or an admin can change this")
    return ds


@router.get("/datasets/{dataset_id}/masks")
def get_masks(dataset_id: int, db: Session = Depends(get_db), user: User = Depends(require_creator)):
    ds = _manageable_dataset(db, dataset_id, user)
    own = {m.column for m in db.scalars(select(ColumnMask).where(ColumnMask.dataset_id == ds.id))}
    return [
        {
            "column": m.column,
            "method": m.method,
            "exempt_groups": m.exempt_groups,
            "inherited_from": None if m.origin.id == ds.id else {"id": m.origin.id, "name": m.origin.name},
        }
        for m in masking.effective_masks(db, ds)
        if m.origin.id != ds.id or m.column in own
    ]


@router.put("/datasets/{dataset_id}/masks")
def set_masks(
    dataset_id: int,
    body: list[MaskIn],
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    ds = _manageable_dataset(db, dataset_id, user)
    columns = {c["name"] for c in ds.columns or []}
    wanted: dict[str, MaskIn] = {}
    for m in body:
        if m.column not in columns:
            raise HTTPException(422, f'"{m.column}" is not a column of this dataset')
        for gid in m.exempt_groups:
            if db.get(Group, gid) is None:
                raise HTTPException(422, f"Unknown group {gid}")
        wanted[m.column] = m
    for old in db.scalars(select(ColumnMask).where(ColumnMask.dataset_id == ds.id)):
        db.delete(old)
    db.flush()
    for m in wanted.values():
        db.add(
            ColumnMask(
                dataset_id=ds.id,
                column=m.column,
                method=m.method,
                exempt_groups=sorted(set(m.exempt_groups)),
                created_by_id=user.id,
            )
        )
    audit.record(
        db,
        "dataset.masks",
        user=user,
        request=request,
        object_type="dataset",
        object_id=ds.id,
        details={"masks": [m.model_dump() for m in wanted.values()]},
        commit=False,
    )
    db.commit()
    return get_masks(dataset_id, db, user)


# ------------------------------------------------------------------ data checks
class CheckIn(BaseModel):
    kind: Literal["freshness", "row_count", "not_null", "unique", "range", "allowed"]
    config: dict[str, Any] = {}
    severity: Literal["warning", "error"] = "warning"
    enabled: bool = True


def _check_out(c: DataCheck) -> dict:
    return {
        "id": c.id,
        "kind": c.kind,
        "config": c.config,
        "severity": c.severity,
        "enabled": c.enabled,
        "description": checks.describe(c),
        "last_status": c.last_status,
        "last_value": c.last_value,
        "last_message": c.last_message,
        "last_run_at": c.last_run_at,
    }


@router.get("/datasets/{dataset_id}/checks")
def list_checks(dataset_id: int, db: Session = Depends(get_db), user: User = Depends(require_creator)):
    ds = db.get(Dataset, dataset_id)
    if ds is None:
        raise HTTPException(404, "Dataset not found")
    rows = db.scalars(select(DataCheck).where(DataCheck.dataset_id == ds.id).order_by(DataCheck.id))
    return [_check_out(c) for c in rows]


@router.post("/datasets/{dataset_id}/checks", status_code=status.HTTP_201_CREATED)
def add_check(
    dataset_id: int,
    body: CheckIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    ds = _manageable_dataset(db, dataset_id, user)
    try:
        config = checks.validate(ds, body.kind, body.config)
    except checks.CheckError as exc:
        raise HTTPException(422, str(exc)) from exc
    c = DataCheck(
        dataset_id=ds.id,
        kind=body.kind,
        config=config,
        severity=body.severity,
        enabled=body.enabled,
        created_by_id=user.id,
    )
    db.add(c)
    db.flush()
    audit.record(
        db,
        "dataset.check",
        user=user,
        request=request,
        object_type="dataset",
        object_id=ds.id,
        details={"added": checks.describe(c)},
        commit=False,
    )
    db.commit()
    checks.run_checks(db, ds)
    db.refresh(c)
    return _check_out(c)


@router.put("/checks/{check_id}")
def change_check(
    check_id: int, body: CheckIn, db: Session = Depends(get_db), user: User = Depends(require_creator)
):
    c = db.get(DataCheck, check_id)
    if c is None:
        raise HTTPException(404, "Check not found")
    ds = _manageable_dataset(db, c.dataset_id, user)
    try:
        c.config = checks.validate(ds, body.kind, body.config)
    except checks.CheckError as exc:
        raise HTTPException(422, str(exc)) from exc
    c.kind, c.severity, c.enabled, c.last_status = body.kind, body.severity, body.enabled, "never"
    db.commit()
    checks.run_checks(db, ds)
    db.refresh(c)
    return _check_out(c)


@router.delete("/checks/{check_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_check(check_id: int, db: Session = Depends(get_db), user: User = Depends(require_creator)):
    c = db.get(DataCheck, check_id)
    if c is None:
        raise HTTPException(404, "Check not found")
    _manageable_dataset(db, c.dataset_id, user)
    db.delete(c)
    db.commit()


@router.post("/datasets/{dataset_id}/checks/run")
def run_dataset_checks(dataset_id: int, db: Session = Depends(get_db), user: User = Depends(require_creator)):
    ds = db.get(Dataset, dataset_id)
    if ds is None:
        raise HTTPException(404, "Dataset not found")
    return [_check_out(c) for c in checks.run_checks(db, ds)]
