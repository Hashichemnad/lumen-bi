from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import JSONType, UTCDateTime, utcnow


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_email: Mapped[str] = mapped_column(String(320), default="")  # kept even if the user is deleted
    action: Mapped[str] = mapped_column(String(100), index=True)  # e.g. auth.login, user.update
    object_type: Mapped[str] = mapped_column(String(50), default="")
    object_id: Mapped[str] = mapped_column(String(100), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    details: Mapped[dict] = mapped_column(JSONType, default=dict)
