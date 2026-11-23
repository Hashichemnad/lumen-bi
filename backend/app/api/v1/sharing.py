"""Subscriptions, alerts, notification channels and embedding.

GET/PUT /admin/settings/notifications, POST /admin/settings/notifications/test     admins
GET  /dashboards/{id}/channels                 channels this person may use on this dashboard
GET/POST /dashboards/{id}/subscriptions, PUT/DELETE /subscriptions/{id}, POST /subscriptions/{id}/send
GET/POST /dashboards/{id}/alerts, PUT/DELETE /alerts/{id}, POST /alerts/{id}/check
GET/POST /admin/embed-keys, PUT/DELETE /admin/embed-keys/{id}                       admins
GET/POST /dashboards/{id}/display-links, DELETE /display-links/{id}               admins
POST /embed/session                            no sign-in: an embed token or a display link -> session

Anyone who can view a dashboard may subscribe themselves and set alerts for themselves. People who manage
the dashboard may add others (each only gets what they can see) and post to Teams channels.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_role
from app.core.config import get_settings
from app.core.crypto import encrypt
from app.db import get_db
from app.models import (
    Alert,
    Dashboard,
    DisplayLink,
    EmbedKey,
    Group,
    NotificationLog,
    Role,
    Subscription,
    User,
)
from app.services import audit, delivery, embed, notify
from app.services import dashboards as svc
from app.services.sync import next_run, valid_cron

router = APIRouter(tags=["subscriptions"])
require_admin = require_role(Role.admin)


# ------------------------------------------------------------------ notification settings
@router.get("/admin/settings/notifications")
def get_notifications(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return notify.public_settings(db)


@router.put("/admin/settings/notifications")
def set_notifications(
    body: dict[str, Any], request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    try:
        out = notify.save_settings(db, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit.record(
        db,
        "settings.update",
        user=user,
        request=request,
        object_type="settings",
        object_id="notifications",
        details={"teams": [t["name"] for t in out["teams"]], "whatsapp": out["whatsapp"]["enabled"]},
        commit=False,
    )
    db.commit()
    return out


class TestIn(BaseModel):
    channel: str


@router.post("/admin/settings/notifications/test")
def test_notification(body: TestIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    if body.channel not in notify.channel_names(db):
        raise HTTPException(422, "Unknown channel")
    msg = notify.Message(
        "Lumen test message", "Notifications from Lumen reach you here.", get_settings().public_url
    )
    result = notify.deliver(db, [body.channel], [user], "test", 0, lambda _u: msg)
    last = db.scalar(select(NotificationLog).order_by(NotificationLog.id.desc()))
    return {**result, "error": last.error if last and last.status != "sent" else ""}


# ------------------------------------------------------------------ shared helpers
class Recipients(BaseModel):
    users: list[int] = []
    groups: list[int] = []


def _dashboard(db: Session, dashboard_id: int, user: User) -> tuple[Dashboard, str]:
    d = db.get(Dashboard, dashboard_id)
    level = svc.share_level(db, user, d) if d else None
    if d is None or level is None:
        raise HTTPException(404, "Dashboard not found")
    return d, level


def _allowed_channels(db: Session, level: str) -> dict[str, str]:
    names = notify.channel_names(db)
    if level != "manage":
        names = {k: v for k, v in names.items() if not k.startswith("teams:")}
    return names


def _check_recipients(db: Session, user: User, level: str, r: Recipients) -> dict:
    if level != "manage" and (r.groups or set(r.users) - {user.id}):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only people who manage this dashboard can add other recipients"
        )
    for uid in r.users:
        if db.get(User, uid) is None:
            raise HTTPException(422, f"Unknown user {uid}")
    for gid in r.groups:
        if db.get(Group, gid) is None:
            raise HTTPException(422, f"Unknown group {gid}")
    if not r.users and not r.groups:
        r.users = [user.id]
    return {"users": sorted(set(r.users)), "groups": sorted(set(r.groups))}


def _check_channels(db: Session, level: str, channels: list[str]) -> list[str]:
    allowed = _allowed_channels(db, level)
    if not channels:
        raise HTTPException(422, "Choose at least one way to send it")
    for ch in channels:
        if ch not in allowed:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN if ch.startswith("teams:") else 422,
                "Only people who manage this dashboard can post to Teams"
                if ch.startswith("teams:")
                else f"Unknown channel {ch}",
            )
    return list(dict.fromkeys(channels))


def _cron(value: str) -> str:
    value = value.strip()
    if not valid_cron(value):
        raise HTTPException(422, "The schedule is not valid")
    return value


def _history(db: Session, source: str, source_id: int) -> list[dict]:
    rows = db.scalars(
        select(NotificationLog)
        .where(NotificationLog.source == source, NotificationLog.source_id == source_id)
        .order_by(NotificationLog.id.desc())
        .limit(10)
    )
    return [
        {
            "at": r.created_at,
            "channel": r.channel,
            "recipient": r.recipient,
            "status": r.status,
            "error": r.error,
        }
        for r in rows
    ]


@router.get("/dashboards/{dashboard_id}/channels")
def channels(dashboard_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _, level = _dashboard(db, dashboard_id, user)
    return [{"id": k, "name": v} for k, v in _allowed_channels(db, level).items()]


# ------------------------------------------------------------------ subscriptions
class SubscriptionIn(BaseModel):
    name: str = Field("", max_length=200)
    recipients: Recipients = Recipients()
    channels: list[str] = ["email"]
    format: Literal["pdf", "png", "link"] = "pdf"
    schedule: str = "0 7 * * 1"
    state: str | None = Field(None, max_length=4000)
    message: str = Field("", max_length=2000)
    enabled: bool = True


def _sub_out(db: Session, s: Subscription, user: User, level: str) -> dict:
    owner = db.get(User, s.owner_id) if s.owner_id else None
    return {
        "id": s.id,
        "name": s.name,
        "recipients": s.recipients,
        "channels": s.channels,
        "format": s.format,
        "schedule": s.schedule,
        "state": s.state,
        "message": s.message,
        "enabled": s.enabled,
        "next_run_at": s.next_run_at,
        "last_run_at": s.last_run_at,
        "last_status": s.last_status,
        "last_error": s.last_error,
        "owner": (owner.name or owner.email) if owner else "",
        "can_edit": s.owner_id == user.id or level == "manage",
        "history": _history(db, "subscription", s.id),
    }


def _mine_or_managed(db: Session, model, item_id: int, user: User):
    item = db.get(model, item_id)
    if item is None:
        raise HTTPException(404, "Not found")
    d, level = _dashboard(db, item.dashboard_id, user)
    if item.owner_id != user.id and level != "manage":
        raise HTTPException(404, "Not found")
    return item, d, level


@router.get("/dashboards/{dashboard_id}/subscriptions")
def list_subscriptions(dashboard_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    d, level = _dashboard(db, dashboard_id, user)
    stmt = select(Subscription).where(Subscription.dashboard_id == d.id).order_by(Subscription.id)
    rows = [s for s in db.scalars(stmt) if level == "manage" or s.owner_id == user.id]
    return [_sub_out(db, s, user, level) for s in rows]


@router.post("/dashboards/{dashboard_id}/subscriptions", status_code=status.HTTP_201_CREATED)
def add_subscription(
    dashboard_id: int,
    body: SubscriptionIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    d, level = _dashboard(db, dashboard_id, user)
    s = Subscription(
        dashboard_id=d.id,
        owner_id=user.id,
        name=body.name.strip() or d.name,
        recipients=_check_recipients(db, user, level, body.recipients),
        channels=_check_channels(db, level, body.channels),
        format=body.format,
        schedule=_cron(body.schedule),
        state=body.state,
        message=body.message,
        enabled=body.enabled,
    )
    s.next_run_at = next_run(s.schedule) if s.enabled else None
    db.add(s)
    db.flush()
    audit.record(
        db,
        "subscription.create",
        user=user,
        request=request,
        object_type="dashboard",
        object_id=d.id,
        details={"subscription": s.id, "recipients": s.recipients, "channels": s.channels},
        commit=False,
    )
    db.commit()
    return _sub_out(db, s, user, level)


@router.put("/subscriptions/{subscription_id}")
def change_subscription(
    subscription_id: int,
    body: SubscriptionIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    s, d, level = _mine_or_managed(db, Subscription, subscription_id, user)
    s.name = body.name.strip() or d.name
    s.recipients = _check_recipients(db, user, level, body.recipients)
    s.channels = _check_channels(db, level, body.channels)
    s.format, s.schedule, s.state, s.message, s.enabled = (
        body.format,
        _cron(body.schedule),
        body.state,
        body.message,
        body.enabled,
    )
    s.next_run_at = next_run(s.schedule) if s.enabled else None
    db.commit()
    return _sub_out(db, s, user, level)


@router.delete("/subscriptions/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_subscription(
    subscription_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    s, _, _ = _mine_or_managed(db, Subscription, subscription_id, user)
    db.delete(s)
    db.commit()


@router.post("/subscriptions/{subscription_id}/send")
def send_subscription(
    subscription_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    s, _, level = _mine_or_managed(db, Subscription, subscription_id, user)
    result = delivery.run_subscription(db, s)
    return {**result, "subscription": _sub_out(db, s, user, level)}


# ------------------------------------------------------------------ alerts
class Condition(BaseModel):
    kind: Literal["value", "kpi", "anomaly"] = "value"
    op: Literal["gt", "gte", "lt", "lte"] = "gt"
    value: float = 0
    status: Literal["off_track", "at_risk"] = "off_track"
    # anomaly: how unusual (high = tells you sooner) and which way
    sensitivity: Literal["low", "medium", "high"] = "medium"
    direction: Literal["both", "up", "down"] = "both"


class AlertIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    tile_id: str = Field(min_length=1, max_length=40)
    condition: Condition
    recipients: Recipients = Recipients()
    channels: list[str] = ["email"]
    schedule: str = "*/30 * * * *"
    enabled: bool = True


def _alert_out(db: Session, a: Alert, user: User, level: str) -> dict:
    owner = db.get(User, a.owner_id) if a.owner_id else None
    return {
        "id": a.id,
        "name": a.name,
        "tile_id": a.tile_id,
        "condition": a.condition,
        "description": delivery.describe_condition(a),
        "recipients": a.recipients,
        "channels": a.channels,
        "schedule": a.schedule,
        "enabled": a.enabled,
        "triggered": user.id in (a.triggered_for or []),
        "last_value": a.last_value if a.owner_id == user.id else "",
        "last_error": a.last_error,
        "last_checked_at": a.last_checked_at,
        "last_triggered_at": a.last_triggered_at,
        "owner": (owner.name or owner.email) if owner else "",
        "can_edit": a.owner_id == user.id or level == "manage",
        "history": _history(db, "alert", a.id),
    }


def _check_target(d: Dashboard, body: AlertIn) -> dict:
    tile = next((i for i in svc.items(d.config or {}) if i["id"] == body.tile_id), None)
    if body.condition.kind == "kpi":
        if tile is None or tile.get("kind") != "section" or not tile.get("goal"):
            raise HTTPException(422, "Choose a KPI section that has a goal")
        return {"kind": "kpi", "status": body.condition.status}
    if tile is None or tile.get("kind") != "chart":
        raise HTTPException(422, "Choose a chart on this dashboard")
    if body.condition.kind == "anomaly":
        return {
            "kind": "anomaly",
            "sensitivity": body.condition.sensitivity,
            "direction": body.condition.direction,
        }
    return {"kind": "value", "op": body.condition.op, "value": body.condition.value}


@router.get("/dashboards/{dashboard_id}/alerts")
def list_alerts(dashboard_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    d, level = _dashboard(db, dashboard_id, user)
    rows = [
        a
        for a in db.scalars(select(Alert).where(Alert.dashboard_id == d.id).order_by(Alert.id))
        if level == "manage" or a.owner_id == user.id
    ]
    return [_alert_out(db, a, user, level) for a in rows]


@router.post("/dashboards/{dashboard_id}/alerts", status_code=status.HTTP_201_CREATED)
def add_alert(
    dashboard_id: int,
    body: AlertIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    d, level = _dashboard(db, dashboard_id, user)
    a = Alert(
        dashboard_id=d.id,
        tile_id=body.tile_id,
        owner_id=user.id,
        name=body.name.strip(),
        condition=_check_target(d, body),
        recipients=_check_recipients(db, user, level, body.recipients),
        channels=_check_channels(db, level, body.channels),
        schedule=_cron(body.schedule),
        enabled=body.enabled,
        triggered_for=[],
    )
    a.next_run_at = next_run(a.schedule) if a.enabled else None
    db.add(a)
    db.flush()
    audit.record(
        db,
        "alert.create",
        user=user,
        request=request,
        object_type="dashboard",
        object_id=d.id,
        details={"alert": a.id, "name": a.name, "recipients": a.recipients, "channels": a.channels},
        commit=False,
    )
    db.commit()
    return _alert_out(db, a, user, level)


@router.put("/alerts/{alert_id}")
def change_alert(
    alert_id: int, body: AlertIn, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    a, d, level = _mine_or_managed(db, Alert, alert_id, user)
    a.name, a.tile_id = body.name.strip(), body.tile_id
    a.condition = _check_target(d, body)
    a.recipients = _check_recipients(db, user, level, body.recipients)
    a.channels = _check_channels(db, level, body.channels)
    a.schedule, a.enabled, a.triggered_for = _cron(body.schedule), body.enabled, []
    a.next_run_at = next_run(a.schedule) if a.enabled else None
    db.commit()
    return _alert_out(db, a, user, level)


@router.delete("/alerts/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alert(alert_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    a, _, _ = _mine_or_managed(db, Alert, alert_id, user)
    db.delete(a)
    db.commit()


@router.post("/alerts/{alert_id}/check")
def check_alert(alert_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    a, _, level = _mine_or_managed(db, Alert, alert_id, user)
    result = delivery.run_alert(db, a)
    return {**result, "alert": _alert_out(db, a, user, level)}


# ------------------------------------------------------------------ embed keys (admins)
class EmbedKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    dashboards: list[int] = []
    enabled: bool = True


def _key_out(k: EmbedKey, secret: str | None = None) -> dict:
    out = {
        "id": k.id,
        "name": k.name,
        "dashboards": k.dashboards,
        "enabled": k.enabled,
        "created_at": k.created_at,
        "last_used_at": k.last_used_at,
    }
    if secret:
        out["secret"] = secret  # shown once
    return out


def _check_dashboards(db: Session, ids: list[int]) -> list[int]:
    for i in ids:
        if db.get(Dashboard, i) is None:
            raise HTTPException(422, f"Unknown dashboard {i}")
    return sorted(set(ids))


@router.get("/admin/embed-keys")
def list_keys(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return [_key_out(k) for k in db.scalars(select(EmbedKey).order_by(EmbedKey.id))]


@router.post("/admin/embed-keys", status_code=status.HTTP_201_CREATED)
def add_key(
    body: EmbedKeyIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    secret = embed.new_secret()
    k = EmbedKey(
        name=body.name.strip(),
        secret_encrypted=encrypt(secret),
        dashboards=_check_dashboards(db, body.dashboards),
        enabled=body.enabled,
        created_by_id=user.id,
    )
    db.add(k)
    db.flush()
    audit.record(
        db,
        "embed.key_create",
        user=user,
        request=request,
        object_type="embed_key",
        object_id=k.id,
        details={"name": k.name, "dashboards": k.dashboards},
        commit=False,
    )
    db.commit()
    return _key_out(k, secret)


@router.put("/admin/embed-keys/{key_id}")
def change_key(
    key_id: int,
    body: EmbedKeyIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    k = db.get(EmbedKey, key_id)
    if k is None:
        raise HTTPException(404, "Key not found")
    k.name, k.dashboards, k.enabled = body.name.strip(), _check_dashboards(db, body.dashboards), body.enabled
    audit.record(
        db,
        "embed.key_update",
        user=user,
        request=request,
        object_type="embed_key",
        object_id=k.id,
        details={"name": k.name, "dashboards": k.dashboards, "enabled": k.enabled},
        commit=False,
    )
    db.commit()
    return _key_out(k)


@router.delete("/admin/embed-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_key(
    key_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    k = db.get(EmbedKey, key_id)
    if k is None:
        raise HTTPException(404, "Key not found")
    db.delete(k)
    audit.record(
        db, "embed.key_delete", user=user, request=request, object_type="embed_key", object_id=key_id
    )


# ------------------------------------------------------------------ display links (admins)
class DisplayIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    run_as_id: int
    expires_days: int | None = Field(None, ge=1, le=3650)


def _link_out(db: Session, link: DisplayLink, token: str | None = None) -> dict:
    who = db.get(User, link.run_as_id)
    out = {
        "id": link.id,
        "name": link.name,
        "run_as": {"id": link.run_as_id, "name": (who.name or who.email) if who else ""},
        "created_at": link.created_at,
        "expires_at": link.expires_at,
        "revoked": link.revoked,
        "last_used_at": link.last_used_at,
    }
    if token:
        out["url"] = f"{get_settings().public_url.rstrip('/')}/display#{token}"  # shown once
    return out


@router.get("/dashboards/{dashboard_id}/display-links")
def list_links(dashboard_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    rows = db.scalars(
        select(DisplayLink).where(DisplayLink.dashboard_id == dashboard_id).order_by(DisplayLink.id)
    )
    return [_link_out(db, link) for link in rows]


@router.post("/dashboards/{dashboard_id}/display-links", status_code=status.HTTP_201_CREATED)
def add_link(
    dashboard_id: int,
    body: DisplayIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    d = db.get(Dashboard, dashboard_id)
    if d is None:
        raise HTTPException(404, "Dashboard not found")
    who = db.get(User, body.run_as_id)
    if who is None or not who.is_active:
        raise HTTPException(422, "Choose an active user to show the dashboard as")
    if who.has_role(Role.admin):
        raise HTTPException(422, "Do not show a dashboard as an admin: admins see every row")
    if svc.share_level(db, who, d) is None:
        raise HTTPException(422, f"{who.email} cannot open this dashboard. Share it with them first.")
    token = embed.new_secret()
    link = DisplayLink(
        dashboard_id=d.id,
        run_as_id=who.id,
        name=body.name.strip(),
        token_hash=embed.hash_token(token),
        created_by_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(days=body.expires_days) if body.expires_days else None,
    )
    db.add(link)
    db.flush()
    audit.record(
        db,
        "embed.display_create",
        user=user,
        request=request,
        object_type="dashboard",
        object_id=d.id,
        details={"link": link.id, "run_as": who.email},
        commit=False,
    )
    db.commit()
    return _link_out(db, link, token)


@router.delete("/display-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_link(
    link_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    link = db.get(DisplayLink, link_id)
    if link is None:
        raise HTTPException(404, "Link not found")
    link.revoked = True
    audit.record(
        db,
        "embed.display_revoke",
        user=user,
        request=request,
        object_type="dashboard",
        object_id=link.dashboard_id,
        details={"link": link.id},
        commit=False,
    )
    db.commit()


# ------------------------------------------------------------------ embed sessions (no sign-in)
class SessionIn(BaseModel):
    token: str | None = Field(None, max_length=4000)  # signed by an embedding application
    display: str | None = Field(None, max_length=200)  # a display link's token


@router.post("/embed/session")
def embed_session(body: SessionIn, request: Request, db: Session = Depends(get_db)):
    try:
        if body.display:
            out = embed.session_from_display(db, body.display)
        elif body.token:
            out = embed.session_from_key(db, body.token)
        else:
            raise embed.EmbedError("No token")
    except embed.EmbedError as exc:
        audit.record(db, "embed.refused", request=request, details={"reason": str(exc)})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    return out
