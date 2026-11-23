"""Signed embedding and display links.

Two ways to open a dashboard without a Lumen sign-in, both ending in a short "embed session" that can only
read that one dashboard, as one Lumen user (whose row-level security and dashboard access apply):

1. Embed keys (for another application, e.g. a portal): an admin creates a key and names the dashboards it may
   open. The application signs a token with the key's secret (HS256, header "kid" = key id) with claims
   {"dashboard": 12, "user": "person@company.ae", "exp": <at most 24 hours ahead>} and opens
   /embed/12#token=<token> in an iframe.
2. Display links (TV screens): an admin creates a link for one dashboard, run as a chosen user (for example a
   "TV" user with the Viewer role). Only a hash is stored; links can expire and be revoked.

The embed session token is sent in the X-Lumen-Embed header (never in a cookie), so it works in iframes and
cannot be used for anything but that dashboard.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt
from app.core.security import create_token, decode_token
from app.models import Dashboard, DisplayLink, EmbedKey, User
from app.services import dashboards as svc

HEADER = "x-lumen-embed"
SESSION_KEY = timedelta(hours=1)
SESSION_DISPLAY = timedelta(hours=12)
MAX_HOST_TOKEN = timedelta(hours=24)


class EmbedError(Exception):
    pass


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def _aware(v: datetime | None) -> datetime | None:
    return v if v is None or v.tzinfo else v.replace(tzinfo=UTC)


def _session(user: User, dashboard_id: int, source: str, lifetime: timedelta) -> dict:
    token = create_token(
        user.id, "embed", lifetime, user.token_version, {"dash": dashboard_id, "src": source}
    )
    return {"token": token, "dashboard_id": dashboard_id, "expires_in": int(lifetime.total_seconds())}


def session_from_key(db: Session, host_token: str) -> dict:
    try:
        header = jwt.get_unverified_header(host_token)
        key = db.get(EmbedKey, int(header.get("kid")))
    except (jwt.PyJWTError, TypeError, ValueError) as exc:
        raise EmbedError("This embed link is not valid") from exc
    if key is None or not key.enabled:
        raise EmbedError("This embed link is not valid")
    try:
        claims = jwt.decode(
            host_token, decrypt(key.secret_encrypted), algorithms=["HS256"], options={"require": ["exp"]}
        )
    except jwt.ExpiredSignatureError as exc:
        raise EmbedError("This embed link has expired") from exc
    except jwt.PyJWTError as exc:
        raise EmbedError("This embed link is not valid") from exc
    exp = datetime.fromtimestamp(int(claims["exp"]), UTC)
    if exp - datetime.now(UTC) > MAX_HOST_TOKEN:
        raise EmbedError("Embed tokens may be valid for at most 24 hours")
    try:
        dashboard_id = int(claims.get("dashboard"))
    except (TypeError, ValueError) as exc:
        raise EmbedError("The token does not name a dashboard") from exc
    if dashboard_id not in [int(d) for d in key.dashboards or []]:
        raise EmbedError("This key may not open that dashboard")
    email = str(claims.get("user") or "").strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email)) if email else None
    if user is None or not user.is_active:
        raise EmbedError("The token's user is not an active Lumen user")
    dash = db.get(Dashboard, dashboard_id)
    if dash is None or svc.share_level(db, user, dash) is None:
        raise EmbedError("That user cannot open this dashboard")
    key.last_used_at = datetime.now(UTC)
    db.commit()
    return _session(user, dashboard_id, f"key:{key.id}", SESSION_KEY)


def session_from_display(db: Session, token: str) -> dict:
    link = db.scalar(select(DisplayLink).where(DisplayLink.token_hash == hash_token(token)))
    if link is None or link.revoked:
        raise EmbedError("This display link is not valid")
    if link.expires_at and _aware(link.expires_at) < datetime.now(UTC):
        raise EmbedError("This display link has expired")
    user = db.get(User, link.run_as_id)
    dash = db.get(Dashboard, link.dashboard_id)
    if user is None or not user.is_active or dash is None or svc.share_level(db, user, dash) is None:
        raise EmbedError("This display link no longer has access to its dashboard")
    link.last_used_at = datetime.now(UTC)
    db.commit()
    return _session(user, link.dashboard_id, f"display:{link.id}", SESSION_DISPLAY)


def viewer_from_header(db: Session, value: str | None, dashboard_id: int) -> User | None:
    """The user of a valid embed session for this dashboard, or None."""
    if not value:
        return None
    payload = decode_token(value, "embed")
    if not payload or int(payload.get("dash", -1)) != dashboard_id:
        return None
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active or payload.get("ver") != user.token_version:
        return None
    kind, _, ref = str(payload.get("src", "")).partition(":")
    # Revoking a key or a link ends its sessions at once
    if kind == "key":
        key = db.get(EmbedKey, int(ref or 0))
        if key is None or not key.enabled or dashboard_id not in [int(d) for d in key.dashboards or []]:
            return None
    elif kind == "display":
        link = db.get(DisplayLink, int(ref or 0))
        if link is None or link.revoked or (link.expires_at and _aware(link.expires_at) < datetime.now(UTC)):
            return None
    else:
        return None
    return user
