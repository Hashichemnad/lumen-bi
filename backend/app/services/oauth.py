"""OAuth 2.1 for the MCP server: dynamic client registration, authorization codes with PKCE (S256 only),
short-lived access tokens and rotating refresh tokens. Each token acts as one person, read-only.

Codes and tokens are random and stored only as SHA-256 hashes. A refresh token that is used twice (a sign
it was copied) revokes every token of that person for that app.
"""

import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AppSetting, OAuthClient, OAuthCode, OAuthToken, Role, User
from app.models.common import utcnow

SCOPE = "lumen.read"
CODE_MINUTES = 5
MAX_REGISTRATIONS_PER_DAY = 500


class OAuthError(Exception):
    def __init__(self, error: str, description: str, status: int = 400):
        super().__init__(description)
        self.error, self.description, self.status = error, description, status


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def base_url() -> str:
    return get_settings().public_url.rstrip("/")


def resource_url() -> str:
    return f"{base_url()}/mcp"


# ------------------------------------------------------------------ settings
def settings(db: Session) -> dict:
    row = db.get(AppSetting, "mcp")
    value = row.value if row else {}
    return {"enabled": bool(value.get("enabled", False)), "min_role": value.get("min_role", "explorer")}


def save_settings(db: Session, enabled: bool, min_role: str, user: User) -> dict:
    row = db.get(AppSetting, "mcp")
    if row is None:
        row = AppSetting(key="mcp", value={})
        db.add(row)
    row.value = {"enabled": enabled, "min_role": min_role}
    row.updated_by_id = user.id
    return row.value


def may_connect(db: Session, user: User) -> bool:
    s = settings(db)
    return s["enabled"] and user.is_active and user.has_role(Role(s["min_role"]))


# ------------------------------------------------------------------ clients
def _valid_redirect(uri: str) -> bool:
    u = urlparse(uri)
    if u.fragment or not u.netloc:
        return False
    if u.scheme == "https":
        return True
    # Native apps listen on the loopback address
    return u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1", "::1")


def register(db: Session, body: dict) -> OAuthClient:
    if not settings(db)["enabled"]:
        raise OAuthError("access_denied", "The Lumen MCP server is turned off", 403)
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not 1 <= len(uris) <= 10 or not all(isinstance(u, str) for u in uris):
        raise OAuthError("invalid_redirect_uri", "Give between 1 and 10 redirect_uris")
    bad = [u for u in uris if len(u) > 1000 or not _valid_redirect(u)]
    if bad:
        raise OAuthError(
            "invalid_redirect_uri", f"Redirect URIs must be https (or http on localhost): {bad[0][:100]}"
        )
    method = body.get("token_endpoint_auth_method", "none")
    if method != "none":
        raise OAuthError("invalid_client_metadata", "Only public clients (token_endpoint_auth_method none)")
    since = utcnow() - timedelta(days=1)
    recent = db.scalar(select(func.count()).select_from(OAuthClient).where(OAuthClient.created_at >= since))
    if (recent or 0) >= MAX_REGISTRATIONS_PER_DAY:
        raise OAuthError("temporarily_unavailable", "Too many apps registered today", 429)
    client = OAuthClient(
        client_id="lmn_c_" + secrets.token_urlsafe(18),
        name=str(body.get("client_name") or "AI app")[:200],
        redirect_uris=uris,
    )
    db.add(client)
    db.commit()
    return client


def client_for(db: Session, client_id: str, redirect_uri: str) -> OAuthClient:
    """Checks the client and its redirect URI. Errors here must NOT redirect (the URI is not trusted)."""
    client = db.scalar(select(OAuthClient).where(OAuthClient.client_id == client_id)) if client_id else None
    if client is None:
        raise OAuthError("invalid_client", "This app is not registered with Lumen")
    if redirect_uri not in (client.redirect_uris or []):
        raise OAuthError("invalid_request", "This app asked to return to an address it did not register")
    return client


def check_request(db: Session, q: dict) -> OAuthClient:
    client = client_for(db, str(q.get("client_id") or ""), str(q.get("redirect_uri") or ""))
    if q.get("response_type") != "code":
        raise OAuthError("unsupported_response_type", "Only response_type=code is supported")
    challenge = str(q.get("code_challenge") or "")
    if q.get("code_challenge_method") != "S256" or not 43 <= len(challenge) <= 128:
        raise OAuthError("invalid_request", "PKCE with code_challenge_method=S256 is required")
    resource = str(q.get("resource") or "")
    if resource and resource.rstrip("/") != resource_url():
        raise OAuthError("invalid_target", "This server only issues tokens for its own MCP endpoint")
    return client


def redirect_with(uri: str, params: dict) -> str:
    sep = "&" if urlparse(uri).query else "?"
    return uri + sep + urlencode({k: v for k, v in params.items() if v is not None})


def approve(db: Session, user: User, q: dict) -> str:
    """Creates a code and returns the address to send the browser back to."""
    check_request(db, q)
    if not may_connect(db, user):
        return redirect_with(
            q["redirect_uri"],
            {"error": "access_denied", "error_description": "Not allowed", "state": q.get("state")},
        )
    code = secrets.token_urlsafe(32)
    db.add(
        OAuthCode(
            code_hash=digest(code),
            client_id=q["client_id"],
            user_id=user.id,
            redirect_uri=q["redirect_uri"],
            code_challenge=q["code_challenge"],
            scope=SCOPE,
            resource=str(q.get("resource") or ""),
            expires_at=utcnow() + timedelta(minutes=CODE_MINUTES),
            used=False,
        )
    )
    db.commit()
    return redirect_with(q["redirect_uri"], {"code": code, "state": q.get("state"), "iss": base_url()})


