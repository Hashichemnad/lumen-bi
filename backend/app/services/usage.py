"""Usage analytics: what is opened, by whom, and how the platform is used (Admin → Usage).

Events: dashboard_view and chart_view (usage_events). Sign-ins, exports and MCP calls come from the audit log
and AI requests from ai_requests. Events older than a year are removed by the daily housekeeping job.
"""

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AiRequest, AuditLog, Dashboard, Dataset, SyncRun, UsageEvent, User
from app.models.common import utcnow

KEEP_DAYS = 365


def track(
    db: Session, user: User | None, kind: str, object_id: int | None = None, source: str = "app"
) -> None:
    db.add(UsageEvent(user_id=user.id if user else None, kind=kind, object_id=object_id, source=source))
    db.commit()


def prune(db: Session) -> int:
    n = db.execute(delete(UsageEvent).where(UsageEvent.at < utcnow() - timedelta(days=KEEP_DAYS))).rowcount
    db.commit()
    return n or 0


def report(db: Session, days: int) -> dict:
    days = max(1, min(days, KEEP_DAYS))
    tz = ZoneInfo(get_settings().timezone)
    since = utcnow() - timedelta(days=days)
    events = db.execute(
        select(
            UsageEvent.at, UsageEvent.user_id, UsageEvent.kind, UsageEvent.object_id, UsageEvent.source
        ).where(UsageEvent.at >= since)
    ).all()
    all_events = events
    # Reports rendered for subscriptions are not people opening dashboards
    events = [e for e in events if e[4] != "render"]
    logins = db.execute(
        select(AuditLog.created_at, AuditLog.user_id).where(
            AuditLog.created_at >= since, AuditLog.action == "auth.login"
        )
    ).all()
    acts = dict(
        db.execute(
            select(AuditLog.action, func.count())
            .where(
                AuditLog.created_at >= since,
                AuditLog.action.in_(["data.export", "dashboard.export", "mcp.tool"]),
            )
            .group_by(AuditLog.action)
        ).all()
    )
    ai = db.scalar(select(func.count()).select_from(AiRequest).where(AiRequest.created_at >= since)) or 0

    today = datetime.now(tz).date()
    series_days = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    active: dict = defaultdict(set)
    views: Counter = Counter()
    for at, uid, kind, _, _ in events:
        d = at.astimezone(tz).date()
        if uid:
            active[d].add(uid)
        if kind == "dashboard_view":
            views[d] += 1
    for at, uid in logins:
        if uid:
            active[at.astimezone(tz).date()].add(uid)

    kinds = Counter(e[2] for e in events)
    users_active = {e[1] for e in events if e[1]} | {u for _, u in logins if u}
    dash_views: Counter = Counter()
    dash_viewers: dict = defaultdict(set)
    last_view: dict = {}
    per_user: Counter = Counter()
    for at, uid, kind, oid, _ in events:
        if uid:
            per_user[uid] += 1
        if kind == "dashboard_view" and oid:
            dash_views[oid] += 1
            if uid:
                dash_viewers[oid].add(uid)
            last_view[oid] = max(last_view.get(oid, at), at)
    sources = Counter(e[4] for e in all_events if e[2] == "dashboard_view")

    dashboards = {d.id: d for d in db.scalars(select(Dashboard))}
    people = {u.id: u for u in db.scalars(select(User))}
    top = [
        {"id": i, "name": dashboards[i].name, "views": n, "viewers": len(dash_viewers[i]),
         "last_viewed_at": last_view.get(i)}
        for i, n in dash_views.most_common(10) if i in dashboards
    ]  # fmt: skip
    unused = sorted(
        (d for d in dashboards.values() if d.id not in dash_views and d.created_at < since),
        key=lambda d: d.updated_at,
    )[:10]
    top_users = [
        {"id": uid, "name": people[uid].name or people[uid].email, "email": people[uid].email,
         "role": people[uid].role, "events": n}
        for uid, n in per_user.most_common(10) if uid in people
    ]  # fmt: skip
    roles = Counter(u.role for u in people.values() if u.is_active)
    never = [u for u in people.values() if u.is_active and u.last_login_at is None]
    return {
        "days": days,
        "totals": {
            "active_users": len(users_active),
            "people": sum(roles.values()),
            "sign_ins": len(logins),
            "dashboard_views": kinds["dashboard_view"],
            "chart_views": kinds["chart_view"],
            "exports": acts.get("data.export", 0) + acts.get("dashboard.export", 0),
            "ai": ai,
            "mcp": acts.get("mcp.tool", 0),
        },
        "daily": [
            {
                "date": d.isoformat(),
                "active_users": len(active.get(d, ())),
                "dashboard_views": views.get(d, 0),
            }
            for d in series_days
        ],
        "top_dashboards": top,
        "unused_dashboards": [
            {
                "id": d.id,
                "name": d.name,
                "updated_at": d.updated_at,
                "owner": (people.get(d.owner_id).email if d.owner_id in people else ""),
            }
            for d in unused
        ],
        "top_users": top_users,
        "roles": dict(roles),
        "never_signed_in": len(never),
        "view_sources": dict(sources),
    }


