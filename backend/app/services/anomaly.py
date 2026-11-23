"""Anomaly alerts: is the latest value of a chart's time series unusual? (Statistics, no AI.)

The chart's first measure is read by its date (at the chart's level, e.g. day or month) with the
person's row-level security. The latest finished period is compared with up to 30 periods before it, using
the median and the median absolute deviation (robust to earlier spikes):

    score = 0.6745 * (value - median) / MAD

It is an anomaly when |score| is over the threshold for the chosen sensitivity (and in the chosen
direction). At least 8 earlier periods are needed.
"""

import statistics
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import Dashboard
from app.query import engine
from app.query.forecast import _parse, step
from app.query.rls import Principal
from app.query.spec import QuerySpec
from app.services import dashboards as svc

THRESHOLDS = {"high": 2.5, "medium": 3.5, "low": 5.0}  # sensitivity -> |score| needed
HISTORY = 30
MIN_HISTORY = 8


def score(history: list[float], value: float) -> float | None:
    if len(history) < MIN_HISTORY:
        return None
    med = statistics.median(history)
    mad = statistics.median(abs(x - med) for x in history)
    if mad == 0:
        # A flat history: fall back to the mean absolute deviation; still flat = any change is unusual
        mad = sum(abs(x - med) for x in history) / len(history) * 1.2533
        if mad == 0:
            return 0.0 if value == med else (10.0 if value > med else -10.0)
    return 0.6745 * (value - med) / mad


def series(
    db: Session, dash: Dashboard, tile_id: str, principal: Principal
) -> tuple[list[tuple[str, float]], dict]:
    """(date, value) for the tile's first measure, oldest first, and the measure's column."""
    _, spec = svc.chart_spec(db, dash, tile_id)
    date_dims = [d for d in spec.dimensions if d.grain]
    if not date_dims or not spec.measures:
        raise svc.DashboardError(
            "Anomaly alerts need a chart with a date (by day, week or month) and a measure"
        )
    d = date_dims[0].model_copy(update={"key": "d"})
    m = spec.measures[0].model_copy(update={"key": "m", "calc": None})
    q = QuerySpec(
        dataset=spec.dataset, dimensions=[d], measures=[m], filters=spec.filters, params=spec.params,
        sort=[{"key": "d", "dir": "asc"}], limit=5000,
    )  # fmt: skip
    out = engine.run(db, q, principal)
    points = [(r[0], float(r[1])) for r in out["rows"] if r[0] is not None and isinstance(r[1], int | float)]
    return points, {"grain": d.grain, "column": out["columns"][1]}


def check(
    db: Session, dash: Dashboard, tile_id: str, principal: Principal, cond: dict, now: datetime | None = None
) -> tuple[bool, str, dict[str, Any]]:
    """(is the latest finished period an anomaly, what was seen, details)."""
    points, meta = series(db, dash, tile_id, principal)
    now = now or datetime.now(UTC)
    if points:
        last = _parse(points[-1][0])
        if last is not None and step(last, meta["grain"]).replace(tzinfo=None) > now.replace(tzinfo=None):
            points = points[:-1]  # the current period is not over yet
    if len(points) < MIN_HISTORY + 1:
        return False, "not enough history", {}
    when, value = points[-1]
    history = [v for _, v in points[-HISTORY - 1 : -1]]
    s = score(history, value)
    if s is None:
        return False, "not enough history", {}
    limit = THRESHOLDS.get(cond.get("sensitivity", "medium"), 3.5)
    direction = cond.get("direction", "both")
    hit = (direction in ("both", "up") and s >= limit) or (direction in ("both", "down") and s <= -limit)
    med = statistics.median(history)
    seen = f"{_fmt(value)} on {str(when)[:10]} (usual about {_fmt(med)})"
    return hit, seen, {"date": when, "value": value, "median": med, "score": round(s, 2)}


def _fmt(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.2f}".rstrip("0").rstrip(".")
