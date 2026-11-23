"""Querying data (anyone with access to a dataset) and dataset security (owner or admin).

POST /query                  run a query spec (charts, tables; AI and dashboards use the same)
POST /query/export           the same as CSV
GET  /catalog/datasets       datasets the user may query
GET  /catalog/datasets/{id}  their fields, hierarchies and parameters
GET  /datasets/{id}/security, rules CRUD, PUT access, GET security/check (preview as a user)
GET  /directory              people and groups, for sharing and rule editors (Explorers and up)
"""

import csv
import io
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_role
from app.api.v1.model import _param_out
from app.db import get_db
from app.models import Dataset, DatasetShare, Group, Hierarchy, Parameter, RlsRule, Role, User
from app.query import duck, engine, rls
from app.query.spec import QuerySpec
from app.schemas import (
    AccessIn,
    AccessOut,
    CatalogDatasetOut,
    DatasetRef,
    FieldOut,
    HierarchyOut,
    InheritedRuleOut,
    RlsRuleIn,
    RlsRuleOut,
    SecurityCheckOut,
    SecurityOut,
)
from app.semantic import model
from app.semantic.formula import FormulaError
from app.services import audit

router = APIRouter(tags=["query"])
require_creator = require_role(Role.creator)


class QueryIn(QuerySpec):
    as_user_id: int | None = None  # preview as another user (dataset owner or admin)


# ------------------------------------------------------------------ helpers
def _dataset(db: Session, dataset_id: int) -> Dataset:
    ds = db.get(Dataset, dataset_id)
    if ds is None:
        raise HTTPException(404, "Dataset not found")
    return ds


def _queryable(db: Session, dataset_id: int, user: User) -> Dataset:
    ds = _dataset(db, dataset_id)
    if not rls.can_query(db, user, ds):
        raise HTTPException(404, "Dataset not found")  # do not reveal datasets people cannot use
    return ds


def _can_manage(user: User, ds: Dataset) -> bool:
    return user.has_role(Role.admin) or (user.has_role(Role.creator) and ds.owner_id == user.id)


def _manageable(db: Session, dataset_id: int, user: User) -> Dataset:
    ds = _dataset(db, dataset_id)
    if not _can_manage(user, ds):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only the dataset's owner or an admin can change its security"
        )
    return ds


def _principal(db: Session, request: Request, user: User, ds: Dataset, as_user_id: int | None):
    if as_user_id is None or as_user_id == user.id:
        return rls.principal_for(db, user)
    if not _can_manage(user, ds):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only the dataset's owner or an admin can preview as someone else"
        )
    target = db.get(User, as_user_id)
    if target is None:
        raise HTTPException(404, "User not found")
    if not rls.can_query(db, target, ds):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"{target.email} cannot use this dataset")
    audit.record(
        db,
        "query.preview_as",
        user=user,
        request=request,
        object_type="dataset",
        object_id=ds.id,
        details={"as": target.email},
    )
    return rls.principal_for(db, target)


def _bad(exc: Exception) -> HTTPException:
    if isinstance(exc, duck.QueryTimeout):
        return HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, str(exc))
    return HTTPException(400, getattr(exc, "message", str(exc)))


# ------------------------------------------------------------------ querying
@router.post("/query")
def run_query(
    body: QueryIn, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, Any]:
    ds = _queryable(db, body.dataset, user)
    principal = _principal(db, request, user, ds, body.as_user_id)
    try:
        return engine.run(db, QuerySpec(**body.model_dump(exclude={"as_user_id"})), principal)
    except (engine.QueryError, duck.QueryTimeout) as exc:
        raise _bad(exc) from exc


def _csv_cell(v: Any) -> Any:
    # Stop spreadsheet apps from running text that looks like a formula
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


