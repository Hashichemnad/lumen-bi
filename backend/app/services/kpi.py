"""KPI goals: a section tile can carry a goal (target, which way is better, a tolerance and the chart tile
whose value is the actual). Status is worked out on the server for each viewer, with their row-level
security, so section headers, the navigator, scorecards and alerts all agree.

status: on_track (target met) | at_risk (missed by no more than the tolerance) | off_track | no_data
"""

from typing import Any

from sqlalchemy.orm import Session

from app.models import Dashboard
from app.query import engine
from app.query.rls import Principal
from app.services import dashboards as svc

STATUS_ORDER = {"on_track": 0, "no_data": 0, "at_risk": 1, "off_track": 2}


def status_of(actual: float | None, goal: dict) -> str:
    if actual is None:
        return "no_data"
    target = float(goal["target"])
    lower = goal.get("direction") == "lower"
    tolerance = abs(target) * float(goal.get("warn", 5)) / 100
    if (actual <= target) if lower else (actual >= target):
        return "on_track"
    gap = (actual - target) if lower else (target - actual)
    return "at_risk" if gap <= tolerance else "off_track"


def tile_value(
    db: Session, dash: Dashboard, tile_id: str, principal: Principal, filters: list[dict] | None = None
) -> tuple[float | None, dict | None]:
    """The first measure of a chart tile: its grand total when the chart has dimensions."""
    _, spec = svc.chart_spec(db, dash, tile_id, filters=filters or [])
    if not spec.measures:
        raise svc.DashboardError("This chart has no measure to compare with a target")
    spec = spec.model_copy(
        update={"totals": bool(spec.dimensions), "limit": 1 if not spec.dimensions else 5000}
    )
    out = engine.run(db, spec, principal)
    idx = next((i for i, c in enumerate(out["columns"]) if c["role"] == "measure"), None)
    if idx is None:
        return None, None
    col = out["columns"][idx]
    raw: Any = None
    if spec.dimensions:
        raw = (out.get("totals") or [None] * (idx + 1))[idx]
    elif out["rows"]:
        raw = out["rows"][0][idx]
    try:
        return (float(raw) if raw is not None else None), col
    except (TypeError, ValueError):
        return None, col


def sections(dash: Dashboard) -> list[dict]:
    return [i for i in svc.items(dash.config or {}) if i.get("kind") == "section"]


def evaluate(
    db: Session, dash: Dashboard, principal: Principal, filters: list[dict] | None = None
) -> list[dict]:
    """Every section with a goal: actual, target and status for this person."""
    out = []
    for sec in sections(dash):
        goal = sec.get("goal")
        if not goal:
            continue
        item: dict[str, Any] = {
            "section": sec["id"],
            "code": sec.get("code", ""),
            "title": sec.get("title", ""),
            "target": goal["target"],
            "direction": goal.get("direction", "higher"),
            "warn": goal.get("warn", 5),
            "tile": goal.get("tile"),
        }
        try:
            actual, col = tile_value(db, dash, goal["tile"], principal, (filters or {}).get(goal["tile"]))
            item.update(
                actual=actual, format=(col or {}).get("format"), status=status_of(actual, goal), error=None
            )
        except (svc.DashboardError, engine.QueryError) as exc:
            message = exc.message if hasattr(exc, "message") else str(exc)
            item.update(actual=None, format=None, status="no_data", error=message)
        out.append(item)
    return out
