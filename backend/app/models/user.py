from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Table, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.common import JSONType, UTCDateTime, utcnow


class Role(StrEnum):
    """Ordered from least to most privileged."""

    viewer = "viewer"  # view dashboards shared with them
    explorer = "explorer"  # + edit copies, ask AI, export
    creator = "creator"  # + connections, datasets, dashboards
    admin = "admin"  # everything, including users and settings

    @property
    def level(self) -> int:
        return list(Role).index(self)


user_groups = Table(
    "user_groups",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("group_id", ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("auth_provider", "external_id", name="uq_users_provider_external"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default=Role.viewer.value)
    # Attributes drive row-level security, e.g. {"region": ["EMEA"], "branch": "Dubai"}
    attributes: Mapped[dict] = mapped_column(JSONType, default=dict)
    # Personal choices: {"theme": "classic"|"advanced"|"analyst", "language": "en"|"ar",
    # "calendar": "gregorian"|"hijri"|"both"}; missing keys use the organisation's defaults
    preferences: Mapped[dict] = mapped_column(JSONType, default=dict)
    # local | entra | uaepass
    auth_provider: Mapped[str] = mapped_column(String(20), default="local")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Incremented to invalidate every existing session of this user
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    groups: Mapped[list["Group"]] = relationship(
        secondary=user_groups, back_populates="members", order_by="Group.name"
    )
    identities: Mapped[list["UserIdentity"]] = relationship(  # noqa: F821
        cascade="all, delete-orphan", order_by="UserIdentity.id"
    )

    def __init__(self, **kwargs):
        # Apply defaults in Python too, so new objects are usable before they are flushed
        kwargs.setdefault("role", Role.viewer.value)
        kwargs.setdefault("attributes", {})
        kwargs.setdefault("preferences", {})
        kwargs.setdefault("auth_provider", "local")
        kwargs.setdefault("is_active", True)
        kwargs.setdefault("failed_logins", 0)
        kwargs.setdefault("token_version", 0)
        super().__init__(**kwargs)

    @property
    def role_enum(self) -> Role:
        return Role(self.role)

    def has_role(self, minimum: Role) -> bool:
        return self.role_enum.level >= minimum.level


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    # local | entra (synced from Microsoft groups)
    source: Mapped[str] = mapped_column(String(20), default="local")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    members: Mapped[list[User]] = relationship(
        secondary=user_groups, back_populates="groups", order_by="User.name"
    )


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
