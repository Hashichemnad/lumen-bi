from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import UTCDateTime, utcnow


class Workspace(Base):
    """A shared place for a team's dashboards and charts. Members (users or groups) are viewers, editors or
    managers of everything in it; admins manage every workspace. Items not in a workspace are personal."""

    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    def __init__(self, **kwargs):
        kwargs.setdefault("description", "")
        super().__init__(**kwargs)


class WorkspaceMember(Base):
    """principal: user | group; role: viewer | editor | manager."""

    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "principal", "principal_id", name="uq_workspace_member_principal"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    principal: Mapped[str] = mapped_column(String(20))
    principal_id: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(10), default="viewer")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
