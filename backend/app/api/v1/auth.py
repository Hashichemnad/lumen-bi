from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import ACCESS_COOKIE, REFRESH_COOKIE, current_user
from app.core.config import get_settings
from app.core.ratelimit import limit
from app.core.security import (
    create_access_token,
    create_refresh_token,
    create_token,
    decode_token,
    hash_password,
    hash_reset_token,
    verify_password,
)
from app.db import get_db
from app.models import IdentityProvider, PasswordResetToken, User
from app.schemas import ForgotPasswordIn, LoginIn, LogoutOut, PasswordChangeIn, ResetPasswordIn, UserOut
from app.services import audit, twofactor
from app.services.sso import SSOError, adapter_for
from app.services.users import is_locked, send_reset_email, set_password, to_out

router = APIRouter(prefix="/auth", tags=["auth"])

# Verifying against a dummy hash for unknown emails keeps response time equal (no account enumeration)
_DUMMY_HASH = hash_password("dummy-password-for-timing")
BAD_LOGIN = "Incorrect email or password"


def set_session_cookies(response: Response, user: User, idp: int | None = None) -> None:
    """idp = id of the SSO provider used to sign in (None for password), so logout can end that session."""
    s = get_settings()
    common = {"httponly": True, "secure": s.secure_cookies, "samesite": "lax"}
    response.set_cookie(
        ACCESS_COOKIE,
        create_access_token(user.id, user.token_version, idp),
        max_age=s.access_token_minutes * 60,
        path="/api",
        **common,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        create_refresh_token(user.id, user.token_version, idp),
        max_age=s.refresh_token_days * 86400,
        path="/api/v1/auth",
        **common,
    )


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/api")
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")


def _login_limit():
    return limit("login", get_settings().login_per_minute, 60)


def _ticket(user: User, step: str) -> str:
    """A 5-minute proof that the password was right, for the second step (code or setting up the app)."""
    return create_token(user.id, "mfa", timedelta(minutes=5), user.token_version, {"step": step})


def _from_ticket(db: Session, ticket: str | None) -> User:
    payload = decode_token(ticket, "mfa") if ticket else None
    user = db.get(User, int(payload["sub"])) if payload else None
    if not user or not user.is_active or payload.get("ver") != user.token_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in again: this step took too long")
    if is_locked(user):
        raise HTTPException(
            status.HTTP_423_LOCKED, "Too many failed attempts. Try again later or reset your password."
        )
    return user


def _finish_login(db: Session, request: Request, response: Response, user: User, method: str) -> UserOut:
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = datetime.now(UTC)
    audit.record(db, "auth.login", user=user, request=request, details={"method": method})
    set_session_cookies(response, user)
    return to_out(user)


def _failed(db: Session, user: User) -> None:
    s = get_settings()
    user.failed_logins += 1
    if user.failed_logins >= s.max_failed_logins:
        user.locked_until = datetime.now(UTC) + timedelta(minutes=s.lockout_minutes)
        user.failed_logins = 0


@router.post("/login", response_model=None, dependencies=[Depends(_login_limit())])
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """Signs in, or (two-factor) returns {"two_factor": "code" | "setup", "ticket": ...} for the next step."""
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
            _failed(db, user)
        audit.record(db, "auth.login_failed", user=user, request=request)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)

    if user.totp_enabled:
        db.commit()
        return {"two_factor": "code", "ticket": _ticket(user, "code")}
    if twofactor.required_for(db, user):
        db.commit()
        return {"two_factor": "setup", "ticket": _ticket(user, "setup")}
    return _finish_login(db, request, response, user, "password")


class TicketIn(BaseModel):
    ticket: str | None = None  # from /login; without it the signed-in user is used
    code: str = Field("", max_length=20)


@router.post("/login/two-factor", response_model=UserOut, dependencies=[Depends(_login_limit())])
def login_two_factor(body: TicketIn, request: Request, response: Response, db: Session = Depends(get_db)):
    user = _from_ticket(db, body.ticket)
    how = twofactor.verify(user, body.code)
    if how is None:
        _failed(db, user)
        audit.record(db, "auth.two_factor_failed", user=user, request=request)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That code is not right. Try the newest one.")
    if how == "recovery":
        audit.record(db, "auth.recovery_code_used", user=user, request=request, commit=False,
                     details={"left": len(user.recovery_codes or [])})  # fmt: skip
    return _finish_login(db, request, response, user, f"password+{how}")


