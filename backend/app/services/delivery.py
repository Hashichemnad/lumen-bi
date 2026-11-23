"""Running subscriptions and alerts.

Subscriptions: at their time, each recipient who can open the dashboard gets it (PDF, PNG or a link) made
with their own row-level security. Teams channels get the link only (channel members may not be users).

Alerts: each recipient is checked with their own data and told once when the condition becomes true, again
only after it was false in between. A Teams channel is checked as the alert's owner.
"""

import html
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import SessionLocal
from app.models import Alert, Chart, Dashboard, Subscription, User
from app.query import engine, rls
from app.services import checks, kpi, notify, render
from app.services import dashboards as svc
from app.services.sync import next_run

log = logging.getLogger("lumen.delivery")
OPS = {
    "gt": (">", lambda a, b: a > b),
    "gte": ("≥", lambda a, b: a >= b),
    "lt": ("<", lambda a, b: a < b),
    "lte": ("≤", lambda a, b: a <= b),
}
STATUS_TEXT = {"on_track": "on track", "at_risk": "at risk", "off_track": "off track", "no_data": "no data"}


def dashboard_url(dash: Dashboard, state: str | None = None) -> str:
    base = f"{get_settings().public_url.rstrip('/')}/dashboards/{dash.id}"
    return f"{base}?s={state}" if state else base


def _fmt(v: float | None) -> str:
    if v is None:
        return "no value"
    return f"{v:,.2f}".rstrip("0").rstrip(".") if abs(v) < 1000 else f"{v:,.0f}"


def _kpi_lines(db: Session, dash: Dashboard, user: User) -> list[str]:
    try:
        items = kpi.evaluate(db, dash, rls.principal_for(db, user))
    except Exception:  # noqa: BLE001
        return []
    return [
        f"{(i['code'] + ' ') if i['code'] else ''}{i['title']}: {_fmt(i['actual'])} "
        f"(target {_fmt(i['target'])}, {STATUS_TEXT[i['status']]})"
        for i in items
    ]


# ------------------------------------------------------------------ subscriptions
def run_subscription(db: Session, sub: Subscription, now: datetime | None = None) -> dict:
    dash = db.get(Dashboard, sub.dashboard_id)
    now = now or datetime.now(UTC)
    sub.last_run_at = now
    if dash is None:
        sub.last_status, sub.last_error = "failed", "The dashboard was deleted"
        db.commit()
        return {"sent": 0, "failed": 0}
    people = [u for u in notify.recipients(db, sub.recipients or {}) if svc.share_level(db, u, dash)]
    link = dashboard_url(dash, sub.state)
    errors: list[str] = []
    files: dict[int, bytes] = {}
    if sub.format in {"pdf", "png"} and any(c in {"email"} for c in sub.channels):
        for u in people:
            try:
                files[u.id] = render.render_dashboard(u.id, dash.id, sub.format, sub.state)
            except Exception as exc:  # noqa: BLE001 - send the link instead
                log.exception("Rendering %s for %s failed", dash.id, u.email)
                errors.append(f"{u.email}: {exc}")

    def message(u: User | None) -> notify.Message:
        title = f"{sub.name or dash.name}"
        intro = sub.message.strip() or f'Here is "{dash.name}".'
        if u is None:
            return notify.Message(title, intro, link)
        lines = _kpi_lines(db, dash, u)
        text = intro + ("\n\n" + "\n".join(lines) if lines else "")
        body = "".join(f"<li>{html.escape(x)}</li>" for x in lines)
        page = (
            f"<p>{html.escape(intro)}</p>"
            + (f"<ul>{body}</ul>" if body else "")
            + f'<p><a href="{html.escape(link)}">Open the dashboard in Lumen</a></p>'
        )
        attachments = []
        if u.id in files:
            mime = "application/pdf" if sub.format == "pdf" else "image/png"
            attachments.append((f"{dash.name}.{sub.format}", files[u.id], mime))
        return notify.Message(title, text, link, page, attachments)

    result = notify.deliver(db, list(sub.channels or []), people, "subscription", sub.id, message)
    sub.last_status = "failed" if result["failed"] and not result["sent"] else "sent"
    sub.last_error = (
        "; ".join(errors)[:2000] if errors else ("" if not result["failed"] else "Some messages failed")
    )
    db.commit()
    return result


# ------------------------------------------------------------------ alerts
def alert_check(db: Session, alert: Alert, dash: Dashboard, user: User) -> tuple[bool, str]:
    """(is the condition true for this person, what was seen)."""
    principal = rls.principal_for(db, user)
    cond = alert.condition or {}
    if cond.get("kind") == "kpi":
        item = next((i for i in kpi.evaluate(db, dash, principal) if i["section"] == alert.tile_id), None)
        if item is None:
            raise svc.DashboardError("This KPI section has no goal any more")
        worse = kpi.STATUS_ORDER[item["status"]] >= kpi.STATUS_ORDER[cond.get("status", "off_track")]
        seen = f"{_fmt(item['actual'])} (target {_fmt(item['target'])}, {STATUS_TEXT[item['status']]})"
        return worse and item["status"] != "no_data", seen
    value, _ = kpi.tile_value(db, dash, alert.tile_id, principal)
    if value is None:
        return False, "no value"
    _, test = OPS[cond.get("op", "gt")]
    return test(value, float(cond.get("value", 0))), _fmt(value)