def deny(db: Session, q: dict) -> str:
    check_request(db, q)
    return redirect_with(q["redirect_uri"], {"error": "access_denied", "state": q.get("state")})


# ------------------------------------------------------------------ tokens
def _issue(db: Session, client_id: str, user_id: int) -> dict:
    s = get_settings()
    access = "lmn_at_" + secrets.token_urlsafe(32)
    refresh = "lmn_rt_" + secrets.token_urlsafe(32)
    now = utcnow()
    db.add(
        OAuthToken(
            access_hash=digest(access),
            refresh_hash=digest(refresh),
            client_id=client_id,
            user_id=user_id,
            scope=SCOPE,
            access_expires_at=now + timedelta(minutes=s.mcp_access_token_minutes),
            refresh_expires_at=now + timedelta(days=s.mcp_refresh_token_days),
            revoked=False,
        )
    )
    db.commit()
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": s.mcp_access_token_minutes * 60,
        "refresh_token": refresh,
        "scope": SCOPE,
    }


def _pkce_ok(verifier: str, challenge: str) -> bool:
    if not 43 <= len(verifier) <= 128:
        return False
    computed = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return secrets.compare_digest(computed, challenge)


def exchange(db: Session, form: dict) -> dict:
    grant = form.get("grant_type")
    client_id = str(form.get("client_id") or "")
    if grant == "authorization_code":
        row = db.scalar(select(OAuthCode).where(OAuthCode.code_hash == digest(str(form.get("code") or ""))))
        if row is None or row.used or row.expires_at < utcnow() or row.client_id != client_id:
            raise OAuthError("invalid_grant", "The code is not valid (it may have expired or been used)")
        row.used = True
        db.commit()
        if row.redirect_uri != form.get("redirect_uri"):
            raise OAuthError("invalid_grant", "redirect_uri does not match")
        if not _pkce_ok(str(form.get("code_verifier") or ""), row.code_challenge):
            raise OAuthError("invalid_grant", "The code_verifier does not match")
        user = db.get(User, row.user_id)
        if user is None or not may_connect(db, user):
            raise OAuthError("invalid_grant", "This person may not connect apps")
        return _issue(db, client_id, user.id)
    if grant == "refresh_token":
        token = db.scalar(
            select(OAuthToken).where(OAuthToken.refresh_hash == digest(str(form.get("refresh_token") or "")))
        )
        if token is None or token.client_id != client_id:
            raise OAuthError("invalid_grant", "The refresh token is not valid")
        if token.revoked:
            # Used twice: someone else has a copy. Cut this app off for this person.
            revoke_app(db, token.user_id, token.client_id)
            raise OAuthError("invalid_grant", "The refresh token was already used; sign in again")
        if token.refresh_expires_at < utcnow():
            raise OAuthError("invalid_grant", "The refresh token has expired; sign in again")
        user = db.get(User, token.user_id)
        token.revoked = True
        db.commit()
        if user is None or not may_connect(db, user):
            raise OAuthError("invalid_grant", "This person may not connect apps")
        return _issue(db, client_id, user.id)
    raise OAuthError("unsupported_grant_type", "Use authorization_code or refresh_token")


def revoke_token(db: Session, value: str) -> None:
    h = digest(value)
    token = db.scalar(
        select(OAuthToken).where((OAuthToken.access_hash == h) | (OAuthToken.refresh_hash == h))
    )
    if token is not None:
        token.revoked = True
        db.commit()


def revoke_app(db: Session, user_id: int, client_id: str) -> int:
    rows = db.scalars(
        select(OAuthToken).where(
            OAuthToken.user_id == user_id, OAuthToken.client_id == client_id, OAuthToken.revoked.is_(False)
        )
    ).all()
    for t in rows:
        t.revoked = True
    db.commit()
    return len(rows)


def user_for_token(db: Session, value: str) -> tuple[User, OAuthToken] | None:
    if not value.startswith("lmn_at_"):
        return None
    token = db.scalar(select(OAuthToken).where(OAuthToken.access_hash == digest(value)))
    if token is None or token.revoked or token.access_expires_at < utcnow():
        return None
    user = db.get(User, token.user_id)
    if user is None or not may_connect(db, user):
        return None
    now = utcnow()
    if token.last_used_at is None or now - token.last_used_at > timedelta(minutes=1):
        token.last_used_at = now
        db.commit()
    return user, token


def connections(db: Session, user_id: int | None = None) -> list[dict]:
    """Apps with a live sign-in (a refresh token that is not revoked or expired), per person."""
    stmt = select(OAuthToken).where(OAuthToken.revoked.is_(False), OAuthToken.refresh_expires_at > utcnow())
    if user_id is not None:
        stmt = stmt.where(OAuthToken.user_id == user_id)
    out: dict[tuple[int, str], dict] = {}
    names = {c.client_id: c.name for c in db.scalars(select(OAuthClient))}
    for t in db.scalars(stmt.order_by(OAuthToken.created_at)):
        key = (t.user_id, t.client_id)
        item = out.setdefault(
            key,
            {"user_id": t.user_id, "client_id": t.client_id, "name": names.get(t.client_id, "AI app"),
             "connected_at": t.created_at, "last_used_at": t.last_used_at},
        )  # fmt: skip
        if t.last_used_at and (item["last_used_at"] is None or t.last_used_at > item["last_used_at"]):
            item["last_used_at"] = t.last_used_at
    return list(out.values())
