import re
from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.models import Role

AttributeValue = str | int | float | bool | list[str | int | float]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_email(value: str) -> str:
    """Lenient check that also accepts internal domains such as name@company.local."""
    value = value.strip().lower()
    if len(value) > 320 or not _EMAIL_RE.match(value):
        raise ValueError("Enter a valid email address")
    return value


Email = Annotated[str, AfterValidator(_normalize_email)]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- auth ----------
class LoginIn(BaseModel):
    email: Email
    password: str


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str


class ForgotPasswordIn(BaseModel):
    email: Email


class ResetPasswordIn(BaseModel):
    token: str
    new_password: str


# ---------- groups ----------
class GroupRef(ORM):
    id: int
    name: str


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class GroupOut(ORM):
    id: int
    name: str
    description: str
    source: str
    created_at: datetime
    member_count: int = 0


class GroupMembersIn(BaseModel):
    user_ids: list[int]


# ---------- users ----------
class UserOut(ORM):
    id: int
    email: str
    name: str
    role: Role
    attributes: dict[str, Any]
    auth_provider: str
    is_active: bool
    locked: bool = False
    last_login_at: datetime | None
    created_at: datetime
    groups: list[GroupRef] = []


class UserCreate(BaseModel):
    email: Email
    name: str = ""
    role: Role = Role.viewer
    password: str | None = None  # None -> SSO-only user, or set later via reset email
    attributes: dict[str, AttributeValue] = {}
    group_ids: list[int] = []
    send_invite: bool = False


class UserUpdate(BaseModel):
    name: str | None = None
    role: Role | None = None
    is_active: bool | None = None
    attributes: dict[str, AttributeValue] | None = None
    group_ids: list[int] | None = None
    password: str | None = None
    unlock: bool | None = None


class UserImportResult(BaseModel):
    created: int
    updated: int
    errors: list[str]


class Page[T](BaseModel):
    items: list[T]
    total: int


class AuditOut(ORM):
    id: int
    created_at: datetime
    user_id: int | None
    user_email: str
    action: str
    object_type: str
    object_id: str
    ip: str
    details: dict[str, Any]