@router.post("/query/export")
def export_query(
    body: QueryIn, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    """CSV of a query (row-level security applied), up to EXPORT_MAX_ROWS rows. Explorers and up."""
    if not user.has_role(Role.explorer):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission to export data")
    ds = _queryable(db, body.dataset, user)
    principal = _principal(db, request, user, ds, body.as_user_id)
    try:
        columns, batches = engine.stream_rows(
            db, QuerySpec(**body.model_dump(exclude={"as_user_id"})), principal
        )
    except (engine.QueryError, duck.QueryTimeout) as exc:
        raise _bad(exc) from exc
    audit.record(
        db,
        "data.export",
        user=user,
        request=request,
        object_type="dataset",
        object_id=ds.id,
        details={"format": "csv", "mode": body.mode},
    )

    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([c.label for c in columns])
        yield "﻿" + buf.getvalue()  # BOM so Excel reads Arabic text correctly
        for batch in batches:
            buf.seek(0)
            buf.truncate()
            writer.writerows([[_csv_cell(v) for v in row] for row in batch])
            yield buf.getvalue()

    safe = re.sub(r"[^\w\- ]+", "", ds.name).strip() or "export"
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe} {stamp}.csv"'},
    )


# ------------------------------------------------------------------ catalog (what a user may query)
@router.get("/catalog/datasets", response_model=list[CatalogDatasetOut])
def catalog(db: Session = Depends(get_db), user: User = Depends(current_user)):
    out = []
    for ds in db.scalars(select(Dataset).order_by(Dataset.name)):
        if rls.can_query(db, user, ds):
            out.append(
                CatalogDatasetOut(
                    id=ds.id,
                    name=ds.name,
                    description=ds.description,
                    row_count=ds.row_count,
                    last_synced_at=ds.last_synced_at,
                    restricted=user.role != "admin" and rls.has_rules(db, ds),
                )
            )
    return out


