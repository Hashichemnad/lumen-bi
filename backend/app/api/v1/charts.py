"""Saved charts. Explorers and up make charts on datasets they can query. The owner, an admin or a manager of
the chart's workspace can change or delete it; workspace editors can change it; workspace viewers open it.
Anyone who can open a chart can save a copy of their own.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_role
from app.db import get_db
from app.models import Chart, Dataset, Role, User, Workspace
from app.query import rls
from app.query.spec import QuerySpec
from app.schemas import ChartIn, ChartOut, MoveIn
from app.services import audit, versions, workspaces
from app.services.dashboards import chart_level, dashboards_using_chart

router = APIRouter(prefix="/charts", tags=["charts"])
require_explorer = require_role(Role.explorer)
MAX_CONFIG_BYTES = 200_000


def _out(db: Session, chart: Chart, user: User, level: str | None = None) -> ChartOut:
    out = ChartOut.model_validate(chart)
    ds = db.get(Dataset, chart.dataset_id)
    owner = db.get(User, chart.owner_id) if chart.owner_id else None
    ws = db.get(Workspace, chart.workspace_id) if chart.workspace_id else None
    out.dataset_name = ds.name if ds else ""
    out.owner_name = (owner.name or owner.email) if owner else ""
    out.workspace_name = ws.name if ws else ""
    out.level = level or chart_level(db, user, chart) or "view"
    out.can_edit = out.level in {"edit", "manage"}
    return out


def _get(db: Session, chart_id: int, user: User, need: str = "view") -> tuple[Chart, str]:
    chart = db.get(Chart, chart_id)
    level = chart_level(db, user, chart) if chart else None
    if chart is None or level is None:
        raise HTTPException(404, "Chart not found")
    order = {"view": 1, "edit": 2, "manage": 3}
    if order[level] < order[need]:
        message = (
            "Only the owner or a workspace manager can do this"
            if need == "manage"
            else ("You can open this chart but not change it")
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, message)
    return chart, level


def _check_workspace(db: Session, user: User, workspace_id: int | None) -> None:
    if workspace_id is None:
        return
    if db.get(Workspace, workspace_id) is None or not workspaces.can_add_to(db, user, workspace_id):
        raise HTTPException(403, "You can add items only to workspaces where you are an editor or manager")


def _validate(db: Session, body: ChartIn, user: User) -> dict:
    ds = db.get(Dataset, body.dataset_id)
    if ds is None or not rls.can_query(db, user, ds):
        raise HTTPException(422, "Choose a dataset you can use")
    if len(json.dumps(body.config)) + len(json.dumps(body.query)) > MAX_CONFIG_BYTES:
        raise HTTPException(422, "This chart is too large to save")
    try:
        spec = QuerySpec(**{**body.query, "dataset": ds.id})
    except ValidationError as exc:
        raise HTTPException(422, f"The chart's query is not valid: {exc.errors()[0]['msg']}") from exc
    return spec.model_dump(exclude={"dataset"}, exclude_defaults=True)


@router.get("", response_model=list[ChartOut])
def list_charts(
    dataset_id: int | None = None,
    workspace_id: int | None = None,  # 0 = personal charts only
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    stmt = select(Chart).order_by(Chart.updated_at.desc())
    if not user.has_role(Role.admin):
        mine = list(workspaces.roles_of(db, user))
        stmt = stmt.where((Chart.owner_id == user.id) | (Chart.workspace_id.in_(mine)))
    if dataset_id is not None:
        stmt = stmt.where(Chart.dataset_id == dataset_id)
    if workspace_id is not None:
        stmt = stmt.where(
            Chart.workspace_id == workspace_id if workspace_id else Chart.workspace_id.is_(None)
        )
    return [_out(db, c, user) for c in db.scalars(stmt)]


@router.post("", response_model=ChartOut, status_code=status.HTTP_201_CREATED)
def create_chart(
    body: ChartIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_explorer)
):
    query = _validate(db, body, user)
    _check_workspace(db, user, body.workspace_id)
    chart = Chart(
        name=body.name.strip(),
        description=body.description,
        dataset_id=body.dataset_id,
        owner_id=user.id,
        workspace_id=body.workspace_id,
        config=body.config,
        query=query,
    )
    db.add(chart)
    db.flush()
    audit.record(
        db,
        "chart.create",
        user=user,
        request=request,
        object_type="chart",
        object_id=chart.id,
        details={"name": chart.name},
        commit=False,
    )
    db.commit()
    return _out(db, chart, user, "manage")


@router.get("/{chart_id}", response_model=ChartOut)
def get_chart(chart_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    chart, level = _get(db, chart_id, user)
    return _out(db, chart, user, level)


@router.put("/{chart_id}", response_model=ChartOut)
def update_chart(
    chart_id: int, body: ChartIn, db: Session = Depends(get_db), user: User = Depends(require_explorer)
):
    chart, level = _get(db, chart_id, user, "edit")
    query = _validate(db, body, user)
    versions.keep_before_change(db, chart, user)
    chart.name, chart.description = body.name.strip(), body.description
    chart.dataset_id, chart.config, chart.query = body.dataset_id, body.config, query
    db.commit()
    return _out(db, chart, user, level)


@router.put("/{chart_id}/workspace", response_model=ChartOut)
def move_chart(
    chart_id: int,
    body: MoveIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_explorer),
):
    chart, _ = _get(db, chart_id, user, "manage")
    _check_workspace(db, user, body.workspace_id)
    before = chart.workspace_id
    chart.workspace_id = body.workspace_id
    audit.record(
        db,
        "chart.move",
        user=user,
        request=request,
        object_type="chart",
        object_id=chart.id,
        details={"name": chart.name, "from": before, "to": body.workspace_id},
        commit=False,
    )
    db.commit()
    return _out(db, chart, user)


@router.post("/{chart_id}/copy", response_model=ChartOut, status_code=status.HTTP_201_CREATED)
def copy_chart(
    chart_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_explorer)
):
    source, _ = _get(db, chart_id, user)
    copy = Chart(
        name=f"{source.name} (copy)"[:200],
        description=source.description,
        dataset_id=source.dataset_id,
        owner_id=user.id,
        config=source.config,
        query=source.query,
    )
    db.add(copy)
    db.flush()
    audit.record(
        db,
        "chart.create",
        user=user,
        request=request,
        object_type="chart",
        object_id=copy.id,
        details={"name": copy.name, "copy_of": source.id},
        commit=False,
    )
    db.commit()
    return _out(db, copy, user, "manage")


@router.delete("/{chart_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chart(
    chart_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_explorer)
):
    chart, _ = _get(db, chart_id, user, "manage")
    used = dashboards_using_chart(db, chart.id)
    if used:
        names = ", ".join(f'"{d.name}"' for d in used[:3])
        raise HTTPException(
            409,
            f"This chart is on the dashboard{'s' if len(used) > 1 else ''} {names}. Remove it there first.",
        )
    name = chart.name
    versions.forget(db, "chart", chart.id)
    db.delete(chart)
    audit.record(
        db,
        "chart.delete",
        user=user,
        request=request,
        object_type="chart",
        object_id=chart_id,
        details={"name": name},
    )
