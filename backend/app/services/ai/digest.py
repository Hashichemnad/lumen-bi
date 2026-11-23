"""The daily digest: a short morning email about the KPIs on a person's favourite dashboards.

People turn it on in their account. From 7:00 (organisation time zone) each day, once a day: the KPI goals
and headline numbers of their favourite dashboards are read with their own row-level security, an AI
model (if one may see that data) writes a few plain sentences, and it is emailed with the numbers. Without
an allowed model the email still goes out, with just the numbers.
"""

import html
import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import SessionLocal
from app.models import Chart, Dashboard, DashboardFavorite, Dataset, NotificationLog, User
from app.query import engine, rls
from app.services import dashboards as svc
from app.services import kpi, notify
from app.services import settings as app_settings
from app.services.ai.providers import AiError, generate

log = logging.getLogger(__name__)
SEND_FROM_HOUR = 7
MAX_DASHBOARDS = 5
MAX_TILES = 4
STATUS = {"on_track": "on track", "at_risk": "at risk", "off_track": "off track", "no_data": "no data"}
STATUS_AR = {
    "on_track": "على المسار",
    "at_risk": "معرض للخطر",
    "off_track": "خارج المسار",
    "no_data": "لا بيانات",
}


def wants_digest(user: User) -> bool:
    return bool((user.preferences or {}).get("ai_digest"))


def _num(v: float | None) -> str:
    if v is None:
        return "–"
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.2f}".rstrip("0").rstrip(".")


def facts(db: Session, user: User) -> tuple[list[dict], list[Dataset]]:
    """[{dashboard, link, lines: [(label, value, status)]}] and the datasets involved."""
    from app.services.delivery import dashboard_url

    principal = rls.principal_for(db, user)
    favs = db.scalars(
        select(Dashboard)
        .join(DashboardFavorite, DashboardFavorite.dashboard_id == Dashboard.id)
        .where(DashboardFavorite.user_id == user.id)
        .order_by(DashboardFavorite.created_at)
    ).all()
    out: list[dict] = []
    dataset_ids: set[int] = set()
    for dash in favs:
        if not svc.share_level(db, user, dash):
            continue
        lines: list[tuple[str, str, str]] = []
        goal_tiles = set()
        for item in kpi.evaluate(db, dash, principal):
            goal_tiles.add(item.get("tile"))
            label = " ".join(x for x in (item.get("code"), item.get("title")) if x) or "KPI"
            value = f"{_num(item['actual'])} (target {_num(item['target'])})"
            lines.append((label, value, item["status"]))
        for tile in svc.items(dash.config or {}):
            if len(lines) >= MAX_TILES + len(goal_tiles) or tile.get("kind") != "chart":
                continue
            if tile["id"] in goal_tiles:
                continue
            try:
                value, _ = kpi.tile_value(db, dash, tile["id"], principal)
            except (svc.DashboardError, engine.QueryError):
                continue
            if value is not None:
                lines.append((tile.get("title") or "Chart", _num(value), ""))
        for cid in svc.chart_ids(dash.config or {}):
            chart = db.get(Chart, cid)
            if chart:
                dataset_ids.add(chart.dataset_id)
        if lines:
            out.append({"dashboard": dash.name, "link": dashboard_url(dash), "lines": lines})
        if len(out) >= MAX_DASHBOARDS:
            break
    datasets = [d for d in (db.get(Dataset, i) for i in dataset_ids) if d is not None]
    return out, datasets


def compose(db: Session, user: User) -> notify.Message | None:
    found, datasets = facts(db, user)
    if not found:
        return None
    lang = app_settings.effective(db, user)["language"]
    names = STATUS_AR if lang == "ar" else STATUS
    plain = []
    for block in found:
        plain.append(block["dashboard"])
        for label, value, status in block["lines"]:
            plain.append(f"- {label}: {value}" + (f" — {names[status]}" if status else ""))
        plain.append("")
    numbers = "\n".join(plain).strip()
    summary = ""
    try:
        reply, _ = generate(
            db,
            user=None,  # scheduled: not counted against the person's hourly limit
            feature="digest",
            datasets=datasets,
            system=(
                "Write a short morning summary (3 to 5 sentences) for a manager from these KPI figures. "
                "Lead with what needs attention (off track, at risk), then what is going well. Use the "
                "numbers. Never invent anything. Plain text. "
                + ("Write in Arabic." if lang == "ar" else "Write in English.")
            ),
            prompt=numbers,
            max_tokens=500,
        )
        summary = reply.text.strip()[:2000]
    except AiError as exc:
        log.info("Digest for %s without AI summary: %s", user.email, exc.message)
    title = "ملخصك اليومي" if lang == "ar" else "Your daily summary"
    text = (summary + "\n\n" if summary else "") + numbers
    rows = []
    for block in found:
        link_html = f'<a href="{html.escape(block["link"])}">{html.escape(block["dashboard"])}</a>'
        rows.append(f'<h3 style="margin:16px 0 4px">{link_html}</h3>')
        rows.append("<ul>")
        for label, value, status in block["lines"]:
            tail = f" — <b>{html.escape(names[status])}</b>" if status else ""
            rows.append(f"<li>{html.escape(label)}: {html.escape(value)}{tail}</li>")
        rows.append("</ul>")
    direction = ' dir="rtl"' if lang == "ar" else ""
    body = (
        f'<div{direction} style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1e293b">'
        + (f"<p>{html.escape(summary)}</p>" if summary else "")
        + "".join(rows)
        + "</div>"
    )
    link = found[0]["link"]
    return notify.Message(title, text, link, html=body)


def _sent_today(db: Session, user: User, start: datetime) -> bool:
    return (
        db.scalar(
            select(NotificationLog.id).where(
                NotificationLog.source == "digest",
                NotificationLog.source_id == user.id,
                NotificationLog.created_at >= start,
            )
        )
        is not None
    )


def send(db: Session, user: User) -> bool:
    message = compose(db, user)
    if message is None:
        return False
    result = notify.deliver(db, ["email"], [user], "digest", user.id, lambda _u: message)
    return result["sent"] > 0


def run_digests(now: datetime | None = None) -> int:
    """Called every 15 minutes. Sends today's digest to everyone who wants one and has not had it."""
    tz = ZoneInfo(get_settings().timezone)
    local = (now or datetime.now(tz)).astimezone(tz)
    if local.hour < SEND_FROM_HOUR:
        return 0
    start = datetime.combine(local.date(), time(0), tz)
    sent = 0
    with SessionLocal() as db:
        for user in db.scalars(select(User).where(User.is_active.is_(True))).all():
            if not wants_digest(user) or _sent_today(db, user, start):
                continue
            try:
                sent += send(db, user)
            except Exception:  # noqa: BLE001 - one person's digest must not stop the others
                log.exception("Daily digest failed for %s", user.email)
                db.rollback()
    return sent
