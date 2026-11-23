"""The semantic layer: fields people see on top of a dataset's columns, and how to turn them into SQL.

Used by the Model screens now, and by the query engine (milestone 7) to compile charts.
"""

import re
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Dataset, DatasetField, Hierarchy, Parameter
from app.semantic.formula import (
    NUMERIC,
    Compiled,
    Context,
    FieldSource,
    FormulaError,
    quote_ident,
    referenced_fields,
    sql_literal,
)

ROLES = {"dimension", "measure"}
AGGREGATIONS = {"sum", "avg", "count", "count_distinct", "min", "max", "median", "none"}
FORMAT_TYPES = {"number", "currency", "percent", "date", "datetime", "text"}
PARAM_TYPES = {"number", "integer", "string", "date", "boolean"}
# Numbers that identify rather than measure (OrderNumber, AccountId, FiscalYear, PostalCode) default to
# dimensions; NumberOfEmployees or Revenue stay measures.
HINT_SNAKE = re.compile(r"(^|_)(id|key|code|no|number|year)$|zip|postal|phone|mobile", re.I)
HINT_CAMEL = re.compile(r"[a-z0-9](Id|Key|Code|No|Number|Year)$")


# ------------------------------------------------------------------ defaults for new columns
def prettify(name: str) -> str:
    """order_date -> Order Date, AnnualRevenue -> Annual Revenue, Account.Name -> Account Name,
    Foo__c -> Foo."""
    text = re.sub(r"__c$", "", name)
    text = text.replace(".", " ").replace("_", " ")
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    words = [w if w.isupper() and len(w) > 1 else w.capitalize() for w in text.split()]
    return " ".join(words) or name


def default_role(name: str, data_type: str) -> str:
    if data_type in NUMERIC and not (HINT_SNAKE.search(name) or HINT_CAMEL.search(name)):
        return "measure"
    return "dimension"


def default_format(data_type: str) -> dict:
    return {
        "integer": {"type": "number", "decimals": 0},
        "number": {"type": "number", "decimals": 2},
        "date": {"type": "date"},
        "datetime": {"type": "datetime"},
    }.get(data_type, {"type": "text"})


# ------------------------------------------------------------------ fields
def fields_of(db: Session, dataset_id: int) -> list[DatasetField]:
    return list(
        db.scalars(
            select(DatasetField)
            .where(DatasetField.dataset_id == dataset_id)
            .order_by(DatasetField.position, DatasetField.id)
        )
    )


def reconcile_fields(db: Session, dataset: Dataset) -> None:
    """Bring column fields in line with the dataset's current columns, keeping every user edit.
    New columns are added with sensible defaults; vanished ones are marked missing (not deleted, so
    labels and formats come back if the column returns). Then every formula is re-checked."""
    existing = {f.name: f for f in fields_of(db, dataset.id)}
    next_pos = max((f.position for f in existing.values()), default=-1) + 1
    current = {c["name"]: c["type"] for c in dataset.columns or []}
    for name, col_type in current.items():
        f = existing.get(name)
        if f is None:
            db.add(
                DatasetField(
                    dataset_id=dataset.id,
                    name=name,
                    kind="column",
                    label=prettify(name),
                    role=default_role(name, col_type),
                    data_type=col_type,
                    format=default_format(col_type),
                    default_agg="sum" if col_type in NUMERIC else "count_distinct",
                    position=next_pos,
                )
            )
            next_pos += 1
        elif f.kind == "column":
            if f.data_type != col_type:
                f.data_type = col_type
            f.missing = False
    for f in existing.values():
        if f.kind == "column" and f.name not in current:
            f.missing = True
    db.flush()
    recheck_calculated(db, dataset)


def parameter_values(
    db: Session, dataset_id: int, overrides: dict[str, Any] | None = None
) -> dict[str, tuple]:
    out = {}
    for p in db.scalars(select(Parameter).where(Parameter.dataset_id == dataset_id)):
        value = (p.default_value or {}).get("value")
        if overrides and p.name in overrides:
            value = coerce_param(p, overrides[p.name])
        out[p.name] = (p.data_type, coerce_param(p, value) if value is not None else None)
    return out


def coerce_param(p: Parameter, value: Any) -> Any:
    if value is None:
        return None
    try:
        if p.data_type == "number":
            value = float(value)
        elif p.data_type == "integer":
            value = int(value)
        elif p.data_type == "boolean":
            value = value if isinstance(value, bool) else str(value).lower() in {"true", "1", "yes"}
        elif p.data_type == "date":
            value = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
        else:
            value = str(value)
    except (TypeError, ValueError) as exc:
        raise FormulaError(f"@{p.name} must be a {p.data_type}") from exc
    if p.allowed_values and value not in [coerce_param_raw(p, v) for v in p.allowed_values]:
        raise FormulaError(f"@{p.name} must be one of its allowed values")
    return value


