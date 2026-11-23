"""Dashboards: who may see or change them, checking their layout, and running their charts for a viewer.

A viewer runs a dashboard's charts *through the dashboard*: the saved chart query is used as it is, and a
viewer may only narrow it (filters, cross-filters), set parameter values, drill along a date or a hierarchy
of the same dataset, or view the rows behind it. Row-level security of the viewer always applies.
"""

import json
import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Chart, Dashboard, DashboardShare, Dataset, Hierarchy, Role, User
from app.query.spec import Filter, QuerySpec

MAX_CONFIG_BYTES = 500_000
ITEM_KINDS = {"chart", "text", "image", "web"}
FILTER_KINDS = {"values", "range", "relative", "text"}
URL_RE = re.compile(r"^https://[^\s<>\"']+$", re.I)
ID_RE = re.compile(r"^[\w-]{1,40}$")


class DashboardError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.message, self.status = message, status


# ------------------------------------------------------------------ permissions
def share_level(db: Session, user: User, dash: Dashboard) -> str | None:
    """'manage' (owner/admin), 'edit', 'view' or None."""
    if user.has_role(Role.admin) or dash.owner_id == user.id:
        return "manage"
    group_ids = {g.id for g in user.groups}
    best = None
    for s in db.scalars(select(DashboardShare).where(DashboardShare.dashboard_id == dash.id)):
        hit = (
            s.principal == "everyone"
            or (s.principal == "user" and s.principal_id == user.id)
            or (s.principal == "group" and s.principal_id in group_ids)
        )
        if hit and (best is None or s.level == "edit"):
            best = s.level
    # Viewers can never edit, whatever they were given
    if best == "edit" and not user.has_role(Role.explorer):
        best = "view"
    return best


def can_edit(level: str | None) -> bool:
    return level in {"edit", "manage"}


# ------------------------------------------------------------------ config
def items(config: dict) -> list[dict]:
    return [item for tab in config.get("tabs", []) for item in tab.get("items", [])]


def chart_ids(config: dict) -> set[int]:
    return {int(i["chart_id"]) for i in items(config) if i.get("kind") == "chart" and i.get("chart_id")}


