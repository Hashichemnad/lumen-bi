"""Model of a dataset: fields (labels, roles, formats, calculated/group/bin fields), hierarchies and
parameters."""

import re

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.db import get_db
from app.models import Dataset, DatasetField, Hierarchy, Parameter, Role, User
from app.schemas import (
    FieldCreate,
    FieldOrderIn,
    FieldOut,
    FieldUpdate,
    FormulaCheckIn,
    FormulaCheckOut,
    HierarchyIn,
    HierarchyOut,
    ParameterIn,
    ParameterOut,
)
from app.semantic import model
from app.semantic.formula import Compiled, Compiler, FormulaError, parse, tokenize
from app.services import audit, snapshots

router = APIRouter(tags=["model"])
require_creator = require_role(Role.creator)
NAME_RE = re.compile(r"^[^\[\]@#'\"]+$")


def _dataset(db: Session, dataset_id: int) -> Dataset:
    ds = db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    return ds


def _editable(db: Session, dataset_id: int, user: User) -> Dataset:
    ds = _dataset(db, dataset_id)
    if not (user.has_role(Role.admin) or ds.owner_id == user.id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only the dataset's owner or an admin can change its model"
        )
    return ds


def _field(db: Session, dataset_id: int, field_id: int) -> DatasetField:
    f = db.get(DatasetField, field_id)
    if not f or f.dataset_id != dataset_id:
        raise HTTPException(404, "Field not found")
    return f


def _out(fields: list[DatasetField], f: DatasetField, hierarchies: list[Hierarchy]) -> FieldOut:
    out = FieldOut.model_validate(f)
    out.used_by = model.dependents(fields, f.name) + [
        f"hierarchy {h.name}" for h in hierarchies if f.name in h.levels
    ]
    return out


def _formula_error(exc: FormulaError) -> HTTPException:
    detail = exc.message + (f" (at character {exc.pos + 1})" if exc.pos is not None else "")
    return HTTPException(422, detail)


def _check_name(fields: list[DatasetField], name: str, exclude_id: int | None = None) -> str:
    name = name.strip()
    if not NAME_RE.match(name):
        raise HTTPException(422, "Field names cannot contain [ ] @ # or quotes")
    if any(f.name.lower() == name.lower() and f.id != exclude_id for f in fields):
        raise HTTPException(409, f'A field called "{name}" already exists')
    return name


def _compile_candidate(
    db: Session,
    ds: Dataset,
    name: str,
    kind: str,
    formula: str | None,
    config: dict,
    fields: list[DatasetField],
) -> Compiled:
    """Compile a new or edited field in the context of all other fields."""
    temp = DatasetField(dataset_id=ds.id, name=name, kind=kind, formula=formula, config=config)
    others = [f for f in fields if f.name.lower() != name.lower()]
    ctx = model.build_context(db, ds.id, fields=[*others, temp])
    if kind == "calculated":
        if not (formula or "").strip():
            raise FormulaError("The formula is empty", 0)
        # Compile the text directly so error positions point into it; marking the field as being visited
        # makes any path back to itself a cycle error
        ctx._visiting.add(name)
        return Compiler(ctx).compile(parse(formula))
    if not config.get("source"):
        raise FormulaError("Choose the field to group or bin")
    return model.compile_field(ctx, name)


def _sample(ds: Dataset, compiled: Compiled) -> list:
    path = snapshots.snapshot_path(ds.id, ds.current_version)
    if not path:
        return []
    con = duckdb.connect()
    try:
        src = "read_parquet('" + str(path).replace("'", "''") + "')"
        limit = "" if compiled.level == "agg" else " LIMIT 10"
        rows = con.execute(f"SELECT {compiled.sql} FROM {src}{limit}").fetchall()
    except duckdb.Error as exc:
        raise FormulaError(f"The formula could not run on the data: {str(exc).splitlines()[0]}") from exc
    finally:
        con.close()
    return [model.jsonable(r[0]) for r in rows]


