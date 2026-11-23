"""What a model proposes for a chart (an "intent"), checked against the dataset and turned into a query
spec and a chart builder config.

Models only ever propose field names and operators; every name is checked against the fields this person
may see, values are bound by the query engine, and the query runs with their row-level security.

Intent:
{"title": "Sales by region", "chart": "bar",
 "dimensions": [{"field": "Order Date", "grain": "month"}, {"field": "Region"}],
 "measures": [{"field": "Amount", "agg": "sum"}],
 "filters": [{"field": "Status", "op": "in", "values": ["Won"]},
             {"field": "Order Date", "op": "relative", "unit": "month", "n": 12}],
 "sort": {"by": "Amount", "dir": "desc"}, "limit": 10, "note": "Won orders only, last 12 months"}
"""

from typing import Any

from pydantic import ValidationError

from app.models import DatasetField
from app.query.spec import QuerySpec
from app.services.ai.providers import AiError

CHARTS = (
    "auto", "table", "kpi", "bar", "stacked", "line", "area", "combo", "donut", "scatter", "heatmap",
    "treemap", "map", "funnel", "gauge",
)  # fmt: skip
GRAINS = ("fiscal_year", "fiscal_quarter", "year", "quarter", "month", "week", "day", "hour")
AGGS = ("sum", "avg", "count", "count_distinct", "min", "max", "median")
OPS = (
    "in", "not_in", "eq", "neq", "gt", "gte", "lt", "lte", "between", "relative", "top", "bottom", "contains",
    "not_contains", "starts_with", "ends_with",
)  # fmt: skip
UNITS = ("day", "week", "month", "quarter", "year", "fiscal_quarter", "fiscal_year")
MAX_DIMS = 3
MAX_MEASURES = 4
MAX_LIMIT = 5000


def _resolve(fields: dict[str, DatasetField], name: Any, what: str) -> DatasetField:
    if not isinstance(name, str) or not name.strip():
        raise AiError(f"The AI model left out a {what}")
    key = name.strip().lower()
    for f in fields.values():
        if f.name.lower() == key or (f.label and f.label.lower() == key):
            return f
    raise AiError(f'The AI model used a field that does not exist: "{name}"')


def _clean_filter(fields: dict[str, DatasetField], f: Any) -> dict:
    if not isinstance(f, dict):
        raise AiError("The AI model proposed a filter Lumen could not read")
    field = _resolve(fields, f.get("field"), "filter field")
    op = f.get("op")
    if op not in OPS:
        raise AiError(f'The AI model used a filter Lumen does not know: "{op}"')
    out: dict[str, Any] = {"field": field.name}
    if f.get("grain") in GRAINS:
        out["grain"] = f["grain"]
    if f.get("agg") in AGGS:
        out["agg"] = f["agg"]
    # Map the simple comparisons onto the filters the chart builder can show, so the saved chart and its
    # query always agree
    if op in ("eq", "neq"):
        out |= {"op": "in" if op == "eq" else "not_in", "values": [f.get("value")]}
    elif op in ("in", "not_in"):
        values = f.get("values") if isinstance(f.get("values"), list) else [f.get("value")]
        out |= {"op": op, "values": values[:500]}
    elif op in ("gt", "gte"):
        out |= {"op": "between", "values": [f.get("value"), None]}
    elif op in ("lt", "lte"):
        out |= {"op": "between", "values": [None, f.get("value")]}
    elif op == "between":
        values = f.get("values") if isinstance(f.get("values"), list) else []
        out |= {"op": "between", "values": (values + [None, None])[:2]}
    elif op == "relative":
        if f.get("unit") not in UNITS:
            raise AiError("The AI model proposed a date filter without a period")
        out |= {"op": "relative", "unit": f["unit"], "n": max(1, min(int(f.get("n") or 1), 1000))}
    elif op in ("top", "bottom"):
        by = f.get("by") if isinstance(f.get("by"), dict) else {}
        by_field = _resolve(fields, by.get("field"), "field to rank by")
        agg = by.get("agg") if by.get("agg") in AGGS else None
        out |= {"op": op, "n": max(1, min(int(f.get("n") or 10), 1000)), "by": {"field": by_field.name}}
        if agg:
            out["by"]["agg"] = agg
    else:
        out |= {"op": op, "value": str(f.get("value") or "")}
    return out


