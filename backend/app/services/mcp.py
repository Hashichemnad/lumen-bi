"""The Lumen MCP server: lets Claude and other MCP apps read Lumen as the signed-in person.

Transport: Streamable HTTP, stateless (each POST is one JSON-RPC message answered with JSON; no SSE).
Every tool is read-only, runs with the person's own permissions and row-level security, and is recorded in
the audit log. Raw rows (not aggregated) need the Explorer role, like exports.
"""

import json
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Chart, Dashboard, Dataset, Role, User
from app.query import duck, engine, rls
from app.query.spec import QuerySpec
from app.services import audit, kpi, workspaces
from app.services import dashboards as svc
from app.services.ai import context

VERSION = "0.13.0"
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
MAX_ROWS = 1000
DEFAULT_ROWS = 200
INSTRUCTIONS = (
    "Lumen BI holds the organisation's dashboards and datasets. Use search to find them, describe_dataset to "
    "see a dataset's fields, then run_query with exact field names. Everything you see is limited to what "
    "the signed-in person may see. Prefer aggregated queries (dimensions + measures) over raw rows."
)


class ToolError(Exception):
    pass


def _link(kind: str, id_: int) -> str:
    from app.services.oauth import base_url

    return f"{base_url()}/{kind}/{id_}"


# ------------------------------------------------------------------ tool definitions
FILTER_SCHEMA = {
    "type": "object",
    "properties": {
        "field": {"type": "string"},
        "op": {
            "type": "string",
            "enum": [
                "eq",
                "neq",
                "in",
                "not_in",
                "gt",
                "gte",
                "lt",
                "lte",
                "between",
                "contains",
                "starts_with",
                "ends_with",
                "is_null",
                "not_null",
                "relative",
                "top",
                "bottom",
            ],
        },  # fmt: skip
        "value": {},
        "values": {"type": "array"},
        "unit": {
            "type": "string",
            "enum": ["day", "week", "month", "quarter", "year", "fiscal_quarter", "fiscal_year"],
        },
        "n": {"type": "integer"},
        "by": {"type": "object", "properties": {"field": {"type": "string"}, "agg": {"type": "string"}}},
        "agg": {"type": "string", "description": "Set to filter on the aggregated value (after grouping)"},
    },
    "required": ["field", "op"],
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search",
        "title": "Search Lumen",
        "description": "Find dashboards, charts and datasets the signed-in person can open. "
        "Leave query empty to list everything (up to 50 of each).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words in the name or description"},
                "kind": {
                    "type": "string",
                    "enum": ["all", "dashboards", "charts", "datasets"],
                    "default": "all",
                },
            },
        },
    },
    {
        "name": "describe_dataset",
        "title": "Describe a dataset",
        "description": "Fields of a dataset (name, role, type, description) with common values of text "
        "fields and date ranges. Use the exact field names in run_query.",
        "inputSchema": {
            "type": "object",
            "properties": {"dataset_id": {"type": "integer"}},
            "required": ["dataset_id"],
        },
    },
    {
        "name": "run_query",
        "title": "Query a dataset",
        "description": "Run a query. Aggregated (default): dimensions to group by, measures to aggregate. "
        "Dates in dimensions take a grain (year, quarter, month, week, day, fiscal_year, fiscal_quarter). "
        "Relative date filter: op relative, unit month, n 12 = the last 12 months including this one. "
        'Use {"field": "*", "agg": "count"} for the number of rows. '
        "Rows mode returns raw rows (Explorers only).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dataset_id": {"type": "integer"},
                "mode": {"type": "string", "enum": ["aggregate", "rows"], "default": "aggregate"},
                "dimensions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"field": {"type": "string"}, "grain": {"type": "string"}},
                        "required": ["field"],
                    },
                },
                "measures": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string"},
                            "agg": {
                                "type": "string",
                                "enum": [
                                    "sum",
                                    "avg",
                                    "count",
                                    "count_distinct",
                                    "min",
                                    "max",
                                    "median",
                                    "percentile",
                                    "stdev",
                                    "var",
                                ],
                            },
                        },  # fmt: skip
                        "required": ["field"],
                    },
                },
                "fields": {"type": "array", "items": {"type": "string"}, "description": "Rows mode: columns"},
                "filters": {"type": "array", "items": FILTER_SCHEMA},
                "sort": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string"},
                            "dir": {"type": "string", "enum": ["asc", "desc"]},
                        },
                    },
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_ROWS, "default": DEFAULT_ROWS},
            },
            "required": ["dataset_id"],
        },
    },
    {
        "name": "get_dashboard",
        "title": "Open a dashboard",
        "description": "A dashboard's tabs and tiles (ids, titles, which chart and dataset each uses), its "
        "filters and KPI goals. Use query_dashboard_tile to get a tile's numbers.",
        "inputSchema": {
            "type": "object",
            "properties": {"dashboard_id": {"type": "integer"}},
            "required": ["dashboard_id"],
        },
    },
    {
        "name": "query_dashboard_tile",
        "title": "Get a dashboard tile's data",
        "description": "The data behind one chart tile, as the signed-in person sees it. Optional extra "
        "filters narrow it (same format as run_query filters, on the tile's dataset).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dashboard_id": {"type": "integer"},
                "tile_id": {"type": "string"},
                "filters": {"type": "array", "items": FILTER_SCHEMA},
            },
            "required": ["dashboard_id", "tile_id"],
        },
    },
    {
        "name": "get_kpis",
        "title": "KPI status",
        "description": "Each KPI goal on a dashboard: actual, target and status (on track, at risk, "
        "off track).",
        "inputSchema": {
            "type": "object",
            "properties": {"dashboard_id": {"type": "integer"}},
            "required": ["dashboard_id"],
        },
    },
]
for _t in TOOLS:
    _t["annotations"] = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}


