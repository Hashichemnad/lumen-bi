from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import ACCESS_COOKIE, REFRESH_COOKIE, current_user
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_reset_token,
    verify_password,
)
from app.db import get_db
from app.models import PasswordResetToken, User
from app.schemas import ForgotPasswordIn, LoginIn, PasswordChangeIn, ResetPasswordIn, UserOut
from app.services import audit
from app.services.users import is_locked, send_reset_email, set_password, to_out

router = APIRouter(prefix="/auth", tags=["auth"])

# Verifying against a dummy hash for unknown emails keeps response time equal (no account enumeration)
_DUMMY_HASH = hash_password("dummy-password-for-timing")
BAD_LOGIN = "Incorrect email or password"


def set_session_cookies(response: Response, user: User) -> None:
    s = get_settings()
    common = {"httponly": True, "secure": s.cookie_secure, "samesite": "lax"}
    response.set_cookie(
        ACCESS_COOKIE,
        create_access_token(user.id, user.token_version),
        max_age=s.access_token_minutes * 60,
        path="/api",
        **common,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        create_refresh_token(user.id, user.token_version),
        max_age=s.refresh_token_days * 86400,
        path="/api/v1/auth",
        **common,
    )


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/api")
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    s = get_settings()
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None:
        verify_password(body.password, _DUMMY_HASH)
        audit.record(db, "auth.login_failed", request=request, details={"email": body.email.lower()})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)

    if is_locked(user):
        audit.record(db, "auth.login_locked", user=user, request=request)
        raise HTTPException(
            status.HTTP_423_LOCKED, "Too many failed attempts. Try again later or reset your password."
        )

    if not user.is_active or not verify_password(body.password, user.password_hash):
        if user.is_active:
            user.failed_logins += 1
            if user.failed_logins >= s.max_failed_logins:
                user.locked_until = datetime.now(UTC) + timedelta(minutes=s.lockout_minutes)
                user.failed_logins = 0
        audit.record(db, "auth.login_failed", user=user, request=request)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)

    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = datetime.now(UTC)
    audit.record(db, "auth.login", user=user, request=request, details={"method": "password"})
    set_session_cookies(response, user)
    return to_out(user)


@router.post("/refresh", response_model=UserOut)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(REFRESH_COOKIE)
    payload = decode_token(token, "refresh") if token else None
    user = db.get(User, int(payload["sub"])) if payload else None
    if not user or not user.is_active or payload.get("ver") != user.token_version:
        clear_session_cookies(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    set_session_cookies(response, user)
    return to_out(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, everywhere: bool = False, db: Session = Depends(get_db)):
    token = request.cookies.get(REFRESH_COOKIE)
    payload = decode_token(token, "refresh") if token else None
    user = db.get(User, int(payload["sub"])) if payload else None
    if user:
        if everywhere:
            user.token_version += 1
        audit.record(db, "auth.logout", user=user, request=request, details={"everywhere": everywhere})
    clear_session_cookies(response)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return to_out(user)


@router.post("/change-password", response_model=UserOut)
def change_password(
    body: PasswordChangeIn,
    request: Request,
    response: Response,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    set_password(user, body.new_password)
    audit.record(db, "auth.password_changed", user=user, request=request)
    set_session_cookies(response, user)  # keep this session, others are signed out
    return to_out(user)


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(body: ForgotPasswordIn, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user and user.is_active and user.auth_provider == "local":
        send_reset_email(db, user)
        audit.record(db, "auth.password_reset_requested", user=user, request=request)
    # Same answer whether or not the account exists
    return {"detail": "If that account exists, a reset link has been sent."}


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(body: ResetPasswordIn, request: Request, db: Session = Depends(get_db)):
    row = db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_reset_token(body.token))
    )
    if not row or row.used_at or row.expires_at < datetime.now(UTC):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is invalid or has expired")
    user = db.get(User, row.user_id)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is invalid or has expired")
    set_password(user, body.new_password)
    row.used_at = datetime.now(UTC)
    audit.record(db, "auth.password_reset", user=user, request=request)