@router.get("/catalog/datasets/{dataset_id}")
def catalog_dataset(dataset_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    ds = _queryable(db, dataset_id, user)
    fields = model.fields_of(db, ds.id)
    hierarchies = list(db.scalars(select(Hierarchy).where(Hierarchy.dataset_id == ds.id)))
    visible = [f for f in fields if not f.missing and not f.error and (not f.hidden or _can_manage(user, ds))]
    return {
        "id": ds.id,
        "name": ds.name,
        "description": ds.description,
        "row_count": ds.row_count,
        "fields": [
            FieldOut.model_validate(f).model_dump(exclude={"formula", "config"})
            if not user.has_role(Role.creator)
            else FieldOut.model_validate(f).model_dump()
            for f in visible
        ],
        "hierarchies": [HierarchyOut.model_validate(h).model_dump() for h in hierarchies],
        "parameters": [
            _param_out(p).model_dump()
            for p in db.scalars(
                select(Parameter).where(Parameter.dataset_id == ds.id).order_by(Parameter.name)
            )
        ],
    }


# ------------------------------------------------------------------ security: rules
def _rule_out(db: Session, r: RlsRule) -> RlsRuleOut:
    out = RlsRuleOut.model_validate(r)
    out.summary = rls.describe(r)
    names = rls.group_names(db, [int(g) for g in r.exempt_groups or []])
    out.exempt_group_names = [names[g] for g in r.exempt_groups or [] if g in names]
    return out


def _access(db: Session, ds: Dataset) -> AccessOut:
    shares = db.scalars(select(DatasetShare).where(DatasetShare.dataset_id == ds.id)).all()
    user_ids = [s.principal_id for s in shares if s.principal == "user"]
    group_ids = [s.principal_id for s in shares if s.principal == "group"]
    users = db.scalars(select(User).where(User.id.in_(user_ids))).all() if user_ids else []
    groups = db.scalars(select(Group).where(Group.id.in_(group_ids))).all() if group_ids else []
    return AccessOut(
        everyone=any(s.principal == "everyone" for s in shares),
        user_ids=user_ids,
        group_ids=group_ids,
        users=[{"id": u.id, "name": u.name, "email": u.email} for u in users],
        groups=[{"id": g.id, "name": g.name} for g in groups],
    )


def _attribute_names(db: Session) -> list[str]:
    names: set[str] = set()
    for attrs in db.scalars(select(User.attributes)):
        names |= {str(k).strip().lower() for k in (attrs or {})}
    return sorted(names | set(rls.BUILT_IN_ATTRIBUTES))


def _validate_rule(db: Session, ds: Dataset, body: RlsRuleIn) -> dict:
    cfg = dict(body.config)
    field_name = str(cfg.get("field", "")).strip()
    if not field_name:
        raise HTTPException(422, "Choose the field to filter on")
    try:
        compiled = model.compile_field(model.build_context(db, ds.id), field_name)
    except FormulaError as exc:
        raise HTTPException(422, exc.message) from exc
    if compiled.level == "agg":
        raise HTTPException(422, "Row-level security needs a row-level field, not an aggregate")
    out: dict[str, Any] = {"field": field_name, "ignore_case": bool(cfg.get("ignore_case", True))}
    if body.kind == "attribute":
        attribute = str(cfg.get("attribute", "")).strip().lower()
        if not attribute:
            raise HTTPException(422, "Choose the user attribute to match")
        out["attribute"] = attribute
    else:
        try:
            ent = db.get(Dataset, int(cfg.get("dataset")))
        except (TypeError, ValueError):
            ent = None
        if ent is None:
            raise HTTPException(422, "Choose the access table dataset")
        if ent.id == ds.id:
            raise HTTPException(422, "A dataset cannot be its own access table")
        columns = {c["name"] for c in ent.columns or []}
        for key, what in (
            ("user_column", "the column with the user"),
            ("value_column", "the column with values"),
        ):
            if cfg.get(key) not in columns:
                raise HTTPException(422, f'Choose {what} from "{ent.name}"')
        out |= {
            "dataset": ent.id,
            "user_column": cfg["user_column"],
            "value_column": cfg["value_column"],
            "match": str(cfg.get("match") or "email").strip().lower(),
            "all_value": str(cfg["all_value"]).strip() if cfg.get("all_value") not in (None, "") else None,
        }
    return out


def _check_groups(db: Session, ids: list[int]) -> list[int]:
    found = rls.group_names(db, ids)
    missing = [g for g in ids if g not in found]
    if missing:
        raise HTTPException(422, "One of the exempt groups no longer exists")
    return list(dict.fromkeys(ids))


@router.get("/datasets/{dataset_id}/security", response_model=SecurityOut)
def get_security(dataset_id: int, db: Session = Depends(get_db), user: User = Depends(require_creator)):
    ds = _dataset(db, dataset_id)
    own = db.scalars(select(RlsRule).where(RlsRule.dataset_id == ds.id).order_by(RlsRule.id)).all()
    inherited = []
    for eff in rls.effective_rules(db, ds):
        if eff.columns is None:
            continue
        field_name = str((eff.rule.config or {}).get("field", ""))
        ctx = rls._rule_context(db, eff, model.build_context(db, ds.id))
        try:
            model.compile_field(ctx, field_name)
            mapped = True
        except FormulaError:
            mapped = False
        inherited.append(
            InheritedRuleOut(
                rule=_rule_out(db, eff.rule),
                dataset=DatasetRef(id=eff.origin.id, name=eff.origin.name),
                mapped=mapped,
            )
        )
    return SecurityOut(
        rules=[_rule_out(db, r) for r in own],
        inherited=inherited,
        access=_access(db, ds),
        attributes=_attribute_names(db),
    )


@router.post(
    "/datasets/{dataset_id}/security/rules", response_model=RlsRuleOut, status_code=status.HTTP_201_CREATED
)
def create_rule(
    dataset_id: int,
    body: RlsRuleIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    ds = _manageable(db, dataset_id, user)
    rule = RlsRule(
        dataset_id=ds.id,
        name=body.name.strip(),
        kind=body.kind,
        config=_validate_rule(db, ds, body),
        exempt_groups=_check_groups(db, body.exempt_groups),
        enabled=body.enabled,
        description=body.description,
    )
    db.add(rule)
    db.flush()
    audit.record(
        db,
        "security.rule_create",
        user=user,
        request=request,
        object_type="dataset",
        object_id=ds.id,
        details={"rule": rule.id, "kind": rule.kind, "config": rule.config},
    )
    return _rule_out(db, rule)


def _rule(db: Session, ds: Dataset, rule_id: int) -> RlsRule:
    rule = db.get(RlsRule, rule_id)
    if rule is None or rule.dataset_id != ds.id:
        raise HTTPException(404, "Rule not found")
    return rule


@router.put("/datasets/{dataset_id}/security/rules/{rule_id}", response_model=RlsRuleOut)
def update_rule(
    dataset_id: int,
    rule_id: int,
    body: RlsRuleIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    ds = _manageable(db, dataset_id, user)
    rule = _rule(db, ds, rule_id)
    before = {"config": rule.config, "enabled": rule.enabled, "exempt_groups": rule.exempt_groups}
    rule.name, rule.kind, rule.description = body.name.strip(), body.kind, body.description
    rule.config = _validate_rule(db, ds, body)
    rule.exempt_groups = _check_groups(db, body.exempt_groups)
    rule.enabled = body.enabled
    db.flush()
    audit.record(
        db,
        "security.rule_update",
        user=user,
        request=request,
        object_type="dataset",
        object_id=ds.id,
        details={
            "rule": rule.id,
            "before": before,
            "after": {"config": rule.config, "enabled": rule.enabled, "exempt_groups": rule.exempt_groups},
        },
    )
    return _rule_out(db, rule)


@router.delete("/datasets/{dataset_id}/security/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(
    dataset_id: int,
    rule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    ds = _manageable(db, dataset_id, user)
    rule = _rule(db, ds, rule_id)
    details = {"rule": rule.id, "kind": rule.kind, "config": rule.config}
    db.delete(rule)
    audit.record(
        db,
        "security.rule_delete",
        user=user,
        request=request,
        object_type="dataset",
        object_id=ds.id,
        details=details,
    )


# ------------------------------------------------------------------ security: who may query
@router.put("/datasets/{dataset_id}/access", response_model=AccessOut)
def set_access(
    dataset_id: int,
    body: AccessIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    ds = _manageable(db, dataset_id, user)
    user_ids = list(dict.fromkeys(body.user_ids))
    group_ids = _check_groups(db, body.group_ids)
    if user_ids and len(db.scalars(select(User.id).where(User.id.in_(user_ids))).all()) != len(user_ids):
        raise HTTPException(422, "One of the people no longer exists")
    for share in db.scalars(select(DatasetShare).where(DatasetShare.dataset_id == ds.id)):
        db.delete(share)
    db.flush()
    if body.everyone:
        db.add(DatasetShare(dataset_id=ds.id, principal="everyone", principal_id=0))
    for uid in user_ids:
        db.add(DatasetShare(dataset_id=ds.id, principal="user", principal_id=uid))
    for gid in group_ids:
        db.add(DatasetShare(dataset_id=ds.id, principal="group", principal_id=gid))
    db.flush()
    audit.record(
        db,
        "security.access_update",
        user=user,
        request=request,
        object_type="dataset",
        object_id=ds.id,
        details={"everyone": body.everyone, "users": user_ids, "groups": group_ids},
    )
    return _access(db, ds)


@router.get("/datasets/{dataset_id}/security/check", response_model=SecurityCheckOut)
def check_as(
    dataset_id: int,
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    """What a user would see: which rules apply to them, with which values, and how many rows."""
    ds = _manageable(db, dataset_id, user)
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "User not found")
    has_access = rls.can_query(db, target, ds)
    principal = rls.principal_for(db, target)
    visible: int | None = None
    try:
        visible, results = engine.visible_rows(db, ds, principal)
    except engine.QueryError:
        _, results = None, []
    audit.record(
        db,
        "query.preview_as",
        user=user,
        request=request,
        object_type="dataset",
        object_id=ds.id,
        details={"as": target.email, "check": True},
    )
    return SecurityCheckOut(
        user={"id": target.id, "name": target.name, "email": target.email, "role": target.role},
        has_access=has_access,
        bypass=principal.bypass,
        rules=[r.jsonable(r.origin.id != ds.id) for r in results],
        visible_rows=visible,
        total_rows=ds.row_count,
    )


@router.get("/directory")
def directory(db: Session = Depends(get_db), _: User = Depends(require_role(Role.explorer))):
    """People and groups, to share dashboards with or to preview as (Explorers and up)."""
    users = db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.name, User.email))
    groups = db.scalars(select(Group).order_by(Group.name))
    return {
        "users": [{"id": u.id, "name": u.name, "email": u.email, "role": u.role} for u in users],
        "groups": [{"id": g.id, "name": g.name} for g in groups],
    }