def _box(value: Any, where: str, cols: int) -> dict | None:
    if value is None:
        return None
    try:
        x, y, w, h = (int(value[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError) as exc:
        raise DashboardError(f"{where}: position needs x, y, w and h") from exc
    if not (0 <= x < cols and 1 <= w <= cols and x + w <= cols and 0 <= y <= 10_000 and 1 <= h <= 60):
        raise DashboardError(f"{where}: position is outside the grid")
    return {"x": x, "y": y, "w": w, "h": h}


def validate_config(db: Session, user: User, config: dict, previous: dict | None) -> dict:
    if len(json.dumps(config)) > MAX_CONFIG_BYTES:
        raise DashboardError("This dashboard is too large to save")
    tabs = config.get("tabs")
    if not isinstance(tabs, list) or not 1 <= len(tabs) <= 20:
        raise DashboardError("A dashboard needs between 1 and 20 tabs")
    seen: set[str] = set()
    out_tabs = []
    for tab in tabs:
        tid = str(tab.get("id", ""))
        if not ID_RE.match(tid) or tid in seen:
            raise DashboardError("Each tab needs a unique id")
        seen.add(tid)
        raw_items = tab.get("items") or []
        if len(raw_items) > 100:
            raise DashboardError("A tab can hold at most 100 tiles")
        out_items = []
        for item in raw_items:
            iid, kind = str(item.get("id", "")), item.get("kind")
            if not ID_RE.match(iid) or iid in seen:
                raise DashboardError("Each tile needs a unique id")
            seen.add(iid)
            if kind not in ITEM_KINDS:
                raise DashboardError("Unknown kind of tile")
            clean: dict[str, Any] = {
                "id": iid,
                "kind": kind,
                "title": str(item.get("title") or "")[:200],
                "desktop": _box(item.get("desktop"), "A tile", 12),
                "phone": _box(item.get("phone"), "A tile (phone)", 4),
            }
            if clean["desktop"] is None:
                raise DashboardError("Each tile needs a position")
            if kind == "chart":
                try:
                    clean["chart_id"] = int(item.get("chart_id"))
                except (TypeError, ValueError) as exc:
                    raise DashboardError("Choose a chart for each chart tile") from exc
                clean["show_title"] = bool(item.get("show_title", True))
            elif kind == "text":
                clean["text"] = str(item.get("text") or "")[:20_000]
            else:
                url = str(item.get("url") or "").strip()
                if url and not URL_RE.match(url):
                    raise DashboardError("Images and web pages must use an https:// address")
                clean["url"] = url[:2000]
                if kind == "image":
                    clean["alt"] = str(item.get("alt") or "")[:300]
                    clean["fit"] = "cover" if item.get("fit") == "cover" else "contain"
            out_items.append(clean)
        out_tabs.append({"id": tid, "name": str(tab.get("name") or "Tab")[:100], "items": out_items})

    filters = config.get("filters") or []
    if not isinstance(filters, list) or len(filters) > 30:
        raise DashboardError("A dashboard can have at most 30 filters")
    out_filters = []
    for f in filters:
        fid = str(f.get("id", ""))
        if not ID_RE.match(fid) or fid in seen:
            raise DashboardError("Each filter needs a unique id")
        seen.add(fid)
        if f.get("kind") not in FILTER_KINDS:
            raise DashboardError("Unknown kind of filter")
        targets = []
        for t in f.get("targets") or []:
            try:
                targets.append({"dataset": int(t["dataset"]), "field": str(t["field"])})
            except (KeyError, TypeError, ValueError) as exc:
                raise DashboardError("A filter applies to a dataset field") from exc
        if not targets:
            raise DashboardError(f'Choose which field the filter "{f.get("label", "")}" applies to')
        out_filters.append(
            {
                "id": fid,
                "label": str(f.get("label") or "")[:100],
                "kind": f["kind"],
                "control": str(f.get("control") or "")[:20],
                "targets": targets,
                "value": f.get("value"),
                "grain": f.get("grain") if f.get("grain") in {"year", "quarter", "month", "day"} else None,
            }
        )
    options = config.get("options") or {}
    clean_config = {
        "tabs": out_tabs,
        "filters": out_filters,
        "options": {
            "cross_filter": bool(options.get("cross_filter", True)),
            "view_data": bool(options.get("view_data", True)),
            "phone_layout": bool(options.get("phone_layout", False)),
        },
    }
    # Charts newly placed on the dashboard must be ones this person can open themselves
    before = chart_ids(previous or {})
    for cid in chart_ids(clean_config) - before:
        chart = db.get(Chart, cid)
        if chart is None:
            raise DashboardError("One of the charts no longer exists")
        if not (user.has_role(Role.admin) or chart.owner_id == user.id):
            raise DashboardError(f'You cannot add the chart "{chart.name}"', 403)
    for cid in chart_ids(clean_config) & before:
        if db.get(Chart, cid) is None:
            raise DashboardError("One of the charts no longer exists")
    return clean_config


def dashboards_using_chart(db: Session, chart_id: int) -> list[Dashboard]:
    return [d for d in db.scalars(select(Dashboard)) if chart_id in chart_ids(d.config or {})]


def datasets_of(db: Session, dash: Dashboard) -> list[Dataset]:
    ids = {c.dataset_id for cid in chart_ids(dash.config or {}) if (c := db.get(Chart, cid))}
    return [d for d in (db.get(Dataset, i) for i in sorted(ids)) if d is not None]


# ------------------------------------------------------------------ running a chart for a viewer
def _drill_allowed(db: Session, dataset_id: int, original: dict, step: dict) -> bool:
    if step.get("field") == original.get("field"):
        return True  # another date level or date part of the same field
    for h in db.scalars(select(Hierarchy).where(Hierarchy.dataset_id == dataset_id)):
        if original.get("field") in (h.levels or []) and step.get("field") in (h.levels or []):
            return True
    return False


def chart_spec(
    db: Session,
    dash: Dashboard,
    item_id: str,
    *,
    filters: list[dict] | None = None,
    params: dict | None = None,
    drill: list[dict] | None = None,
    rows: bool = False,
    limit: int | None = None,
) -> tuple[Chart, QuerySpec]:
    item = next((i for i in items(dash.config or {}) if i["id"] == item_id), None)
    if item is None or item.get("kind") != "chart":
        raise DashboardError("That tile is not on this dashboard", 404)
    chart = db.get(Chart, item["chart_id"])
    if chart is None:
        raise DashboardError("This chart was deleted", 404)
    spec = dict(chart.query or {})
    try:
        extra = [Filter(**f).model_dump(exclude_none=True) for f in filters or []]
    except ValidationError as exc:
        raise DashboardError(f"A filter is not valid: {exc.errors()[0]['msg']}") from exc
    if rows:
        out = {
            "dataset": chart.dataset_id,
            "mode": "rows",
            "filters": [f for f in spec.get("filters", []) if "agg" not in f] + extra,
            "params": {**spec.get("params", {}), **(params or {})},
            "limit": min(limit or 500, 1_000_000),
        }
    else:
        dims = [dict(d) for d in spec.get("dimensions", [])]
        for step in drill or []:
            target = next((d for d in dims if d.get("key") == step.get("key")), None)
            if target is None or not _drill_allowed(db, chart.dataset_id, target, step):
                raise DashboardError("That drill step is not allowed", 403)
            target["field"] = str(step["field"])
            for k in ("grain", "part"):
                if step.get(k):
                    target[k] = step[k]
                else:
                    target.pop(k, None)
        out = {
            **spec,
            "dataset": chart.dataset_id,
            "dimensions": dims,
            "filters": list(spec.get("filters", [])) + extra,
            "params": {**spec.get("params", {}), **(params or {})},
        }
        if limit:
            out["limit"] = limit
    try:
        return chart, QuerySpec(**out)
    except ValidationError as exc:
        raise DashboardError(f"The chart's query is not valid: {exc.errors()[0]['msg']}") from exc