def coerce_param_raw(p: Parameter, v: Any) -> Any:
    try:
        return coerce_param(Parameter(name=p.name, data_type=p.data_type, allowed_values=[]), v)
    except FormulaError:
        return v


def build_context(
    db: Session,
    dataset_id: int,
    *,
    alias: str | None = None,
    params: dict[str, Any] | None = None,
    fields: list[DatasetField] | None = None,
) -> Context:
    """Every field of the dataset as something the formula compiler can reference."""
    fields = fields if fields is not None else fields_of(db, dataset_id)
    prefix = f"{quote_ident(alias)}." if alias else ""
    ctx = Context(fields={}, params=parameter_values(db, dataset_id, params))
    for f in fields:
        if f.kind == "column":
            if f.missing:
                continue
            ctx.fields[f.name] = FieldSource(f.name, f.data_type, sql=f"{prefix}{quote_ident(f.name)}")
        elif f.kind == "calculated" and f.formula:
            ctx.fields[f.name] = FieldSource(f.name, f.data_type, formula=f.formula)
        elif f.kind in {"group", "bin"}:
            ctx.fields[f.name] = FieldSource(f.name, f.data_type, custom_sql=_custom(ctx, f))
    return ctx


def _custom(ctx: Context, f: DatasetField):
    def build() -> Compiled:
        from app.semantic.formula import Compiler

        cfg = f.config or {}
        source = Compiler(ctx).field_compiled(cfg.get("source", ""), 0)
        if source.level == "agg":
            raise FormulaError(f"[{f.name}] cannot be based on an aggregated field")
        if f.kind == "bin":
            size = float(cfg.get("size") or 0)
            if source.type not in NUMERIC or size <= 0:
                raise FormulaError(f"Bins of [{f.name}] need a number field and a size above 0")
            size_sql = sql_literal(int(size) if size.is_integer() else size)
            return Compiled(f"(floor({source.sql} / {size_sql}) * {size_sql})", "number", "row")
        whens = []
        for g in cfg.get("groups", []):
            values = [v for v in g.get("values", []) if v is not None]
            if values:
                whens.append(
                    f"WHEN {source.sql} IN ({', '.join(sql_literal(v) for v in values)}) "
                    f"THEN {sql_literal(str(g.get('name', '')))}"
                )
        other = cfg.get("other")
        fallback = (
            sql_literal(str(other))
            if other not in (None, "")
            else (source.sql if source.type == "string" else f"CAST({source.sql} AS VARCHAR)")
        )
        if not whens:
            return Compiled(f"({fallback})", "string", "row")
        return Compiled(f"(CASE {' '.join(whens)} ELSE {fallback} END)", "string", "row")

    return build


def compile_field(ctx: Context, name: str) -> Compiled:
    from app.semantic.formula import Compiler

    return Compiler(ctx).field_compiled(name, 0)


def recheck_calculated(db: Session, dataset: Dataset) -> None:
    """Recompile every calculated/group/bin field; record its type or the error that now stops it."""
    fields = fields_of(db, dataset.id)
    for f in fields:
        if f.kind == "column":
            continue
        ctx = build_context(db, dataset.id, fields=fields)
        try:
            compiled = compile_field(ctx, f.name)
        except FormulaError as exc:
            f.error = exc.message
            continue
        f.error, f.data_type, f.is_aggregate = None, compiled.type, compiled.level == "agg"
    db.flush()


def dependents(fields: list[DatasetField], name: str) -> list[str]:
    """Calculated fields that use [name] directly."""
    out = []
    for f in fields:
        if (
            (
                f.kind == "calculated"
                and f.formula
                and name.lower() in {r.lower() for r in referenced_fields(f.formula)}
            )
            or f.kind in {"group", "bin"}
            and str((f.config or {}).get("source", "")).lower() == name.lower()
        ):
            out.append(f.name)
    return out


def hierarchies_using(db: Session, dataset_id: int, name: str) -> list[str]:
    return [
        h.name
        for h in db.scalars(select(Hierarchy).where(Hierarchy.dataset_id == dataset_id))
        if name in (h.levels or [])
    ]


def jsonable(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if hasattr(value, "is_finite") or type(value).__name__ == "Decimal":
        return float(value)
    return value