def _setup_user(db: Session, request: Request, ticket: str | None) -> User:
    if ticket:
        return _from_ticket(db, ticket)
    return current_user(request, db)


@router.post("/two-factor/setup", dependencies=[Depends(_login_limit())])
def two_factor_setup(body: TicketIn, request: Request, db: Session = Depends(get_db)):
    """Starts setting up an authenticator app: the secret and a QR code to scan."""
    user = _setup_user(db, request, body.ticket)
    if user.auth_provider != "local" and not user.password_hash:
        raise HTTPException(400, "You sign in with single sign-on; your organisation's sign-in handles this")
    if body.ticket and user.totp_enabled:
        raise HTTPException(400, "Two-factor sign-in is already on")
    out = twofactor.start(user)
    db.commit()
    return out


@router.post("/two-factor/enable", dependencies=[Depends(_login_limit())])
def two_factor_enable(body: TicketIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """Confirms the app with a code. Returns one-time recovery codes (shown once). With a ticket, also
    finishes signing in."""
    user = _setup_user(db, request, body.ticket)
    codes = twofactor.confirm(user, body.code)
    if codes is None:
        _failed(db, user)
        db.commit()
        raise HTTPException(400, "That code is not right. Check the time on your phone and try again.")
    audit.record(db, "auth.two_factor_on", user=user, request=request, commit=False)
    if body.ticket:
        out = _finish_login(db, request, response, user, "password+totp")
    else:
        db.commit()
        out = to_out(user)
    return {"user": out, "recovery_codes": codes}


class PasswordIn(BaseModel):
    password: str = ""
    code: str = ""


@router.post("/two-factor/disable", response_model=UserOut)
def two_factor_disable(
    body: PasswordIn, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    if twofactor.required_for(db, user):
        raise HTTPException(403, "Your organisation requires two-factor sign-in")
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    twofactor.turn_off(user)
    audit.record(db, "auth.two_factor_off", user=user, request=request)
    return to_out(user)


@router.post("/two-factor/recovery-codes")
def new_recovery_codes(
    body: PasswordIn, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    if not user.totp_enabled:
        raise HTTPException(400, "Two-factor sign-in is not on")
    if twofactor.verify(user, body.code) != "totp":
        db.commit()
        raise HTTPException(400, "That code is not right. Try the newest one.")
    codes = twofactor.new_recovery_codes(user)
    audit.record(db, "auth.recovery_codes_new", user=user, request=request)
    return {"recovery_codes": codes}


@router.post("/refresh", response_model=UserOut)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(REFRESH_COOKIE)
    payload = decode_token(token, "refresh") if token else None
    user = db.get(User, int(payload["sub"])) if payload else None
    if not user or not user.is_active or payload.get("ver") != user.token_version:
        clear_session_cookies(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    set_session_cookies(response, user, payload.get("idp"))
    return to_out(user)


@router.post("/logout", response_model=LogoutOut)
def logout(request: Request, response: Response, everywhere: bool = False, db: Session = Depends(get_db)):
    """Ends the session. If the user signed in with SSO, returns the provider's logout URL to visit next."""
    token = request.cookies.get(REFRESH_COOKIE)
    payload = decode_token(token, "refresh") if token else None
    user = db.get(User, int(payload["sub"])) if payload else None
    logout_url = None
    if user:
        if everywhere:
            user.token_version += 1
        audit.record(db, "auth.logout", user=user, request=request, details={"everywhere": everywhere})
        provider = db.get(IdentityProvider, payload["idp"]) if payload.get("idp") else None
        if provider and provider.enabled:
            try:
                logout_url = adapter_for(provider).logout_url(
                    f"{get_settings().public_url.rstrip('/')}/login"
                )
            except SSOError:
                logout_url = None
    clear_session_cookies(response)
    return LogoutOut(logout_url=logout_url)


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


@router.post(
    "/forgot-password", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(limit("reset", 5, 60))]
)
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
