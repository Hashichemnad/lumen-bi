from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password, new_reset_token, password_problems
from app.models import Group, PasswordResetToken, Role, User
from app.schemas import IdentityOut, UserOut
from app.services.email import send_email


def is_locked(user: User) -> bool:
    return bool(user.locked_until and user.locked_until > datetime.now(UTC))


def to_out(user: User) -> UserOut:
    out = UserOut.model_validate(user, from_attributes=True)
    out.locked = is_locked(user)
    out.sign_in_methods = [
        IdentityOut(
            provider_id=i.provider_id,
            provider_name=i.provider.name,
            provider_type=i.provider.type,
            email=i.email,
            last_login_at=i.last_login_at,
        )
        for i in user.identities
    ]
    return out


def validate_password(password: str) -> None:
    problems = password_problems(password)
    if problems:
        raise HTTPException(422, "Password must contain " + ", ".join(problems))


def set_password(user: User, password: str) -> None:
    validate_password(password)
    user.password_hash = hash_password(password)
    user.token_version += 1  # sign out other sessions
    user.failed_logins = 0
    user.locked_until = None


def load_groups(db: Session, group_ids: list[int]) -> list[Group]:
    if not group_ids:
        return []
    groups = list(db.scalars(select(Group).where(Group.id.in_(group_ids))))
    missing = set(group_ids) - {g.id for g in groups}
    if missing:
        raise HTTPException(422, f"Unknown group ids: {sorted(missing)}")
    return groups


def admin_count(db: Session) -> int:
    return db.scalar(
        select(func.count()).select_from(User).where(User.role == Role.admin.value, User.is_active.is_(True))
    )


def ensure_not_last_admin(db: Session, user: User) -> None:
    if user.role == Role.admin.value and user.is_active and admin_count(db) <= 1:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This is the last active admin; add another admin first"
        )


def send_reset_email(db: Session, user: User, *, invite: bool = False) -> None:
    s = get_settings()
    token, token_hash = new_reset_token()
    minutes = s.password_reset_minutes * (24 if invite else 1)
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=datetime.now(UTC) + timedelta(minutes=minutes),
        )
    )
    db.commit()
    link = f"{s.public_url.rstrip('/')}/reset-password?token={token}"
    if invite:
        subject = f"You have been invited to {s.app_name}"
        body = f"Hello {user.name or user.email},\n\nSet your password here (valid 24 hours):\n{link}\n"
    else:
        subject = f"Reset your {s.app_name} password"
        body = (
            f"Hello {user.name or user.email},\n\nReset your password here (valid {minutes} minutes):\n"
            f"{link}\n\nIf you did not ask for this, ignore this email."
        )
    send_email(user.email, subject, body)