def _role_and_format(compiled: Compiled, kind: str) -> tuple[str, dict]:
    role = (
        "measure"
        if kind == "calculated" and (compiled.level == "agg" or compiled.type in {"integer", "number"})
        else "dimension"
    )
    return role, model.default_format(compiled.type)


# ------------------------------------------------------------------ fields
@router.get("/datasets/{dataset_id}/fields", response_model=list[FieldOut])
def list_fields(dataset_id: int, db: Session = Depends(get_db), _: User = Depends(require_creator)):
    ds = _dataset(db, dataset_id)
    fields = model.fields_of(db, dataset_id)
    if not fields and ds.columns:  # synced before the model existed: create its fields now
        model.reconcile_fields(db, ds)
        db.commit()
        fields = model.fields_of(db, dataset_id)
    hierarchies = list(db.scalars(select(Hierarchy).where(Hierarchy.dataset_id == dataset_id)))
    return [_out(fields, f, hierarchies) for f in fields]


@router.get("/datasets/{dataset_id}/values")
def field_values(
    dataset_id: int,
    field: str,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    _: User = Depends(require_creator),
):
    """Distinct values of a field (most frequent first), e.g. to build groups."""
    ds = _dataset(db, dataset_id)
    path = snapshots.snapshot_path(ds.id, ds.current_version)
    if not path:
        return {"values": [], "truncated": False}
    try:
        compiled = model.compile_field(model.build_context(db, ds.id), field)
    except FormulaError as exc:
        raise HTTPException(422, exc.message) from exc
    if compiled.level == "agg":
        raise HTTPException(422, "Aggregated fields have no list of values")
    con = duckdb.connect()
    try:
        src = "read_parquet('" + str(path).replace("'", "''") + "')"
        rows = con.execute(
            f"SELECT v, count(*) AS n FROM (SELECT {compiled.sql} AS v FROM {src}) "
            f"GROUP BY v ORDER BY n DESC, v LIMIT {int(limit) + 1}"
        ).fetchall()
    finally:
        con.close()
    values = [model.jsonable(r[0]) for r in rows[:limit]]
    return {"values": values, "truncated": len(rows) > limit}


@router.post("/datasets/{dataset_id}/formula/check", response_model=FormulaCheckOut)
def check_formula(
    dataset_id: int, body: FormulaCheckIn, db: Session = Depends(get_db), _: User = Depends(require_creator)
):
    ds = _dataset(db, dataset_id)
    fields = model.fields_of(db, dataset_id)
    name = (body.field_name or "__formula_check__").strip()
    try:
        compiled = _compile_candidate(db, ds, name, body.kind, body.formula, body.config, fields)
        sample = _sample(ds, compiled)
    except FormulaError as exc:
        return FormulaCheckOut(ok=False, error=exc.message, pos=exc.pos)
    return FormulaCheckOut(ok=True, type=compiled.type, is_aggregate=compiled.level == "agg", sample=sample)


