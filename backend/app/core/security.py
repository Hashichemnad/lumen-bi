import hashlib
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import get_settings

_hasher = PasswordHasher()
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def password_problems(password: str) -> list[str]:
    """Return a list of policy violations (empty list = acceptable)."""
    problems = []
    if len(password) < 10:
        problems.append("at least 10 characters")
    if not re.search(r"[A-Za-z]", password):
        problems.append("a letter")
    if not re.search(r"\d", password):
        problems.append("a number")
    return problems


def create_token(
    user_id: int, kind: str, lifetime: timedelta, token_version: int, extra: dict | None = None
) -> str:
    now = datetime.now(UTC)
    payload = {
        **(extra or {}),
        "sub": str(user_id),
        "type": kind,
        "ver": token_version,
        "iat": now,
        "exp": now + lifetime,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, get_settings().secret_key, algorithm=ALGORITHM)


def create_access_token(user_id: int, token_version: int, idp: int | None = None) -> str:
    minutes = get_settings().access_token_minutes
    return create_token(user_id, "access", timedelta(minutes=minutes), token_version, {"idp": idp})


def create_refresh_token(user_id: int, token_version: int, idp: int | None = None) -> str:
    days = get_settings().refresh_token_days
    return create_token(user_id, "refresh", timedelta(days=days), token_version, {"idp": idp})


def create_state_token(data: dict, minutes: int) -> str:
    """Signed, short-lived token holding SSO state (stored in a cookie during the redirect)."""
    payload = {**data, "type": "sso_state", "exp": datetime.now(UTC) + timedelta(minutes=minutes)}
    return jwt.encode(payload, get_settings().secret_key, algorithm=ALGORITHM)


def decode_state_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    return payload if payload.get("type") == "sso_state" else None


def decode_token(token: str, kind: str) -> dict | None:
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != kind:
        return None
    return payload


def new_reset_token() -> tuple[str, str]:
    """Return (token for the email link, sha256 hash to store)."""
    token = secrets.token_urlsafe(32)
    return token, hash_reset_token(token)


def hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
