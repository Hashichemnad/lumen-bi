"""Dashboards: tiles of charts, text, images and web pages with shared filters; sharing and favourites.

Anyone can open the dashboards shared with them (Viewers too). Explorers and up create dashboards; the owner,
an admin or a manager of its workspace manages sharing; people given "edit" (or editors of its workspace) can
change the layout.
"""

import base64
import csv
import io
import re
from datetime import UTC, datetime
from typing import Literal

from celery.exceptions import TimeoutError as CeleryTimeout
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, dashboard_viewer, require_role
from app.core.config import get_settings
from app.db import get_db
from app.models import (
    Chart,
    Dashboard,
    DashboardFavorite,
    DashboardShare,
    Group,
    Hierarchy,
    Parameter,
    Role,
    User,
    Workspace,
)
from app.query import duck, engine, rls
from app.schemas import (
    DashboardIn,
    DashboardQueryIn,
    DashboardSharing,
    DashboardSummary,
    MoveIn,
    ShareEntry,
)
from app.semantic import model
from app.services import audit, checks, excel, render, versions, workspaces
from app.services import dashboards as svc

router = APIRouter(prefix="/dashboards", tags=["dashboards"])
require_explorer = require_role(Role.explorer)


def _get(db: Session, dashboard_id: int, user: User) -> tuple[Dashboard, str]:
    dash = db.get(Dashboard, dashboard_id)
    level = svc.share_level(db, user, dash) if dash else None
    if dash is None or level is None:
        raise HTTPException(404, "Dashboard not found")
    return dash, level


