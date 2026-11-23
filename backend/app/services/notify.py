"""Sending notifications: email, Microsoft Teams and WhatsApp.

Channels are set up by admins (Admin → Settings → Notifications), stored in app_settings key
"notifications"; secrets (webhook addresses, the WhatsApp token) are encrypted and never returned.

- Email goes to each person (their own copy, with their own data).
- Teams: an incoming webhook of a channel ("Workflows" → "Post to a channel when a webhook request is
  received"). One message per channel, with a button to open the dashboard.
- WhatsApp: the WhatsApp Business Cloud API (Meta). Messages started by a business must use an approved
  template; Lumen's template has three body values: {{1}} title, {{2}} message, {{3}} link. Each person's
  number is read from a user attribute (default "phone"), in international format.
"""

import logging
import re
import secrets
from dataclasses import dataclass, field

import httpx
from sqlalchemy.orm import Session

from app.core.crypto import decrypt, encrypt
from app.models import AppSetting, Group, NotificationLog, User
from app.services.email import send_email

log = logging.getLogger("lumen.notify")
GRAPH = "https://graph.facebook.com/v21.0"
PHONE_RE = re.compile(r"^\+?[1-9]\d{7,14}$")


# ------------------------------------------------------------------ settings
def _raw(db: Session) -> dict:
    row = db.get(AppSetting, "notifications")
    return dict(row.value) if row and row.value else {}


def public_settings(db: Session) -> dict:
    raw = _raw(db)
    wa = raw.get("whatsapp") or {}
    return {
        "teams": [
            {"id": t["id"], "name": t["name"], "has_url": bool(t.get("url"))} for t in raw.get("teams", [])
        ],
        "whatsapp": {
            "enabled": bool(wa.get("enabled")),
            "phone_number_id": wa.get("phone_number_id", ""),
            "template": wa.get("template", "lumen_alert"),
            "language": wa.get("language", "en"),
            "phone_attribute": wa.get("phone_attribute", "phone"),
            "has_token": bool(wa.get("token")),
        },
    }


def save_settings(db: Session, body: dict) -> dict:
    """body as from public_settings, with "url" / "token" only when they change ("" keeps the stored one)."""
    raw = _raw(db)
    old_teams = {t["id"]: t for t in raw.get("teams", [])}
    teams = []
    for t in body.get("teams", [])[:20]:
        tid = t.get("id") if t.get("id") in old_teams else secrets.token_hex(4)
        url = (t.get("url") or "").strip()
        if url and not url.startswith("https://"):
            raise ValueError("A Teams webhook address starts with https://")
        stored = encrypt(url) if url else old_teams.get(tid, {}).get("url", "")
        name = str(t.get("name") or "").strip()[:100]
        if not name:
            raise ValueError("Give each Teams channel a name")
        if not stored:
            raise ValueError(f'Paste the webhook address of "{name}"')
        teams.append({"id": tid, "name": name, "url": stored})
    wa_in = body.get("whatsapp") or {}
    wa_old = raw.get("whatsapp") or {}
    token = (wa_in.get("token") or "").strip()
    wa = {
        "enabled": bool(wa_in.get("enabled")),
        "phone_number_id": str(wa_in.get("phone_number_id") or "").strip()[:40],
        "template": str(wa_in.get("template") or "lumen_alert").strip()[:100],
        "language": str(wa_in.get("language") or "en").strip()[:10],
        "phone_attribute": str(wa_in.get("phone_attribute") or "phone").strip()[:50],
        "token": encrypt(token) if token else wa_old.get("token", ""),
    }
    if wa["enabled"] and not (wa["phone_number_id"] and wa["token"]):
        raise ValueError("WhatsApp needs the phone number ID and an access token")
    row = db.get(AppSetting, "notifications")
    if row is None:
        row = AppSetting(key="notifications", value={})
        db.add(row)
    row.value = {"teams": teams, "whatsapp": wa}
    db.flush()
    return public_settings(db)


def channel_names(db: Session) -> dict[str, str]:
    raw = _raw(db)
    out = {"email": "Email"}
    for t in raw.get("teams", []):
        out[f"teams:{t['id']}"] = f"Teams: {t['name']}"
    if (raw.get("whatsapp") or {}).get("enabled"):
        out["whatsapp"] = "WhatsApp"
    return out


# ------------------------------------------------------------------ recipients
def recipients(db: Session, spec: dict) -> list[User]:
    """Active users named directly or through groups, each once."""
    ids = set(int(i) for i in spec.get("users", []) or [])
    for gid in spec.get("groups", []) or []:
        g = db.get(Group, int(gid))
        if g is not None:
            ids.update(u.id for u in g.members)
    users = [db.get(User, i) for i in sorted(ids)]
    return [u for u in users if u is not None and u.is_active]


