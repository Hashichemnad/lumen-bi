"""The query engine: a query spec in, rows out.

Every field is looked up in the semantic layer and compiled by the formula compiler; user values (filter
values, row-level security values) are bound parameters. The dataset's snapshot is read in exactly one
place, the `base` CTE, which carries the row-level security condition, so nothing (filters, top-N, totals,
table calculations, value lists, exports) can see rows the user may not see.

Shape of an aggregate query:

    WITH base AS (SELECT * FROM read_parquet(...) WHERE <row-level security>),
         q    AS (SELECT <dimensions>, <measures> FROM base WHERE <filters> GROUP BY ... HAVING ...),
         calc AS (SELECT <dimensions>, <table calculations over q>)
    SELECT * FROM calc ORDER BY ... LIMIT ...
"""

import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Dataset, DatasetField, User
from app.query import cache, duck, forecast, masking, rls
from app.query.rls import Params, Principal
from app.query.spec import Dimension, Filter, Measure, QuerySpec, TableCalc
from app.semantic import model
from app.semantic.formula import NUMERIC, Context, FormulaError
from app.services import settings, snapshots

DEFAULT_LIMIT = 5000
DATE_TYPES = {"date", "datetime"}
NUMERIC_AGGS = {"sum", "avg", "median", "percentile", "stdev", "var"}
AGG_SQL = {
    "sum": "sum({})",
    "avg": "avg({})",
    "count": "count({})",
    "count_distinct": "count(DISTINCT {})",
    "min": "min({})",
    "max": "max({})",
    "median": "median({})",
    "stdev": "stddev_samp({})",
    "var": "var_samp({})",
}
AGG_LABEL = {
    "sum": "Sum of",
    "avg": "Average",
    "count": "Count of",
    "count_distinct": "Distinct count of",
    "min": "Minimum",
    "max": "Maximum",
    "median": "Median",
    "percentile": "Percentile of",
    "stdev": "Std. dev. of",
    "var": "Variance of",
}
CALC_LABEL = {
    "running_total": "Running total",
    "pct_total": "% of total",
    "difference": "Difference",
    "pct_change": "% change",
    "rank": "Rank",
    "moving_avg": "Moving average",
}
PART_SQL = {"weekday": "isodow"}
INTERVAL_UNIT = {"day": "DAY", "week": "WEEK", "month": "MONTH", "quarter": "MONTH", "year": "YEAR"}
GRAIN_LABEL = {"fiscal_year": "Fiscal year", "fiscal_quarter": "Fiscal quarter"}


class QueryError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class OutColumn:
    key: str
    label: str
    field: str
    role: str  # dimension | measure
    type: str
    format: dict
    agg: str | None = None
    grain: str | None = None
    part: str | None = None
    calc: str | None = None

    def jsonable(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class Plan:
    sql: str
    params: list[Any]
    columns: list[OutColumn]
    files: list[tuple[str, int, int]]
    limit: int
    totals_sql: str | None = None
    restricted: bool = False
    rules: list = field(default_factory=list)


# ------------------------------------------------------------------ helpers
def to_json(v: Any) -> Any:
    if v is None or isinstance(v, bool | int | str):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, timedelta):
        return v.total_seconds()
    return str(v)


def _file_id(path: Path) -> tuple[str, int, int]:
    st = path.stat()
    return (str(path), st.st_mtime_ns, st.st_size)