@router.post("/datasets/{dataset_id}/fields", response_model=FieldOut, status_code=status.HTTP_201_CREATED)
def create_field(
    dataset_id: int,
    body: FieldCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    ds = _editable(db, dataset_id, user)
    fields = model.fields_of(db, dataset_id)
    name = _check_name(fields, body.name)
    try:
        compiled = _compile_candidate(db, ds, name, body.kind, body.formula, body.config, fields)
    except FormulaError as exc:
        raise _formula_error(exc) from exc
    role, fmt = _role_and_format(compiled, body.kind)
    f = DatasetField(
        dataset_id=ds.id,
        name=name,
        kind=body.kind,
        label=body.label or name,
        description=body.description,
        role=body.role or role,
        data_type=compiled.type,
        format=body.format or fmt,
        default_agg="none"
        if compiled.level == "agg"
        else ("sum" if compiled.type in {"integer", "number"} else "count_distinct"),
        formula=body.formula if body.kind == "calculated" else None,
        config=body.config,
        is_aggregate=compiled.level == "agg",
        position=max((x.position for x in fields), default=-1) + 1,
    )
    db.add(f)
    db.flush()
    audit.record(
        db,
        "dataset.field_create",
        user=user,
        request=request,
        object_type="dataset",
        object_id=ds.id,
        details={"field": name, "kind": body.kind},
    )
    return _out([*fields, f], f, [])


@router.put("/datasets/{dataset_id}/fields/order", response_model=list[FieldOut])
def reorder_fields(
    dataset_id: int, body: FieldOrderIn, db: Session = Depends(get_db), user: User = Depends(require_creator)
):
    _editable(db, dataset_id, user)
    fields = {f.id: f for f in model.fields_of(db, dataset_id)}
    if set(body.ids) != set(fields):
        raise HTTPException(422, "Send every field id of the dataset exactly once")
    for pos, fid in enumerate(body.ids):
        fields[fid].position = pos
    db.commit()
    return list_fields(dataset_id, db)


@router.patch("/datasets/{dataset_id}/fields/{field_id}", response_model=FieldOut)
def update_field(
    dataset_id: int,
    field_id: int,
    body: FieldUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    ds = _editable(db, dataset_id, user)
    f = _field(db, dataset_id, field_id)
    fields = model.fields_of(db, dataset_id)
    changes = body.model_dump(exclude_unset=True)
    if f.kind == "column" and ({"name", "formula", "config"} & set(changes)):
        raise HTTPException(422, "Source columns keep their name; add a calculated field to transform them")

    if f.kind != "column" and ({"name", "formula", "config"} & set(changes)):
        new_name = _check_name(fields, body.name, exclude_id=f.id) if body.name else f.name
        formula = body.formula if body.formula is not None else f.formula
        config = body.config if body.config is not None else f.config
        try:
            compiled = _compile_candidate(db, ds, new_name, f.kind, formula, config, fields)
        except FormulaError as exc:
            raise _formula_error(exc) from exc
        if new_name != f.name:
            _rename_references(db, dataset_id, fields, f.name, new_name)
            if f.label == f.name:
                f.label = new_name
            f.name = new_name
        f.formula = formula if f.kind == "calculated" else None
        f.config = config
        f.data_type, f.is_aggregate, f.error = compiled.type, compiled.level == "agg", None

    if body.label is not None:
        f.label = body.label.strip() or f.name
    if body.description is not None:
        f.description = body.description
    if body.role is not None:
        f.role = body.role
    if body.format is not None:
        if body.format.get("type") not in model.FORMAT_TYPES:
            raise HTTPException(422, f"Format must be one of: {', '.join(sorted(model.FORMAT_TYPES))}")
        f.format = body.format
    if body.default_agg is not None:
        if body.default_agg not in model.AGGREGATIONS:
            raise HTTPException(422, f"Aggregation must be one of: {', '.join(sorted(model.AGGREGATIONS))}")
        f.default_agg = body.default_agg
    if body.hidden is not None:
        f.hidden = body.hidden
    db.flush()
    model.recheck_calculated(db, ds)  # fields that use this one may change type or break
    audit.record(
        db,
        "dataset.field_update",
        user=user,
        request=request,
        object_type="dataset",
        object_id=ds.id,
        details={"field": f.name, "changes": sorted(changes)},
    )
    fields = model.fields_of(db, dataset_id)
    hierarchies = list(db.scalars(select(Hierarchy).where(Hierarchy.dataset_id == dataset_id)))
    return _out(fields, f, hierarchies)


def _rename_references(db: Session, dataset_id: int, fields: list[DatasetField], old: str, new: str) -> None:
    pattern = re.compile(r"\[" + re.escape(old.replace("]", "]]")) + r"\]", re.I)
    replacement = "[" + new.replace("]", "]]") + "]"
    for other in fields:
        if other.formula:
            other.formula = pattern.sub(lambda _m: replacement, other.formula)
        if (
            other.kind in {"group", "bin"}
            and str((other.config or {}).get("source", "")).lower() == old.lower()
        ):
            other.config = {**other.config, "source": new}
    for h in db.scalars(select(Hierarchy).where(Hierarchy.dataset_id == dataset_id)):
        h.levels = [new if lv == old else lv for lv in h.levels]


@router.delete("/datasets/{dataset_id}/fields/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_field(
    dataset_id: int,
    field_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    ds = _editable(db, dataset_id, user)
    f = _field(db, dataset_id, field_id)
    if f.kind == "column" and not f.missing:
        raise HTTPException(422, "Source columns cannot be deleted; hide them instead")
    fields = model.fields_of(db, dataset_id)
    used = model.dependents(fields, f.name) + [
        f"hierarchy {h}" for h in model.hierarchies_using(db, ds.id, f.name)
    ]
    if used:
        raise HTTPException(409, f"[{f.name}] is used by {', '.join(used)}. Change those first.")
    name = f.name
    db.delete(f)
    audit.record(
        db,
        "dataset.field_delete",
        user=user,
        request=request,
        object_type="dataset",
        object_id=ds.id,
        details={"field": name},
    )


# ------------------------------------------------------------------ hierarchies
def _check_levels(db: Session, dataset_id: int, body: HierarchyIn) -> list[str]:
    names = {f.name for f in model.fields_of(db, dataset_id) if not f.missing}
    levels = [lv for lv in body.levels if lv]
    if len(levels) < 2:
        raise HTTPException(422, "A hierarchy needs at least two levels")
    if len(set(levels)) != len(levels):
        raise HTTPException(422, "Each field can appear once in a hierarchy")
    unknown = [lv for lv in levels if lv not in names]
    if unknown:
        raise HTTPException(422, f"Unknown field(s): {', '.join(unknown)}")
    return levels


@router.get("/datasets/{dataset_id}/hierarchies", response_model=list[HierarchyOut])
def list_hierarchies(dataset_id: int, db: Session = Depends(get_db), _: User = Depends(require_creator)):
    _dataset(db, dataset_id)
    return list(
        db.scalars(select(Hierarchy).where(Hierarchy.dataset_id == dataset_id).order_by(Hierarchy.name))
    )


@router.post(
    "/datasets/{dataset_id}/hierarchies", response_model=HierarchyOut, status_code=status.HTTP_201_CREATED
)
def create_hierarchy(
    dataset_id: int, body: HierarchyIn, db: Session = Depends(get_db), user: User = Depends(require_creator)
):
    _editable(db, dataset_id, user)
    h = Hierarchy(dataset_id=dataset_id, name=body.name.strip(), levels=_check_levels(db, dataset_id, body))
    db.add(h)
    db.commit()
    return h


@router.put("/datasets/{dataset_id}/hierarchies/{hierarchy_id}", response_model=HierarchyOut)
def update_hierarchy(
    dataset_id: int,
    hierarchy_id: int,
    body: HierarchyIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    _editable(db, dataset_id, user)
    h = db.get(Hierarchy, hierarchy_id)
    if not h or h.dataset_id != dataset_id:
        raise HTTPException(404, "Hierarchy not found")
    h.name, h.levels = body.name.strip(), _check_levels(db, dataset_id, body)
    db.commit()
    return h


@router.delete("/datasets/{dataset_id}/hierarchies/{hierarchy_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_hierarchy(
    dataset_id: int, hierarchy_id: int, db: Session = Depends(get_db), user: User = Depends(require_creator)
):
    _editable(db, dataset_id, user)
    h = db.get(Hierarchy, hierarchy_id)
    if not h or h.dataset_id != dataset_id:
        raise HTTPException(404, "Hierarchy not found")
    db.delete(h)
    db.commit()


# ------------------------------------------------------------------ parameters
def _param_out(p: Parameter) -> ParameterOut:
    return ParameterOut(
        id=p.id,
        name=p.name,
        description=p.description,
        data_type=p.data_type,
        default_value=model.jsonable((p.default_value or {}).get("value")),
        allowed_values=p.allowed_values or [],
    )


def _apply_param(p: Parameter, body: ParameterIn) -> None:
    p.name, p.description, p.data_type = body.name.strip(), body.description, body.data_type
    p.allowed_values = []
    try:
        allowed = [
            model.jsonable(model.coerce_param(p, v)) for v in body.allowed_values if v not in (None, "")
        ]
        p.allowed_values = allowed
        default = model.coerce_param(p, body.default_value) if body.default_value not in (None, "") else None
    except FormulaError as exc:
        raise HTTPException(422, exc.message) from exc
    if default is None and allowed:
        default = model.coerce_param(p, allowed[0])
    p.default_value = {"value": model.jsonable(default)}


def _params_used(db: Session, dataset_id: int, name: str) -> list[str]:
    used = []
    for f in model.fields_of(db, dataset_id):
        if f.formula:
            try:
                if any(t.kind == "param" and t.value.lower() == name.lower() for t in tokenize(f.formula)):
                    used.append(f.name)
            except FormulaError:
                continue
    return used


@router.get("/datasets/{dataset_id}/parameters", response_model=list[ParameterOut])
def list_parameters(dataset_id: int, db: Session = Depends(get_db), _: User = Depends(require_creator)):
    _dataset(db, dataset_id)
    return [
        _param_out(p)
        for p in db.scalars(
            select(Parameter).where(Parameter.dataset_id == dataset_id).order_by(Parameter.name)
        )
    ]


@router.post(
    "/datasets/{dataset_id}/parameters", response_model=ParameterOut, status_code=status.HTTP_201_CREATED
)
def create_parameter(
    dataset_id: int, body: ParameterIn, db: Session = Depends(get_db), user: User = Depends(require_creator)
):
    ds = _editable(db, dataset_id, user)
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_ ]*$", body.name.strip()):
        raise HTTPException(
            422, "Parameter names use letters, numbers, spaces and _ (starting with a letter)"
        )
    if db.scalar(
        select(Parameter).where(Parameter.dataset_id == dataset_id, Parameter.name == body.name.strip())
    ):
        raise HTTPException(409, "A parameter with this name already exists")
    p = Parameter(dataset_id=dataset_id)
    _apply_param(p, body)
    db.add(p)
    db.flush()
    model.recheck_calculated(db, ds)
    db.commit()
    return _param_out(p)


@router.put("/datasets/{dataset_id}/parameters/{parameter_id}", response_model=ParameterOut)
def update_parameter(
    dataset_id: int,
    parameter_id: int,
    body: ParameterIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    ds = _editable(db, dataset_id, user)
    p = db.get(Parameter, parameter_id)
    if not p or p.dataset_id != dataset_id:
        raise HTTPException(404, "Parameter not found")
    if body.name.strip() != p.name and _params_used(db, dataset_id, p.name):
        raise HTTPException(409, f"@{p.name} is used by formulas; rename it there first")
    _apply_param(p, body)
    db.flush()
    model.recheck_calculated(db, ds)
    db.commit()
    return _param_out(p)


@router.delete("/datasets/{dataset_id}/parameters/{parameter_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_parameter(
    dataset_id: int, parameter_id: int, db: Session = Depends(get_db), user: User = Depends(require_creator)
):
    _editable(db, dataset_id, user)
    p = db.get(Parameter, parameter_id)
    if not p or p.dataset_id != dataset_id:
        raise HTTPException(404, "Parameter not found")
    used = _params_used(db, dataset_id, p.name)
    if used:
        raise HTTPException(409, f"@{p.name} is used by {', '.join(used)}. Change those first.")
    db.delete(p)
    db.commit()