# ------------------------------------------------------------------ sending
@dataclass
class Message:
    title: str
    text: str
    link: str
    html: str | None = None
    attachments: list[tuple[str, bytes, str]] = field(default_factory=list)


def _log(
    db: Session, source: str, source_id: int, channel: str, recipient: str, status: str, error: str = ""
):
    db.add(
        NotificationLog(
            source=source,
            source_id=source_id,
            channel=channel,
            recipient=recipient[:320],
            status=status,
            error=error[:2000],
        )
    )


def send_email_to(db: Session, user: User, msg: Message, source: str, source_id: int) -> bool:
    try:
        send_email(user.email, msg.title, f"{msg.text}\n\n{msg.link}\n", msg.html, msg.attachments)
        _log(db, source, source_id, "email", user.email, "sent")
        return True
    except Exception as exc:  # noqa: BLE001 - one failed address must not stop the others
        log.exception("Email to %s failed", user.email)
        _log(db, source, source_id, "email", user.email, "failed", str(exc))
        return False


def send_teams(db: Session, channel_id: str, msg: Message, source: str, source_id: int) -> bool:
    team = next((t for t in _raw(db).get("teams", []) if t["id"] == channel_id), None)
    if team is None:
        _log(db, source, source_id, f"teams:{channel_id}", "", "failed", "This Teams channel was removed")
        return False
    card = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": msg.title,
                            "weight": "Bolder",
                            "size": "Medium",
                            "wrap": True,
                        },
                        {"type": "TextBlock", "text": msg.text, "wrap": True},
                    ],
                    "actions": [{"type": "Action.OpenUrl", "title": "Open in Lumen", "url": msg.link}],
                },
            }
        ],
    }
    try:
        r = httpx.post(decrypt(team["url"]), json=card, timeout=15)
        r.raise_for_status()
        _log(db, source, source_id, f"teams:{channel_id}", team["name"], "sent")
        return True
    except Exception as exc:  # noqa: BLE001
        _log(db, source, source_id, f"teams:{channel_id}", team["name"], "failed", f"{type(exc).__name__}")
        return False


def phone_of(user: User, attribute: str) -> str | None:
    value = (user.attributes or {}).get(attribute)
    if isinstance(value, list):
        value = value[0] if value else None
    phone = re.sub(r"[\s\-()]", "", str(value or ""))
    return phone.lstrip("+") if PHONE_RE.match(phone) else None


def send_whatsapp(db: Session, user: User, msg: Message, source: str, source_id: int) -> bool:
    wa = _raw(db).get("whatsapp") or {}
    if not wa.get("enabled"):
        _log(db, source, source_id, "whatsapp", user.email, "skipped", "WhatsApp is not set up")
        return False
    phone = phone_of(user, wa.get("phone_attribute") or "phone")
    if not phone:
        _log(db, source, source_id, "whatsapp", user.email, "skipped", "No phone number on this user")
        return False
    body = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": wa.get("template") or "lumen_alert",
            "language": {"code": wa.get("language") or "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": msg.title[:200]},
                        {"type": "text", "text": msg.text[:600]},
                        {"type": "text", "text": msg.link},
                    ],
                }
            ],
        },
    }
    try:
        r = httpx.post(
            f"{GRAPH}/{wa['phone_number_id']}/messages",
            json=body,
            headers={"Authorization": f"Bearer {decrypt(wa['token'])}"},
            timeout=15,
        )
        r.raise_for_status()
        _log(db, source, source_id, "whatsapp", f"+{phone}", "sent")
        return True
    except Exception as exc:  # noqa: BLE001
        _log(db, source, source_id, "whatsapp", f"+{phone}", "failed", f"{type(exc).__name__}")
        return False


def deliver(
    db: Session,
    channels: list[str],
    people: list[User],
    source: str,
    source_id: int,
    message_for,  # (user | None) -> Message; None = the shared message for Teams
) -> dict[str, int]:
    """Send on every channel. Personal channels (email, WhatsApp) get each person's own message."""
    sent = failed = 0
    for ch in channels:
        if ch == "email" or ch == "whatsapp":
            for u in people:
                ok = (send_email_to if ch == "email" else send_whatsapp)(
                    db, u, message_for(u), source, source_id
                )
                sent, failed = sent + ok, failed + (not ok)
        elif ch.startswith("teams:"):
            ok = send_teams(db, ch.split(":", 1)[1], message_for(None), source, source_id)
            sent, failed = sent + ok, failed + (not ok)
    db.commit()
    return {"sent": sent, "failed": failed}