def _bad(exc: svc.DashboardError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


def _favorites(db: Session, user: User) -> set[int]:
    return set(db.scalars(select(DashboardFavorite.dashboard_id).where(DashboardFavorite.user_id == user.id)))


def _summary(db: Session, d: Dashboard, level: str, favs: set[int]) -> DashboardSummary:
    owner = db.get(User, d.owner_id) if d.owner_id else None
    ws = db.get(Workspace, d.workspace_id) if d.workspace_id else None
    shared = (
        db.scalar(select(DashboardShare.id).where(DashboardShare.dashboard_id == d.id).limit(1)) is not None
    )
    return DashboardSummary(
        id=d.id,
        name=d.name,
        description=d.description,
        owner_id=d.owner_id,
        owner_name=(owner.name or owner.email) if owner else "",
        updated_at=d.updated_at,
        favorite=d.id in favs,
        level=level,
        shared=shared,
        tiles=len(svc.items(d.config or {})),
        workspace_id=d.workspace_id,
        workspace_name=ws.name if ws else "",
        layout=_layout(d.config or {}),
    )


def _layout(config: dict) -> list[dict]:
    """The first tab's tiles as boxes, for a small picture of the dashboard in lists."""
    tabs = config.get("tabs") or []
    out = []
    for item in (tabs[0].get("items", []) if tabs else [])[:40]:
        box = item.get("desktop") or {}
        if all(k in box for k in ("x", "y", "w", "h")):
            out.append({"kind": item.get("kind", "chart"), **{k: box[k] for k in ("x", "y", "w", "h")}})
    return out


def _detail(db: Session, d: Dashboard, level: str, user: User) -> dict:
    charts = {}
    for cid in svc.chart_ids(d.config or {}):
        c = db.get(Chart, cid)
        if c is not None:
            charts[cid] = {
                "id": c.id,
                "name": c.name,
                "dataset_id": c.dataset_id,
                "config": c.config,
                "can_edit": svc.chart_level(db, user, c) in {"edit", "manage"},
            }
    datasets = {}
    for ds in svc.datasets_of(db, d):
        fields = [f for f in model.fields_of(db, ds.id) if not f.missing and not f.error and not f.hidden]
        datasets[ds.id] = {
            "id": ds.id,
            "name": ds.name,
            "last_synced_at": ds.last_synced_at.isoformat() if ds.last_synced_at else None,
            "last_status": ds.last_status,
            "fields": [
                {
                    "name": f.name,
                    "label": f.label or f.name,
                    "role": f.role,
                    "data_type": f.data_type,
                    "format": f.format,
                    "is_aggregate": f.is_aggregate,
                    "default_agg": f.default_agg,
                }
                for f in fields
            ],
            "hierarchies": [
                {"id": h.id, "name": h.name, "levels": h.levels}
                for h in db.scalars(select(Hierarchy).where(Hierarchy.dataset_id == ds.id))
            ],
            "parameters": [
                {
                    "name": p.name,
                    "description": p.description,
                    "data_type": p.data_type,
                    "default_value": (p.default_value or {}).get("value"),
                    "allowed_values": p.allowed_values or [],
                }
                for p in db.scalars(select(Parameter).where(Parameter.dataset_id == ds.id))
            ],
        }
    return {
        **_summary(db, d, level, _favorites(db, user)).model_dump(mode="json"),
        "config": d.config,
        "charts": charts,
        "datasets": datasets,
        "can_edit": svc.can_edit(level),
        "can_manage": level == "manage",
        "quality": checks.summary(db, [int(k) for k in datasets]),
    }


# ------------------------------------------------------------------ list, create, open, change, delete
def _check_workspace(db: Session, user: User, workspace_id: int | None) -> None:
    if workspace_id is None:
        return
    if db.get(Workspace, workspace_id) is None or not workspaces.can_add_to(db, user, workspace_id):
        raise HTTPException(403, "You can add items only to workspaces where you are an editor or manager")


@router.get("", response_model=list[DashboardSummary])
def list_dashboards(
    workspace_id: int | None = None,  # 0 = personal (not in a workspace)
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    favs = _favorites(db, user)
    out = []
    stmt = select(Dashboard).order_by(Dashboard.updated_at.desc())
    if workspace_id is not None:
        stmt = stmt.where(
            Dashboard.workspace_id == workspace_id if workspace_id else Dashboard.workspace_id.is_(None)
        )
    for d in db.scalars(stmt):
        level = svc.share_level(db, user, d)
        if level:
            out.append(_summary(db, d, level, favs))
    return out


@router.post("", status_code=status.HTTP_201_CREATED)
def create_dashboard(
    body: DashboardIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_explorer)
):
    _check_workspace(db, user, body.workspace_id)
    try:
        config = svc.validate_config(db, user, body.config, None)
    except svc.DashboardError as exc:
        raise _bad(exc) from exc
    d = Dashboard(
        name=body.name.strip(),
        description=body.description,
        owner_id=user.id,
        workspace_id=body.workspace_id,
        config=config,
    )
    db.add(d)
    db.flush()
    audit.record(
        db,
        "dashboard.create",
        user=user,
        request=request,
        object_type="dashboard",
        object_id=d.id,
        details={"name": d.name},
        commit=False,
    )
    db.commit()
    return _detail(db, d, "manage", user)


@router.get("/{dashboard_id}")
def get_dashboard(
    dashboard_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(dashboard_viewer)
):
    d, level = _get(db, dashboard_id, user)
    if getattr(request.state, "embedded", False):
        level = "view"  # embedded: read only, whoever the user is
    out = _detail(db, d, level, user)
    out["embedded"] = getattr(request.state, "embedded", False)
    return out


@router.put("/{dashboard_id}")
def update_dashboard(
    dashboard_id: int,
    body: DashboardIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    d, level = _get(db, dashboard_id, user)
    if not svc.can_edit(level):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can view this dashboard but not change it")
    try:
        new_config = svc.validate_config(db, user, body.config, d.config)
    except svc.DashboardError as exc:
        raise _bad(exc) from exc
    if new_config != d.config or body.name.strip() != d.name or body.description != d.description:
        versions.keep_before_change(db, d, user)
    d.config = new_config
    d.name, d.description = body.name.strip(), body.description
    d.updated_at = datetime.now(UTC)
    audit.record(
        db,
        "dashboard.update",
        user=user,
        request=request,
        object_type="dashboard",
        object_id=d.id,
        details={"name": d.name},
        commit=False,
    )
    db.commit()
    return _detail(db, d, level, user)


@router.put("/{dashboard_id}/workspace")
def move_dashboard(
    dashboard_id: int,
    body: MoveIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_explorer),
):
    d, level = _get(db, dashboard_id, user)
    if level != "manage":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner or a workspace manager can move this")
    _check_workspace(db, user, body.workspace_id)
    before = d.workspace_id
    d.workspace_id = body.workspace_id
    audit.record(
        db,
        "dashboard.move",
        user=user,
        request=request,
        object_type="dashboard",
        object_id=d.id,
        details={"name": d.name, "from": before, "to": body.workspace_id},
        commit=False,
    )
    db.commit()
    return _detail(db, d, svc.share_level(db, user, d) or "view", user)


@router.post("/{dashboard_id}/copy", status_code=status.HTTP_201_CREATED)
def copy_dashboard(
    dashboard_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_explorer)
):
    source, _ = _get(db, dashboard_id, user)
    d = Dashboard(
        name=f"{source.name} (copy)"[:200],
        description=source.description,
        owner_id=user.id,
        config=source.config,
    )
    db.add(d)
    db.flush()
    audit.record(
        db,
        "dashboard.create",
        user=user,
        request=request,
        object_type="dashboard",
        object_id=d.id,
        details={"name": d.name, "copy_of": source.id},
        commit=False,
    )
    db.commit()
    return _detail(db, d, "manage", user)