# ------------------------------------------------------------------ sync monitor
def sync_monitor(db: Session) -> dict:
    now = datetime.now(UTC)
    week = now - timedelta(days=7)
    runs = db.scalars(select(SyncRun).where(SyncRun.created_at >= week).order_by(SyncRun.created_at)).all()
    by_ds: dict[int, list[SyncRun]] = defaultdict(list)
    for r in runs:
        by_ds[r.dataset_id].append(r)
    out = []
    for ds in db.scalars(select(Dataset).order_by(Dataset.name)):
        mine = by_ds.get(ds.id, [])
        done = [r for r in mine if r.finished_at and r.started_at]
        durations = [(r.finished_at - r.started_at).total_seconds() for r in done]
        last = mine[-1] if mine else None
        late = bool(ds.schedule and ds.next_run_at and ds.next_run_at < now - timedelta(hours=1))
        out.append(
            {
                "id": ds.id,
                "name": ds.name,
                "kind": ds.kind,
                "connection": ds.connection.name if ds.connection else "",
                "connection_type": ds.connection.type if ds.connection else ds.kind,
                "schedule": ds.schedule,
                "next_run_at": ds.next_run_at,
                "last_synced_at": ds.last_synced_at,
                "last_status": ds.last_status,
                "row_count": ds.row_count,
                "running": any(r.status in ("queued", "running") for r in mine),
                "runs_7d": len(mine),
                "failed_7d": sum(r.status == "failed" for r in mine),
                "avg_seconds": round(sum(durations) / len(durations), 1) if durations else None,
                "last_seconds": round(durations[-1], 1) if durations else None,
                "last_error": (last.error or "")[:500] if last and last.status == "failed" else "",
                "late": late,
            }
        )
    failures = [
        {"dataset_id": r.dataset_id, "at": r.finished_at or r.created_at, "error": (r.error or "")[:500],
         "trigger": r.trigger}
        for r in reversed(runs) if r.status == "failed"
    ][:20]  # fmt: skip

    def state(d: dict) -> str:
        if d["last_status"] == "failed":
            return "failed"
        if d["late"]:
            return "late"
        return "never" if d["last_status"] == "never" else "ok"

    health = Counter(state(d) for d in out)
    since_day = now - timedelta(days=1)
    rows_today = db.scalar(
        select(func.coalesce(func.sum(SyncRun.rows_read), 0)).where(
            SyncRun.finished_at >= since_day, SyncRun.status == "success"
        )
    )
    return {
        "datasets": out,
        "failures": failures,
        "health": dict(health),
        "rows_last_24h": int(rows_today or 0),
    }
