"""Saved charts. Explorers and up make charts on datasets they can query; the owner or an admin can change
or delete them. Anyone who can open a chart can save a copy of their own. (Sharing comes with dashboards.)
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_role
from app.db import get_db
from app.models import Chart, Dataset, Role, User
from app.query import rls
from app.query.spec import QuerySpec
from app.schemas import ChartIn, ChartOut
from app.services import audit
from app.services.dashboards import dashboards_using_chart

router = APIRouter(prefix="/charts", tags=["charts"])
require_explorer = require_role(Role.explorer)
MAX_CONFIG_BYTES = 200_000


def _out(db: Session, chart: Chart, user: User) -> ChartOut:
    out = ChartOut.model_validate(chart)
    ds = db.get(Dataset, chart.dataset_id)
    owner = db.get(User, chart.owner_id) if chart.owner_id else None
    out.dataset_name = ds.name if ds else ""
    out.owner_name = (owner.name or owner.email) if owner else ""
    out.can_edit = _can_edit(user, chart)
    return out


def _can_edit(user: User, chart: Chart) -> bool:
    return user.has_role(Role.admin) or chart.owner_id == user.id


def _get(db: Session, chart_id: int, user: User) -> Chart:
    chart = db.get(Chart, chart_id)
    if chart is None or not (_can_edit(user, chart)):
        raise HTTPException(404, "Chart not found")
    return chart


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
    dataset_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    stmt = select(Chart).order_by(Chart.updated_at.desc())
    if not user.has_role(Role.admin):
        stmt = stmt.where(Chart.owner_id == user.id)
    if dataset_id is not None:
        stmt = stmt.where(Chart.dataset_id == dataset_id)
    return [_out(db, c, user) for c in db.scalars(stmt)]


@router.post("", response_model=ChartOut, status_code=status.HTTP_201_CREATED)
def create_chart(
    body: ChartIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_explorer)
):
    query = _validate(db, body, user)
    chart = Chart(
        name=body.name.strip(),
        description=body.description,
        dataset_id=body.dataset_id,
        owner_id=user.id,
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
    return _out(db, chart, user)


@router.get("/{chart_id}", response_model=ChartOut)
def get_chart(chart_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _out(db, _get(db, chart_id, user), user)


@router.put("/{chart_id}", response_model=ChartOut)
def update_chart(
    chart_id: int, body: ChartIn, db: Session = Depends(get_db), user: User = Depends(require_explorer)
):
    chart = _get(db, chart_id, user)
    query = _validate(db, body, user)
    chart.name, chart.description = body.name.strip(), body.description
    chart.dataset_id, chart.config, chart.query = body.dataset_id, body.config, query
    db.commit()
    return _out(db, chart, user)


@router.post("/{chart_id}/copy", response_model=ChartOut, status_code=status.HTTP_201_CREATED)
def copy_chart(
    chart_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_explorer)
):
    source = _get(db, chart_id, user)
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
    return _out(db, copy, user)


@router.delete("/{chart_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chart(
    chart_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_explorer)
):
    chart = _get(db, chart_id, user)
    used = dashboards_using_chart(db, chart.id)
    if used:
        names = ", ".join(f'"{d.name}"' for d in used[:3])
        raise HTTPException(
            409,
            f"This chart is on the dashboard{'s' if len(used) > 1 else ''} {names}. Remove it there first.",
        )
    name = chart.name
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