@router.delete("/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dashboard(
    dashboard_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    d, level = _get(db, dashboard_id, user)
    if level != "manage":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only the owner, an admin or a workspace manager can delete this dashboard",
        )
    name = d.name
    versions.forget(db, "dashboard", d.id)
    db.delete(d)
    audit.record(
        db,
        "dashboard.delete",
        user=user,
        request=request,
        object_type="dashboard",
        object_id=dashboard_id,
        details={"name": name},
    )


# ------------------------------------------------------------------ favourites and sharing
@router.put("/{dashboard_id}/favorite", status_code=status.HTTP_204_NO_CONTENT)
def add_favorite(dashboard_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _get(db, dashboard_id, user)
    exists = db.scalar(
        select(DashboardFavorite).where(
            DashboardFavorite.user_id == user.id, DashboardFavorite.dashboard_id == dashboard_id
        )
    )
    if exists is None:
        db.add(DashboardFavorite(user_id=user.id, dashboard_id=dashboard_id))
        db.commit()


@router.delete("/{dashboard_id}/favorite", status_code=status.HTTP_204_NO_CONTENT)
def remove_favorite(dashboard_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    for f in db.scalars(
        select(DashboardFavorite).where(
            DashboardFavorite.user_id == user.id, DashboardFavorite.dashboard_id == dashboard_id
        )
    ):
        db.delete(f)
    db.commit()


def _sharing(db: Session, d: Dashboard) -> dict:
    shares = db.scalars(select(DashboardShare).where(DashboardShare.dashboard_id == d.id)).all()
    users = {s.principal_id: s.level for s in shares if s.principal == "user"}
    groups = {s.principal_id: s.level for s in shares if s.principal == "group"}
    everyone = next((s.level for s in shares if s.principal == "everyone"), "none")
    names_u = {u.id: u for u in db.scalars(select(User).where(User.id.in_(users)))} if users else {}
    names_g = rls.group_names(db, list(groups))
    return {
        "everyone": everyone,
        "users": [
            {"id": i, "level": lv, "name": names_u[i].name, "email": names_u[i].email}
            for i, lv in users.items()
            if i in names_u
        ],
        "groups": [{"id": i, "level": lv, "name": names_g[i]} for i, lv in groups.items() if i in names_g],
    }


@router.get("/{dashboard_id}/sharing")
def get_sharing(dashboard_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    d, level = _get(db, dashboard_id, user)
    if level != "manage":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only the owner or an admin can see who it is shared with"
        )
    return _sharing(db, d)


@router.put("/{dashboard_id}/sharing")
def set_sharing(
    dashboard_id: int,
    body: DashboardSharing,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    d, level = _get(db, dashboard_id, user)
    if level != "manage":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner or an admin can share this dashboard")

    def unique(entries: list[ShareEntry]) -> dict[int, str]:
        return {e.id: e.level for e in entries}

    users, groups = unique(body.users), unique(body.groups)
    if users and len(db.scalars(select(User.id).where(User.id.in_(users))).all()) != len(users):
        raise HTTPException(422, "One of the people no longer exists")
    if groups and len(db.scalars(select(Group.id).where(Group.id.in_(groups))).all()) != len(groups):
        raise HTTPException(422, "One of the groups no longer exists")
    for s in db.scalars(select(DashboardShare).where(DashboardShare.dashboard_id == d.id)):
        db.delete(s)
    db.flush()
    if body.everyone != "none":
        db.add(DashboardShare(dashboard_id=d.id, principal="everyone", principal_id=0, level=body.everyone))
    for uid, lv in users.items():
        db.add(DashboardShare(dashboard_id=d.id, principal="user", principal_id=uid, level=lv))
    for gid, lv in groups.items():
        db.add(DashboardShare(dashboard_id=d.id, principal="group", principal_id=gid, level=lv))
    db.flush()
    audit.record(
        db,
        "dashboard.share",
        user=user,
        request=request,
        object_type="dashboard",
        object_id=d.id,
        details={"everyone": body.everyone, "users": users, "groups": groups},
    )
    return _sharing(db, d)


# ------------------------------------------------------------------ data for viewers
def _run_args(body: DashboardQueryIn) -> dict:
    return {
        "filters": body.filters,
        "params": body.params,
        "drill": body.drill,
        "rows": body.rows,
        "limit": body.limit,
    }


@router.post("/{dashboard_id}/query")
def query_tile(
    dashboard_id: int,
    body: DashboardQueryIn,
    db: Session = Depends(get_db),
    user: User = Depends(dashboard_viewer),
):
    """Run one chart tile for the signed-in user (their row-level security applies)."""
    d, level = _get(db, dashboard_id, user)
    if (
        body.rows
        and not (d.config or {}).get("options", {}).get("view_data", True)
        and not svc.can_edit(level)
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Viewing the rows behind charts is turned off here")
    try:
        _, spec = svc.chart_spec(db, d, body.item, **_run_args(body))
        return engine.run(db, spec, rls.principal_for(db, user))
    except svc.DashboardError as exc:
        raise _bad(exc) from exc
    except duck.QueryTimeout as exc:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, str(exc)) from exc
    except engine.QueryError as exc:
        raise HTTPException(400, exc.message) from exc


@router.post("/{dashboard_id}/export")
def export_tile(
    dashboard_id: int,
    body: DashboardQueryIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_explorer),
):
    d, level = _get(db, dashboard_id, user)
    if (
        body.rows
        and not (d.config or {}).get("options", {}).get("view_data", True)
        and not svc.can_edit(level)
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Viewing the rows behind charts is turned off here")
    try:
        chart, spec = svc.chart_spec(db, d, body.item, **{**_run_args(body), "limit": None})
        columns, batches = engine.stream_rows(
            db, spec.model_copy(update={"limit": None}), rls.principal_for(db, user)
        )
    except svc.DashboardError as exc:
        raise _bad(exc) from exc
    except engine.QueryError as exc:
        raise HTTPException(400, exc.message) from exc
    audit.record(
        db,
        "data.export",
        user=user,
        request=request,
        object_type="dashboard",
        object_id=d.id,
        details={"format": body.format, "chart": chart.id, "rows": body.rows},
    )
    safe = re.sub(r"[^\w\- ]+", "", chart.name).strip() or "export"
    if body.format == "xlsx":
        return Response(
            excel.workbook(columns, batches, chart.name),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{safe}.xlsx"'},
        )

    def generate():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([c.label for c in columns])
        yield "﻿" + buf.getvalue()
        for batch in batches:
            buf.seek(0)
            buf.truncate()
            w.writerows(
                [
                    [("'" + v) if isinstance(v, str) and v[:1] in "=+-@\t\r" and v else v for v in r]
                    for r in batch
                ]
            )
            yield buf.getvalue()

    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe}.csv"'},
    )


@router.get("/{dashboard_id}/values")
def filter_values(
    dashboard_id: int,
    dataset: int,
    field: str,
    search: str = "",
    limit: int = 500,
    db: Session = Depends(get_db),
    user: User = Depends(dashboard_viewer),
):
    """Values for a filter control, from a dataset used on the dashboard (row-level security applies)."""
    d, _ = _get(db, dashboard_id, user)
    if dataset not in {ds.id for ds in svc.datasets_of(db, d)}:
        raise HTTPException(404, "That dataset is not used on this dashboard")
    try:
        return engine.field_values(
            db,
            dataset,
            rls.principal_for(db, user),
            field,
            search=search[:200],
            limit=min(max(limit, 1), 1000),
        )
    except engine.QueryError as exc:
        raise HTTPException(422, exc.message) from exc


class RenderIn(BaseModel):
    format: Literal["pdf", "png"] = "pdf"
    state: str | None = Field(None, max_length=8000)  # the ?s= value: filters, parameters, tab


def _render(user_id: int, dashboard_id: int, fmt: str, state: str | None) -> bytes:
    settings = get_settings()
    if settings.jobs_mode == "celery":
        from app.worker import render_dashboard

        result = render_dashboard.delay(user_id, dashboard_id, fmt, state)
        try:
            return base64.b64decode(result.get(timeout=settings.render_timeout_seconds))
        except CeleryTimeout as exc:
            raise render.RenderError("Drawing the dashboard took too long") from exc
    return render.render_dashboard(user_id, dashboard_id, fmt, state)


@router.post("/{dashboard_id}/render")
def render_dashboard(
    dashboard_id: int,
    body: RenderIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """The dashboard as a PDF or PNG, as this person sees it (their row-level security applies)."""
    d, _ = _get(db, dashboard_id, user)
    try:
        data = _render(user.id, d.id, body.format, body.state)
    except render.RenderError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    audit.record(
        db,
        "dashboard.export",
        user=user,
        request=request,
        object_type="dashboard",
        object_id=d.id,
        details={"format": body.format},
    )
    safe = re.sub(r"[^\w\- ]+", "", d.name).strip() or "dashboard"
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    return Response(
        data,
        media_type="application/pdf" if body.format == "pdf" else "image/png",
        headers={"Content-Disposition": f'attachment; filename="{safe} {stamp}.{body.format}"'},
    )