# ------------------------------------------------------------------ tools
def _int(args: dict, key: str) -> int:
    try:
        return int(args[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError(f"{key} must be a number") from exc


def _dataset(db: Session, user: User, dataset_id: int) -> Dataset:
    ds = db.get(Dataset, dataset_id)
    if ds is None or not rls.can_query(db, user, ds):
        raise ToolError("Dataset not found")
    return ds


def _dashboard(db: Session, user: User, dashboard_id: int) -> Dashboard:
    d = db.get(Dashboard, dashboard_id)
    if d is None or not svc.share_level(db, user, d):
        raise ToolError("Dashboard not found")
    return d


def _table(result: dict, limit: int) -> dict:
    cols = [c.get("label") or c["key"] for c in result["columns"]]
    rows = result["rows"][:limit]
    return {
        "columns": cols,
        "rows": rows,
        "row_count": len(rows),
        "truncated": result.get("truncated", False) or len(result["rows"]) > limit,
        "totals": result.get("totals"),
        "restricted_by_row_security": result.get("restricted", False),
    }


def tool_search(db: Session, user: User, args: dict) -> dict:
    q = str(args.get("query") or "").strip().lower()
    kind = args.get("kind") or "all"

    def hit(name: str, desc: str) -> bool:
        return not q or q in (name or "").lower() or q in (desc or "").lower()

    out: dict[str, list] = {}
    if kind in ("all", "dashboards"):
        found = []
        for d in db.scalars(select(Dashboard).order_by(Dashboard.updated_at.desc())):
            if hit(d.name, d.description) and svc.share_level(db, user, d):
                found.append(
                    {
                        "id": d.id,
                        "name": d.name,
                        "description": d.description,
                        "url": _link("dashboards", d.id),
                    }
                )
            if len(found) >= 50:
                break
        out["dashboards"] = found
    if kind in ("all", "charts"):
        stmt = select(Chart).order_by(Chart.updated_at.desc())
        if not user.has_role(Role.admin):
            mine = list(workspaces.roles_of(db, user))
            stmt = stmt.where((Chart.owner_id == user.id) | (Chart.workspace_id.in_(mine)))
        found = []
        for c in db.scalars(stmt):
            if hit(c.name, c.description):
                found.append(
                    {"id": c.id, "name": c.name, "dataset_id": c.dataset_id, "url": _link("charts", c.id)}
                )
            if len(found) >= 50:
                break
        out["charts"] = found
    if kind in ("all", "datasets"):
        found = []
        for ds in db.scalars(select(Dataset).order_by(Dataset.name)):
            if hit(ds.name, ds.description) and rls.can_query(db, user, ds):
                found.append(
                    {"id": ds.id, "name": ds.name, "description": ds.description, "rows": ds.row_count,
                     "last_synced_at": ds.last_synced_at.isoformat() if ds.last_synced_at else None}
                )  # fmt: skip
            if len(found) >= 50:
                break
        out["datasets"] = found
    return out


def tool_describe_dataset(db: Session, user: User, args: dict) -> dict:
    ds = _dataset(db, user, _int(args, "dataset_id"))
    fields = context.usable_fields(db, ds)
    return {
        "id": ds.id,
        "name": ds.name,
        "fields": [
            {
                "name": f.name,
                "label": f.label or f.name,
                "role": f.role,
                "type": f.data_type,
                "default_agg": f.default_agg if f.role == "measure" else None,
                "description": f.description,
            }
            for f in fields
        ],  # fmt: skip
        "overview": context.describe(db, ds, rls.principal_for(db, user)),
    }


def _sort(spec: dict, raw: Any) -> list[dict]:
    """Sort by field name -> sort by the key of the matching dimension or measure."""
    out = []
    for s in raw or []:
        if not isinstance(s, dict):
            continue
        name = str(s.get("field") or s.get("key") or "")
        for item in spec.get("dimensions", []) + spec.get("measures", []):
            if item["field"] == name or item.get("key") == name:
                out.append({"key": item["key"], "dir": "asc" if s.get("dir") == "asc" else "desc"})
                break
    return out


def tool_run_query(db: Session, user: User, args: dict) -> dict:
    ds = _dataset(db, user, _int(args, "dataset_id"))
    mode = args.get("mode") or "aggregate"
    if mode == "rows" and not user.has_role(Role.explorer):
        raise ToolError("Raw rows need the Explorer role. Ask for an aggregated query instead.")
    limit = max(1, min(int(args.get("limit") or DEFAULT_ROWS), MAX_ROWS))
    spec: dict[str, Any] = {
        "dataset": ds.id,
        "mode": mode,
        "dimensions": [
            {**d, "key": f"d{i}"} for i, d in enumerate(args.get("dimensions") or []) if isinstance(d, dict)
        ],
        "measures": [
            {**m, "key": f"m{i}"} for i, m in enumerate(args.get("measures") or []) if isinstance(m, dict)
        ],
        "fields": [str(f) for f in args.get("fields") or []],
        "filters": args.get("filters") or [],
        "limit": limit,
    }
    spec["sort"] = _sort(spec, args.get("sort"))
    try:
        q = QuerySpec(**spec)
    except ValidationError as exc:
        e = exc.errors()[0]
        raise ToolError(f"The query is not valid: {'.'.join(str(x) for x in e['loc'])}: {e['msg']}") from exc
    try:
        result = engine.run(db, q, rls.principal_for(db, user))
    except (engine.QueryError, duck.QueryTimeout) as exc:
        raise ToolError(getattr(exc, "message", str(exc))) from exc
    return {"dataset": ds.name, **_table(result, limit)}


def tool_get_dashboard(db: Session, user: User, args: dict) -> dict:
    d = _dashboard(db, user, _int(args, "dashboard_id"))
    tabs = []
    for tab in (d.config or {}).get("tabs", []):
        tiles = []
        for item in tab.get("items", []):
            tile: dict[str, Any] = {"id": item["id"], "kind": item["kind"], "title": item.get("title", "")}
            if item["kind"] == "chart":
                c = db.get(Chart, item.get("chart_id"))
                if c is not None:
                    tile.update(chart=c.name, dataset_id=c.dataset_id, title=item.get("title") or c.name)
            elif item["kind"] == "text":
                tile["text"] = str(item.get("text") or "")[:2000]
            elif item["kind"] == "section":
                tile.update(code=item.get("code", ""), text=item.get("text", ""), goal=item.get("goal"))
            tiles.append(tile)
        tabs.append({"name": tab.get("name", ""), "tiles": tiles})
    return {
        "id": d.id,
        "name": d.name,
        "description": d.description,
        "url": _link("dashboards", d.id),
        "tabs": tabs,
        "filters": [
            {
                "label": f.get("label"),
                "kind": f.get("kind"),
                "fields": [t["field"] for t in f.get("targets", [])],
            }
            for f in (d.config or {}).get("filters", [])
        ],
    }


def tool_query_dashboard_tile(db: Session, user: User, args: dict) -> dict:
    d = _dashboard(db, user, _int(args, "dashboard_id"))
    try:
        chart, spec = svc.chart_spec(db, d, str(args.get("tile_id") or ""), filters=args.get("filters") or [])
        result = engine.run(db, spec.model_copy(update={"limit": min(spec.limit or MAX_ROWS, MAX_ROWS)}),
                            rls.principal_for(db, user))  # fmt: skip
    except svc.DashboardError as exc:
        raise ToolError(exc.message) from exc
    except (engine.QueryError, duck.QueryTimeout) as exc:
        raise ToolError(getattr(exc, "message", str(exc))) from exc
    return {"chart": chart.name, **_table(result, MAX_ROWS)}


def tool_get_kpis(db: Session, user: User, args: dict) -> dict:
    d = _dashboard(db, user, _int(args, "dashboard_id"))
    items = kpi.evaluate(db, d, rls.principal_for(db, user))
    return {
        "dashboard": d.name,
        "kpis": [
            {k: i.get(k) for k in ("code", "title", "actual", "target", "direction", "status", "error")}
            for i in items
        ],
    }


HANDLERS = {
    "search": tool_search,
    "describe_dataset": tool_describe_dataset,
    "run_query": tool_run_query,
    "get_dashboard": tool_get_dashboard,
    "query_dashboard_tile": tool_query_dashboard_tile,
    "get_kpis": tool_get_kpis,
}


def _audit_details(name: str, args: dict, client_id: str, ok: bool) -> dict:
    keep = {k: args[k] for k in ("dataset_id", "dashboard_id", "tile_id", "mode", "kind") if k in args}
    return {"tool": name, "client": client_id, "ok": ok, **keep}


# ------------------------------------------------------------------ JSON-RPC
def _error(id_: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def handle(db: Session, user: User, client_id: str, msg: Any, request=None) -> dict | None:
    """One JSON-RPC message -> the response (None for notifications)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid request")
    method, id_, params = msg["method"], msg.get("id"), msg.get("params") or {}
    if "id" not in msg:
        return None  # notifications (initialized, cancelled, ...) need no answer
    if method == "initialize":
        asked = params.get("protocolVersion")
        return {
            "jsonrpc": "2.0",
            "id": id_,
            "result": {
                "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "lumen-bi", "title": "Lumen BI", "version": VERSION},
                "instructions": INSTRUCTIONS,
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": id_, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": id_, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if handler is None:
            return _error(id_, -32602, f"Unknown tool: {name}")
        if not isinstance(args, dict):
            return _error(id_, -32602, "arguments must be an object")
        try:
            data = handler(db, user, args)
            ok = True
        except ToolError as exc:
            data, ok = None, False
            text = str(exc)
        audit.record(
            db, "mcp.tool", user=user, request=request, object_type="mcp", object_id=name,
            details=_audit_details(name, args, client_id, ok),
        )  # fmt: skip
        if not ok:
            return {
                "jsonrpc": "2.0",
                "id": id_,
                "result": {"content": [{"type": "text", "text": text}], "isError": True},
            }
        return {
            "jsonrpc": "2.0",
            "id": id_,
            "result": {
                "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}],
                "structuredContent": data,
                "isError": False,
            },
        }
    if method in ("resources/list", "prompts/list"):
        return {"jsonrpc": "2.0", "id": id_, "result": {method.split("/")[0]: []}}
    return _error(id_, -32601, f"Method not found: {method}")
