"""Lineage and impact: where a dataset's data comes from, and everything that would be affected by changing
or removing it (or one of its fields).

Upstream: the connection, and the datasets a join or union is built from (all the way up).
Downstream: datasets built from it, charts on it, dashboards showing those charts, subscriptions and alerts
on those dashboards, data checks, and datasets that use it as an access table for row-level security.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Alert, Chart, Dashboard, DataCheck, Dataset, Subscription
from app.query import rls
from app.semantic import derived, model
from app.services import dashboards as svc


def _ref(ds: Dataset) -> dict:
    return {"id": ds.id, "name": ds.name, "kind": ds.kind, "last_status": ds.last_status}


def upstream(db: Session, ds: Dataset, seen: set[int] | None = None) -> list[dict]:
    seen = seen if seen is not None else {ds.id}
    out = []
    for pid in derived.parents(ds.source or {}):
        parent = db.get(Dataset, pid)
        if parent is None or parent.id in seen:
            continue
        seen.add(parent.id)
        out.append({**_ref(parent), "connection": _conn(parent), "parents": upstream(db, parent, seen)})
    return out


def _conn(ds: Dataset) -> dict | None:
    c = ds.connection
    return {"id": c.id, "name": c.name, "type": c.type} if c else None


def _downstream_datasets(db: Session, ds: Dataset, seen: set[int]) -> list[Dataset]:
    out = []
    for child in derived.dependents(db, ds.id):
        if child.id in seen:
            continue
        seen.add(child.id)
        out.append(child)
        out += _downstream_datasets(db, child, seen)
    return out


def _uses_field(query: dict, name: str) -> bool:
    for key in ("dimensions", "measures", "filters", "fields", "sort"):
        for item in query.get(key) or []:
            if isinstance(item, str) and item == name:
                return True
            if isinstance(item, dict) and (
                item.get("field") == name or (item.get("by") or {}).get("field") == name
            ):
                return True
    return False


def impact(db: Session, ds: Dataset, field: str | None = None) -> dict[str, Any]:
    """Everything downstream of the dataset (or of one field of it)."""
    datasets = _downstream_datasets(db, ds, {ds.id})
    ids = [ds.id] + [d.id for d in datasets]
    charts = db.scalars(select(Chart).where(Chart.dataset_id.in_(ids)).order_by(Chart.name)).all()
    if field is not None:
        # Only charts on this dataset that use the field (or a field calculated from it)
        names = {field, *model.dependents(model.fields_of(db, ds.id), field)}
        charts = [
            c for c in charts if c.dataset_id == ds.id and any(_uses_field(c.query or {}, n) for n in names)
        ]
    chart_ids = {c.id for c in charts}
    dashboards = [
        d
        for d in db.scalars(select(Dashboard).order_by(Dashboard.name))
        if svc.chart_ids(d.config or {}) & chart_ids
    ]
    if field is not None:
        # Dashboard filters on the field count too
        for d in db.scalars(select(Dashboard)):
            for f in (d.config or {}).get("filters", []):
                targets = f.get("targets", [])
                hit = any(t.get("dataset") == ds.id and t.get("field") == field for t in targets)
                if hit and d not in dashboards:
                    dashboards.append(d)
    dash_ids = [d.id for d in dashboards]
    subs = (
        db.scalars(select(Subscription).where(Subscription.dashboard_id.in_(dash_ids))).all()
        if dash_ids
        else []
    )
    alerts = db.scalars(select(Alert).where(Alert.dashboard_id.in_(dash_ids))).all() if dash_ids else []
    checks = db.scalars(select(DataCheck).where(DataCheck.dataset_id == ds.id)).all()
    if field is not None:
        checks = [c for c in checks if (c.config or {}).get("column") == field]
    access_for = [] if field is not None else rls.datasets_using_as_access_table(db, ds.id)
    calculated = model.dependents(model.fields_of(db, ds.id), field) if field else []
    by_chart: dict[int, list[str]] = {}
    for d in dashboards:
        for cid in svc.chart_ids(d.config or {}) & chart_ids:
            by_chart.setdefault(cid, []).append(d.name)
    return {
        "datasets": [_ref(d) for d in datasets],
        "charts": [
            {"id": c.id, "name": c.name, "dataset_id": c.dataset_id, "dashboards": by_chart.get(c.id, [])}
            for c in charts
        ],
        "dashboards": [{"id": d.id, "name": d.name} for d in dashboards],
        "subscriptions": [{"id": s.id, "name": s.name, "dashboard_id": s.dashboard_id} for s in subs],
        "alerts": [{"id": a.id, "name": a.name, "dashboard_id": a.dashboard_id} for a in alerts],
        "checks": [{"id": c.id, "kind": c.kind} for c in checks],
        "access_table_for": [_ref(d) for d in access_for],
        "calculated_fields": calculated,
    }


def lineage(db: Session, ds: Dataset) -> dict:
    return {
        "dataset": {
            **_ref(ds),
            "connection": _conn(ds),
            "row_count": ds.row_count,
            "last_synced_at": ds.last_synced_at,
        },
        "upstream": upstream(db, ds),
        "downstream": impact(db, ds),
    }