def describe_condition(alert: Alert) -> str:
    cond = alert.condition or {}
    if cond.get("kind") == "kpi":
        return (
            "the KPI is off track" if cond.get("status") == "off_track" else "the KPI is at risk or off track"
        )
    sign, _ = OPS[cond.get("op", "gt")]
    return f"the value is {sign} {_fmt(float(cond.get('value', 0)))}"


def run_alert(db: Session, alert: Alert, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    alert.last_checked_at = now
    dash = db.get(Dashboard, alert.dashboard_id)
    owner = db.get(User, alert.owner_id) if alert.owner_id else None
    if dash is None or owner is None or not owner.is_active:
        alert.last_error = "The dashboard or the owner no longer exists"
        db.commit()
        return {"sent": 0, "failed": 0}
    people = [u for u in notify.recipients(db, alert.recipients or {}) if svc.share_level(db, u, dash)]
    was = set(alert.triggered_for or [])
    now_true: set[int] = set()
    seen: dict[int, str] = {}
    errors = []
    checks_for = people + ([owner] if any(c.startswith("teams:") for c in alert.channels or []) else [])
    for u in {u.id: u for u in checks_for}.values():
        try:
            true, value = alert_check(db, alert, dash, u)
        except (svc.DashboardError, engine.QueryError) as exc:
            errors.append(getattr(exc, "message", str(exc)))
            continue
        seen[u.id] = value
        if true:
            now_true.add(u.id)
    if owner.id in seen:
        alert.last_value = seen[owner.id]
    alert.last_error = "; ".join(sorted(set(errors)))[:2000]
    # Teams is "person 0", checked as the owner
    if owner.id in now_true and any(c.startswith("teams:") for c in alert.channels or []):
        now_true.add(0)
    new = now_true - was
    alert.triggered_for = sorted(now_true)
    result = {"sent": 0, "failed": 0}
    if new:
        alert.last_triggered_at = now
        link = dashboard_url(dash)

        def message(u: User | None) -> notify.Message:
            value = seen.get(u.id if u else owner.id, "")
            text = f'{alert.name}: {describe_condition(alert)} on "{dash.name}". Now: {value}.'
            return notify.Message(f"Alert: {alert.name}", text, link)

        personal = [c for c in alert.channels or [] if not c.startswith("teams:")]
        shared = [c for c in alert.channels or [] if c.startswith("teams:")] if 0 in new else []
        r1 = notify.deliver(db, personal, [u for u in people if u.id in new], "alert", alert.id, message)
        r2 = notify.deliver(db, shared, [], "alert", alert.id, message)
        result = {"sent": r1["sent"] + r2["sent"], "failed": r1["failed"] + r2["failed"]}
    db.commit()
    return result


# ------------------------------------------------------------------ scheduler
def dispatch_due(now: datetime | None = None) -> dict:
    """Called every minute: due subscriptions and alerts."""
    now = now or datetime.now(UTC)
    done = {"subscriptions": 0, "alerts": 0}
    with SessionLocal() as db:
        for sub in db.scalars(
            select(Subscription).where(Subscription.enabled.is_(True), Subscription.next_run_at <= now)
        ).all():
            sub.next_run_at = next_run(sub.schedule, now)
            db.commit()
            try:
                run_subscription(db, sub, now)
            except Exception:  # noqa: BLE001
                log.exception("Subscription %s failed", sub.id)
                db.rollback()
            done["subscriptions"] += 1
        for alert in db.scalars(select(Alert).where(Alert.enabled.is_(True), Alert.next_run_at <= now)).all():
            alert.next_run_at = next_run(alert.schedule, now)
            db.commit()
            try:
                run_alert(db, alert, now)
            except Exception:  # noqa: BLE001
                log.exception("Alert %s failed", alert.id)
                db.rollback()
            done["alerts"] += 1
    return done


def after_sync(dataset_id: int) -> None:
    """A dataset has new data: run its checks, then the alerts on dashboards that use it."""
    from app.models import Dataset

    with SessionLocal() as db:
        ds = db.get(Dataset, dataset_id)
        if ds is None:
            return
        checks.run_checks(db, ds)
        chart_ids = set(db.scalars(select(Chart.id).where(Chart.dataset_id == dataset_id)))
        dash_ids = {d.id for d in db.scalars(select(Dashboard)) if svc.chart_ids(d.config or {}) & chart_ids}
        if not dash_ids:
            return
        for alert in db.scalars(
            select(Alert).where(Alert.enabled.is_(True), Alert.dashboard_id.in_(dash_ids))
        ).all():
            try:
                run_alert(db, alert)
            except Exception:  # noqa: BLE001
                log.exception("Alert %s failed after sync", alert.id)
                db.rollback()
