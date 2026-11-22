from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.db import get_db
from app.models import Role, User

ACCESS_COOKIE = "lumen_access"
REFRESH_COOKIE = "lumen_refresh"
CSRF_HEADER = "x-requested-with"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def csrf_protect(request: Request) -> None:
    """Cookie-authenticated writes must carry a custom header.

    Browsers cannot send custom headers cross-site without a CORS preflight (which we never allow),
    so this blocks cross-site request forgery. API clients using Bearer tokens are exempt.
    """
    if request.method in SAFE_METHODS:
        return
    if request.headers.get("authorization", "").lower().startswith("bearer "):
        return
    if request.headers.get(CSRF_HEADER) != "lumen":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing CSRF header")


def _token_from_request(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(ACCESS_COOKIE)


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _token_from_request(request)
    payload = decode_token(token, "access") if token else None
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user = db.get(User, int(payload["sub"]))
    if not user or not user.is_active or payload.get("ver") != user.token_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    return user


def require_role(minimum: Role) -> Callable[..., User]:
    def checker(user: User = Depends(current_user)) -> User:
        if not user.has_role(minimum):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission for this action")
        return user

    return checker


require_admin = require_role(Role.admin)