def build(intent: dict, fields: list[DatasetField], dataset_id: int) -> tuple[QuerySpec, dict, dict]:
    """(query spec, chart builder config, cleaned intent). Raises AiError when the intent does not fit."""
    usable = {f.name: f for f in fields}
    chart = intent.get("chart") if intent.get("chart") in CHARTS else "auto"
    dims_in = intent.get("dimensions") if isinstance(intent.get("dimensions"), list) else []
    measures_in = intent.get("measures") if isinstance(intent.get("measures"), list) else []
    dims: list[dict] = []
    pills_d: list[dict] = []
    for i, d in enumerate(dims_in[:MAX_DIMS]):
        d = d if isinstance(d, dict) else {"field": d}
        f = _resolve(usable, d.get("field"), "dimension")
        key = f"d{i}"
        spec: dict[str, Any] = {"field": f.name, "key": key}
        if f.data_type in ("date", "datetime"):
            spec["grain"] = d.get("grain") if d.get("grain") in GRAINS else "month"
        dims.append(spec)
        pills_d.append(
            {
                "id": key,
                "field": f.name,
                "role": "dimension",
                **({"grain": spec["grain"]} if "grain" in spec else {}),
            }
        )
    measures: list[dict] = []
    pills_m: list[dict] = []
    for i, m in enumerate(measures_in[:MAX_MEASURES]):
        m = m if isinstance(m, dict) else {"field": m}
        key = f"m{i}"
        if m.get("field") in ("*", "rows", "Number of rows"):
            spec = {"field": "*", "agg": "count", "key": key}
        else:
            f = _resolve(usable, m.get("field"), "measure")
            agg = m.get("agg") if m.get("agg") in AGGS else None
            if f.is_aggregate:
                agg = None
            elif agg is None:
                agg = f.default_agg if f.role == "measure" else "count_distinct"
            spec = {"field": f.name, "key": key, **({"agg": agg} if agg else {})}
        measures.append(spec)
        pills_m.append(
            {
                "id": key,
                "field": spec["field"],
                "role": "measure",
                **({"agg": spec["agg"]} if "agg" in spec else {}),
            }
        )
    if not dims and not measures:
        raise AiError("The AI model did not choose any fields. Try naming what you want to see.")
    filters = [_clean_filter(usable, f) for f in (intent.get("filters") or [])[:10]]

    sort_in = intent.get("sort")
    sort: list[dict] = []
    sort_pill: dict | None = None
    if isinstance(sort_in, list):
        sort_in = sort_in[0] if sort_in else None
    if isinstance(sort_in, dict) and sort_in.get("by"):
        wanted = str(sort_in["by"]).strip().lower()
        for s, p in zip(dims + measures, pills_d + pills_m, strict=True):
            label = usable[s["field"]].label.lower() if s["field"] in usable else ""
            if wanted in (s["field"].lower(), s["key"], label) or (
                wanted in ("*", "rows") and s["field"] == "*"
            ):
                direction = "asc" if sort_in.get("dir") == "asc" else "desc"
                sort = [{"key": s["key"], "dir": direction}]
                sort_pill = {"pill": p["id"], "dir": direction}
                break
    try:
        limit = max(1, min(int(intent.get("limit") or MAX_LIMIT), MAX_LIMIT))
    except (TypeError, ValueError):
        limit = MAX_LIMIT
    if chart == "kpi" and dims:
        chart = "auto"
    totals = chart in ("kpi", "gauge")
    try:
        spec = QuerySpec(
            dataset=dataset_id,
            dimensions=dims,
            measures=measures,
            filters=filters,
            sort=sort,
            limit=limit,
            totals=totals,
        )
    except ValidationError as exc:
        raise AiError(f"The AI model proposed a query Lumen could not use: {exc.errors()[0]['msg']}") from exc

    title = str(intent.get("title") or "").strip()[:200]
    config = {
        "version": 1,
        "type": chart,
        "shelves": _shelves(chart, pills_d, pills_m),
        "filters": [_filter_pill(f, i) for i, f in enumerate(filters)],
        "sort": sort_pill,
        "options": {"labels": "none", "legend": True, **({"title": title} if title else {})},
        "params": {},
        "limit": limit,
    }
    cleaned = {
        "title": title,
        "chart": chart,
        "note": str(intent.get("note") or "").strip()[:300],
    }
    return spec, config, cleaned


def _shelves(chart: str, dims: list[dict], measures: list[dict]) -> dict:
    shelves: dict[str, list] = {
        k: [] for k in ("columns", "rows", "color", "size", "label", "tooltip", "detail")
    }
    if chart == "scatter" and len(measures) >= 2:
        shelves["columns"] = [measures[0]]
        shelves["rows"] = measures[1:2]
        shelves["detail"] = dims[:1]
        shelves["color"] = dims[1:2]
        shelves["tooltip"] = measures[2:]
        return shelves
    if chart == "table":
        shelves["rows"] = dims
        shelves["columns"] = measures
        return shelves
    if chart in ("donut", "treemap", "funnel"):
        shelves["color"] = dims[:1]
        shelves["size"] = measures[:1]
        shelves["detail"] = dims[1:]
        shelves["tooltip"] = measures[1:]
        return shelves
    if chart == "heatmap":
        shelves["columns"] = dims[:1]
        shelves["rows"] = dims[1:2]
        shelves["color"] = measures[:1]
        shelves["detail"] = dims[2:]
        return shelves
    shelves["columns"] = dims[:1]
    shelves["rows"] = measures
    if len(dims) > 1:
        shelves["color"] = dims[1:2]
        shelves["detail"] = dims[2:]
    return shelves


def _filter_pill(f: dict, i: int) -> dict:
    base: dict[str, Any] = {
        "id": f"f{i}",
        "field": f["field"],
        **({"grain": f["grain"]} if f.get("grain") else {}),
    }
    op = f["op"]
    if op in ("in", "not_in"):
        return base | {"kind": "values", "values": f["values"], "exclude": op == "not_in"}
    if op == "between":
        lo, hi = f["values"]
        return base | {"kind": "range", "min": lo, "max": hi, **({"agg": f["agg"]} if f.get("agg") else {})}
    if op == "relative":
        return base | {"kind": "relative", "unit": f["unit"], "n": f["n"]}
    if op in ("top", "bottom"):
        return base | {"kind": "top", "top": op, "n": f["n"], "by": f["by"]}
    return base | {"kind": "text", "op": op, "text": f["value"]}