class _Builder:
    def __init__(self, db: Session, ds: Dataset, spec: QuerySpec, principal: Principal):
        self.db, self.ds, self.spec, self.principal = db, ds, spec, principal
        path = snapshots.snapshot_path(ds.id, ds.current_version)
        if path is None:
            raise QueryError(f'"{ds.name}" has no data yet. Sync it first.')
        self.path = path
        self.fields = model.fields_of(db, ds.id)
        self.meta: dict[str, DatasetField] = {f.name.lower(): f for f in self.fields}
        try:
            self.ctx: Context = model.build_context(db, ds.id, params=spec.params)
        except FormulaError as exc:
            raise QueryError(exc.message) from exc
        self.params = Params()
        self.calendar = settings.regional(db)
        rls_sql, self.rule_results = rls.evaluate(db, ds, principal, self.ctx, self.params)
        self.restricted = rls_sql is not None
        # Masked columns (PII) are replaced after row-level security has chosen the rows
        replace, self.masked = masking.replacements(db, ds, principal)
        self.base = (
            f"base AS (SELECT *{replace} FROM read_parquet({snapshots._lit(path)}) WHERE {rls_sql or 'TRUE'})"
        )

    # -------------------------------------------------------------- fields
    def label(self, name: str) -> str:
        f = self.meta.get(name.lower())
        return (f.label or f.name) if f else name

    def compile(self, name: str):
        try:
            return model.compile_field(self.ctx, name)
        except FormulaError as exc:
            raise QueryError(exc.message) from exc

    def field_name(self, name: str) -> str:
        f = self.meta.get(name.lower())
        return f.name if f else name

    def row_expr(self, name: str, grain: str | None, part: str | None) -> tuple[str, str]:
        c = self.compile(name)
        if c.level == "agg":
            raise QueryError(f"[{self.field_name(name)}] is an aggregate; use it as a measure")
        sql, typ = c.sql, c.type
        if (grain or part) and typ not in DATE_TYPES:
            raise QueryError(f"[{self.field_name(name)}] is not a date")
        if grain:
            if typ == "date" and grain in {"hour", "minute"}:
                raise QueryError(f"[{self.field_name(name)}] has no time of day")
            if grain in {"hour", "minute"}:
                return f"CAST(date_trunc('{grain}', {sql}) AS TIMESTAMP)", "datetime"
            return self.period_start(grain, sql), "date"
        if part:
            off = self.calendar.fiscal_year_start - 1
            shifted = f"(CAST({sql} AS DATE) - INTERVAL {off} MONTH)"
            if part == "fiscal_year":
                # Named by the year it ends in: a year starting July 2026 is fiscal year 2027
                return f"CAST(year({shifted}) AS INTEGER) + {1 if off else 0}", "integer"
            if part == "fiscal_quarter":
                return f"CAST(quarter({shifted}) AS INTEGER)", "integer"
            return f"CAST(date_part('{PART_SQL.get(part, part)}', {sql}) AS INTEGER)", "integer"
        return sql, typ

    def period_start(self, unit: str, sql: str) -> str:
        """The first day of the period (week, month, fiscal year…) a date is in, as a DATE."""
        if unit == "week":
            ws = self.calendar.week_start
            day = f"CAST({sql} AS DATE)"
            if ws == 1:
                return f"CAST(date_trunc('week', {day}) AS DATE)"
            return f"CAST({day} - CAST((isodow({day}) - {ws} + 7) % 7 AS INTEGER) AS DATE)"
        if unit in {"fiscal_year", "fiscal_quarter"}:
            off = self.calendar.fiscal_year_start - 1
            base = "year" if unit == "fiscal_year" else "quarter"
            return (
                f"CAST(date_trunc('{base}', CAST({sql} AS DATE) - INTERVAL {off} MONTH) "
                f"+ INTERVAL {off} MONTH AS DATE)"
            )
        return f"CAST(date_trunc('{unit}', {sql}) AS DATE)"

    def measure_expr(self, m: Measure) -> tuple[str, str, str | None]:
        """(SQL, type, aggregation used or None for an aggregate field)."""
        if m.field == "*":
            if m.agg not in (None, "count"):
                raise QueryError("Only count can be used on all rows")
            return "count(*)", "integer", "count"
        c = self.compile(m.field)
        if c.level == "agg":
            return c.sql, c.type, None
        meta = self.meta.get(m.field.lower())
        agg = m.agg or (meta.default_agg if meta else None) or ("sum" if c.type in NUMERIC else "count")
        if agg == "none":
            raise QueryError(f"Choose how to total [{self.field_name(m.field)}]")
        if agg in NUMERIC_AGGS and c.type not in NUMERIC:
            raise QueryError(f"{AGG_LABEL[agg]} needs a number field; [{self.field_name(m.field)}] is not")
        if agg == "percentile":
            if m.p is None:
                raise QueryError("Choose the percentile (between 0 and 1)")
            return f"quantile_cont({c.sql}, {float(m.p)!r})", "number", agg
        out_type = {
            "count": "integer",
            "count_distinct": "integer",
            "min": c.type,
            "max": c.type,
            "sum": c.type,
        }.get(agg, "number")
        return AGG_SQL[agg].format(c.sql), out_type, agg

    def fmt(self, name: str, typ: str) -> dict:
        f = self.meta.get(name.lower())
        if f and f.format:
            return dict(f.format)
        return model.default_format(typ)

    # -------------------------------------------------------------- filters
    def value(self, v: Any, typ: str, name: str) -> Any:
        if v is None:
            return None
        coerced = rls._coerce([v], "string" if typ not in NUMERIC | DATE_TYPES | {"boolean"} else typ)
        if not coerced:
            raise QueryError(f'"{v}" is not a valid value for [{self.field_name(name)}]')
        return coerced[0]

    def condition(self, f: Filter, expr: str, typ: str) -> str:
        p, op = self.params, f.op
        values = f.values if f.values is not None else ([] if f.value is None else [f.value])
        if op in {"is_null", "not_null"}:
            return f"{expr} IS {'NOT ' if op == 'not_null' else ''}NULL"
        if op == "relative":
            if typ not in DATE_TYPES:
                raise QueryError(f"[{self.field_name(f.field)}] is not a date")
            if not f.unit or not f.n:
                raise QueryError("Choose the period for the relative date filter")
            unit, n = f.unit, int(f.n)
            step, interval = {
                "quarter": (3, "MONTH"),
                "fiscal_quarter": (3, "MONTH"),
                "fiscal_year": (12, "MONTH"),
            }.get(unit, (1, INTERVAL_UNIT.get(unit, "DAY")))
            now = self.period_start(unit, "current_date")
            start = f"{now} - INTERVAL {(n - 1) * step} {interval}"
            end = f"{now} + INTERVAL {step} {interval}"
            return f"({expr} >= {start} AND {expr} < {end})"
        if op in {"contains", "not_contains", "starts_with", "ends_with"}:
            if not values or values[0] in (None, ""):
                return "TRUE"
            text = f"lower(CAST({expr} AS VARCHAR))"
            ph = p.add(str(values[0]).lower())
            fn = {"contains": "contains", "not_contains": "contains", "starts_with": "starts_with"}.get(
                op, "ends_with"
            )
            cond = f"coalesce({fn}({text}, {ph}), FALSE)"
            return f"NOT {cond}" if op == "not_contains" else cond
        if op in {"in", "not_in"}:
            has_null = any(v is None for v in values)
            items = [self.value(v, typ, f.field) for v in values if v is not None]
            parts = []
            if items:
                parts.append(f"{expr} IN ({', '.join(p.add(v) for v in items)})")
            if has_null:
                parts.append(f"{expr} IS NULL")
            cond = "(" + " OR ".join(parts) + ")" if parts else "FALSE"
            if op == "not_in":
                return f"NOT coalesce({cond}, FALSE)" if parts else "TRUE"
            return cond
        if op == "between":
            if len(values) != 2:
                raise QueryError("A range filter needs a from and a to value")
            lo, hi = (self.value(v, typ, f.field) for v in values)
            parts = []
            if lo is not None:
                parts.append(f"{expr} >= {p.add(lo)}")
            if hi is not None:
                parts.append(f"{expr} <= {p.add(hi)}")
            return " AND ".join(parts) if parts else "TRUE"
        if len(values) != 1:
            raise QueryError(f"The filter on [{self.field_name(f.field)}] needs one value")
        v = self.value(values[0], typ, f.field)
        if v is None:
            return f"{expr} IS {'NOT ' if op == 'neq' else ''}NULL"
        sym = {"eq": "=", "neq": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
        if op == "neq":
            return f"({expr} IS NULL OR {expr} <> {p.add(v)})"
        return f"{expr} {sym} {p.add(v)}"

    def filters(self) -> tuple[list[str], list[str]]:
        """(row conditions for WHERE, aggregate conditions for HAVING)."""
        where, having, ranked = [], [], []
        for f in self.spec.filters:
            if f.op in {"top", "bottom"}:
                ranked.append(f)
                continue
            if f.field != "*":
                c = self.compile(f.field)
                is_agg = c.level == "agg" or f.agg is not None
            else:
                is_agg = True
            if is_agg:
                if f.grain or f.part:
                    raise QueryError("Aggregated filters cannot use a date grain")
                expr, typ, _ = self.measure_expr(Measure(field=f.field, agg=f.agg))
                having.append(self.condition(f, expr, typ))
            else:
                expr, typ = self.row_expr(f.field, f.grain, f.part)
                where.append(self.condition(f, expr, typ))
        # Top/bottom N: ranked within the rows left by the other filters (and row-level security)
        context = " AND ".join(where) or "TRUE"
        for f in ranked:
            if not f.n:
                raise QueryError("Choose how many values to keep")
            if f.by is None:
                raise QueryError("Choose the measure to rank by")
            expr, _ = self.row_expr(f.field, f.grain, f.part)
            by, _, _ = self.measure_expr(f.by)
            direction = "DESC" if f.op == "top" else "ASC"
            where.append(
                f"{expr} IN (SELECT {expr} FROM base WHERE {context} GROUP BY 1 "
                f"ORDER BY {by} {direction} NULLS LAST, 1 LIMIT {int(f.n)})"
            )
        return where, having

    # -------------------------------------------------------------- plans
    def limit(self, maximum: int | None = None) -> int:
        maximum = maximum or get_settings().query_max_rows
        return min(self.spec.limit or DEFAULT_LIMIT, maximum)

    def order_by(self, keys: dict[str, str], default: list[str]) -> str:
        parts = []
        for s in self.spec.sort:
            col = keys.get(s.key)
            if col is None:
                raise QueryError(f'Cannot sort by "{s.key}": it is not in the query')
            parts.append(f"{col} {s.dir.upper()} NULLS LAST")
        if not parts:
            parts = [f"{c} ASC NULLS LAST" for c in default]
        return f" ORDER BY {', '.join(parts)}" if parts else ""

    def plan_rows(self, maximum: int | None = None) -> Plan:
        spec = self.spec
        if spec.measures or spec.dimensions:
            raise QueryError("Row queries list fields, not dimensions and measures")
        names = spec.fields or [
            f.name
            for f in self.fields
            if not f.hidden and not f.missing and not f.error and not f.is_aggregate
        ]
        if not names:
            raise QueryError("Add at least one field")
        where, having = self.filters()
        if having:
            raise QueryError("Row queries cannot filter on aggregated values")
        cols, select, keys = [], [], {}
        for i, name in enumerate(names):
            expr, typ = self.row_expr(name, None, None)
            real = self.field_name(name)
            if real in keys:
                raise QueryError(f"[{real}] is listed twice")
            keys[real] = f"c{i}"
            select.append(f"{expr} AS c{i}")
            role = self.meta[real.lower()].role if real.lower() in self.meta else "dimension"
            cols.append(OutColumn(real, self.label(real), real, role, typ, self.fmt(real, typ)))
        limit = self.limit(maximum)
        sql = (
            f"WITH {self.base} SELECT {', '.join(select)} FROM base WHERE {' AND '.join(where) or 'TRUE'}"
            f"{self.order_by(keys, [])} LIMIT {limit + 1}"
        )
        return Plan(sql, self.params.values, cols, [_file_id(self.path)], limit, None, self.restricted)

    def plan_aggregate(self, maximum: int | None = None) -> Plan:
        spec = self.spec
        if not spec.dimensions and not spec.measures:
            raise QueryError("Add at least one field")
        cols: list[OutColumn] = []
        keys: dict[str, str] = {}
        dim_exprs: list[str] = []
        select: list[str] = []

        def add_key(key: str, col: str) -> None:
            if key in keys:
                raise QueryError(f'"{key}" is in the query twice')
            keys[key] = col

        for i, d in enumerate(spec.dimensions):
            expr, typ = self.row_expr(d.field, d.grain, d.part)
            real = self.field_name(d.field)
            key = d.key or real + (f":{d.grain}" if d.grain else f":part:{d.part}" if d.part else "")
            col = f"c{i}"
            add_key(key, col)
            dim_exprs.append(expr)
            select.append(f"{expr} AS {col}")
            label = self.label(real)
            if d.grain or d.part:
                level = d.grain or d.part or ""
                label += f" ({GRAIN_LABEL.get(level, level.capitalize())})"
            fmt = (
                {"type": "number", "decimals": 0, "plain": True}
                if d.part
                else ({"type": "date", "grain": d.grain} if d.grain else self.fmt(real, typ))
            )
            cols.append(OutColumn(key, label, real, "dimension", typ, fmt, grain=d.grain, part=d.part))

        measures: list[tuple[Measure, str, str, TableCalc | None]] = []
        for j, m in enumerate(spec.measures):
            expr, typ, agg = self.measure_expr(m)
            real = "*" if m.field == "*" else self.field_name(m.field)
            calc = m.calc or spec.table_calc
            key = m.key or (
                (f"{agg}:{real}" if agg else real) + (f":{calc.type}" if calc else "")
                if real != "*"
                else "count:*" + (f":{calc.type}" if calc else "")
            )
            col = f"c{len(spec.dimensions) + j}"
            add_key(key, col)
            select.append(f"{expr} AS {col}")
            measures.append((m, col, typ, calc))
            if real == "*":
                label, fmt = "Number of rows", {"type": "number", "decimals": 0}
            else:
                label = self.label(real) if agg is None else f"{AGG_LABEL.get(agg, agg)} {self.label(real)}"
                fmt = (
                    {"type": "number", "decimals": 0}
                    if agg in {"count", "count_distinct"}
                    else self.fmt(real, typ)
                )
            out_type = typ
            if calc:
                if calc.type != "rank" and typ not in NUMERIC:
                    raise QueryError(f"{CALC_LABEL[calc.type]} needs a number")
                label = f"{CALC_LABEL[calc.type]} of {label[0].lower() + label[1:]}"
                if calc.type in {"pct_total", "pct_change"}:
                    fmt, out_type = {"type": "percent", "decimals": 1}, "number"
                elif calc.type == "rank":
                    fmt, out_type = {"type": "number", "decimals": 0}, "integer"
                elif calc.type == "moving_avg":
                    out_type = "number"
            cols.append(
                OutColumn(
                    key, label, real, "measure", out_type, fmt, agg=agg, calc=calc.type if calc else None
                )
            )

        where, having = self.filters()
        n_dims = len(spec.dimensions)
        group = f" GROUP BY {', '.join(str(i + 1) for i in range(n_dims))}" if n_dims else ""
        q = (
            f"q AS (SELECT {', '.join(select)} FROM base WHERE {' AND '.join(where) or 'TRUE'}{group}"
            + (f" HAVING {' AND '.join(having)}" if having else "")
            + ")"
        )
        ctes = [self.base, q]
        source = "q"
        if any(calc for *_, calc in measures):
            ctes.append(self.calc_cte(cols, keys, measures, n_dims))
            source = "calc"
        limit = self.limit(maximum)
        sql = (
            f"WITH {', '.join(ctes)} SELECT * FROM {source}"
            f"{self.order_by(keys, [f'c{i}' for i in range(n_dims)])} LIMIT {limit + 1}"
        )

        totals_sql = None
        if spec.totals and measures:
            restrict = ""
            if having and n_dims:
                match = " AND ".join(f"q.c{i} IS NOT DISTINCT FROM {e}" for i, e in enumerate(dim_exprs))
                restrict = f" AND EXISTS (SELECT 1 FROM q WHERE {match})"
            total_cols = ", ".join("NULL" if calc else self.measure_expr(m)[0] for m, _, _, calc in measures)
            totals_sql = (
                f"WITH {self.base}, {q} SELECT {total_cols} FROM base "
                f"WHERE {' AND '.join(where) or 'TRUE'}{restrict}"
            )
        return Plan(sql, self.params.values, cols, [_file_id(self.path)], limit, totals_sql, self.restricted)

    def calc_cte(self, cols: list[OutColumn], keys: dict[str, str], measures, n_dims: int) -> str:
        dim_cols = [f"c{i}" for i in range(n_dims)]
        dim_keys = {c.key: f"c{i}" for i, c in enumerate(cols[:n_dims])}
        parts = list(dim_cols)
        for _m, col, _typ, calc in measures:
            if not calc:
                parts.append(col)
                continue
            if calc.along is not None and calc.along not in dim_keys:
                raise QueryError(f'"{calc.along}" is not a dimension of this query')
            along = dim_keys.get(calc.along) if calc.along else None
            if along is None and n_dims:
                dates = [f"c{i}" for i, c in enumerate(cols[:n_dims]) if c.type in DATE_TYPES or c.part]
                along = dates[0] if dates else "c0"
            if calc.partition_by is not None:
                missing = [k for k in calc.partition_by if k not in dim_keys]
                if missing:
                    raise QueryError(f'"{missing[0]}" is not a dimension of this query')
                partition = [dim_keys[k] for k in calc.partition_by]
            else:
                partition = [c for c in dim_cols if c != along]
            p = f"PARTITION BY {', '.join(partition)}" if partition else ""
            o = f"ORDER BY {along} ASC NULLS LAST" if along else ""
            over = f"OVER ({p} {o})".replace("( ", "(").replace(" )", ")")
            if calc.type == "running_total":
                frame = f"OVER ({p} {o} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)".replace("( ", "(")
                expr = f"sum({col}) {frame}"
            elif calc.type == "pct_total":
                expr = f"{col} / nullif(sum({col}) OVER ({p}), 0)".replace("( )", "()")
            elif calc.type == "difference":
                expr = f"{col} - lag({col}) {over}"
            elif calc.type == "pct_change":
                expr = f"({col} - lag({col}) {over}) / nullif(abs(lag({col}) {over}), 0)"
            elif calc.type == "rank":
                rank_p = f"PARTITION BY {', '.join(partition)} " if partition else ""
                expr = f"rank() OVER ({rank_p}ORDER BY {col} DESC NULLS LAST)"
            else:  # moving_avg
                frame = f"ROWS BETWEEN {int(calc.window) - 1} PRECEDING AND CURRENT ROW"
                expr = f"avg({col}) OVER ({p} {o} {frame})".replace("( ", "(")
            parts.append(f"{expr} AS {col}")
        return f"calc AS (SELECT {', '.join(parts)} FROM q)"


# ------------------------------------------------------------------ running
def _dataset(db: Session, dataset_id: int) -> Dataset:
    ds = db.get(Dataset, dataset_id)
    if ds is None:
        raise QueryError("Dataset not found")
    return ds


def plan(db: Session, spec: QuerySpec, principal: Principal, maximum: int | None = None) -> Plan:
    b = _Builder(db, _dataset(db, spec.dataset), spec, principal)
    p = b.plan_rows(maximum) if spec.mode == "rows" else b.plan_aggregate(maximum)
    p.rules = b.rule_results
    return p


def _execute(sql: str, params: list[Any]) -> list[tuple]:
    try:
        _, rows = duck.run(sql, params)
    except duck.QueryTimeout:
        raise
    except duckdb.Error as exc:
        raise QueryError(f"The query could not run: {str(exc).splitlines()[0]}") from exc
    return rows


def run(db: Session, spec: QuerySpec, principal: Principal) -> dict:
    started = time.perf_counter()
    p = plan(db, spec, principal)
    key = cache.make_key(p.sql + (p.totals_sql or ""), p.params, p.files)
    hit = cache.get(key)
    if hit is None:
        rows = _execute(p.sql, p.params)
        totals = None
        if p.totals_sql:
            totals = [to_json(v) for v in _execute(p.totals_sql, p.params)[0]]
            totals = [None] * (len(p.columns) - len(totals)) + totals
        hit = {
            "rows": [[to_json(v) for v in r] for r in rows[: p.limit]],
            "truncated": len(rows) > p.limit,
            "totals": totals,
        }
        cache.put(key, hit)
        cached = False
    else:
        cached = True
    columns = [c.jsonable() for c in p.columns]
    out = {
        "columns": columns,
        **hit,
        "row_count": len(hit["rows"]),
        "restricted": p.restricted,
        "cached": cached,
    }
    if spec.forecast and spec.mode == "aggregate":
        f = spec.forecast
        out["forecast"] = forecast.compute(
            columns, hit["rows"], f.periods, f.interval, f.seasonality, f.exclude_current
        )
    out["ms"] = round((time.perf_counter() - started) * 1000)
    return out


def stream_rows(db: Session, spec: QuerySpec, principal: Principal):
    """(columns, iterator of row lists) for exports, up to export_max_rows."""
    maximum = get_settings().export_max_rows
    if spec.limit is None:
        spec = spec.model_copy(update={"limit": maximum})
    p = plan(db, spec, principal, maximum)
    sql = p.sql.rsplit(" LIMIT ", 1)[0] + f" LIMIT {p.limit}"

    def rows():
        it = duck.stream(sql, p.params)
        next(it)
        for batch in it:
            yield [[to_json(v) for v in r] for r in batch]

    return p.columns, rows()


def field_values(
    db: Session,
    dataset_id: int,
    principal: Principal,
    name: str,
    *,
    search: str = "",
    limit: int = 200,
    order: str = "value",
    params: dict[str, Any] | None = None,
) -> dict:
    """Distinct values of a field the user may see (for filter lists and group editors)."""
    ds = _dataset(db, dataset_id)
    b = _Builder(db, ds, QuerySpec(dataset=ds.id, params=params or {}), principal)
    expr, _ = b.row_expr(name, None, None)
    cond = "TRUE"
    if search:
        cond = f"coalesce(contains(lower(CAST(v AS VARCHAR)), {b.params.add(search.lower())}), FALSE)"
    ordering = "n DESC, v ASC NULLS LAST" if order == "frequency" else "v ASC NULLS LAST"
    sql = (
        f"WITH {b.base} SELECT v, count(*) AS n FROM (SELECT {expr} AS v FROM base) WHERE {cond} "
        f"GROUP BY v ORDER BY {ordering} LIMIT {int(limit) + 1}"
    )
    key = cache.make_key(sql, b.params.values, [_file_id(b.path)])
    hit = cache.get(key)
    if hit is None:
        rows = _execute(sql, b.params.values)
        hit = {
            "values": [to_json(r[0]) for r in rows[:limit]],
            "counts": [r[1] for r in rows[:limit]],
            "truncated": len(rows) > limit,
        }
        cache.put(key, hit)
    return {**hit, "restricted": b.restricted}


def visible_rows(db: Session, ds: Dataset, principal: Principal) -> tuple[int, list]:
    b = _Builder(db, ds, QuerySpec(dataset=ds.id), principal)
    rows = _execute(f"WITH {b.base} SELECT count(*) FROM base", b.params.values)
    return int(rows[0][0]), b.rule_results


def base_sql(db: Session, ds: Dataset, principal: Principal) -> tuple[str, list[Any], Context, bool]:
    """`WITH base AS (...)` for other server code (previews, formula samples) that reads a dataset."""
    b = _Builder(db, ds, QuerySpec(dataset=ds.id), principal)
    return f"WITH {b.base}", b.params.values, b.ctx, b.restricted


__all__ = [
    "Dimension",
    "QueryError",
    "QuerySpec",
    "User",
    "field_values",
    "plan",
    "run",
    "stream_rows",
    "visible_rows",
]
