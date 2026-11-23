from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import JSONType, UTCDateTime, utcnow


class Dashboard(Base):
    """A dashboard: tabs of tiles (charts, text, images, web pages) on a grid, with filters for all of them.

    config: {"tabs": [{"id", "name", "items": [{"id", "kind": "chart"|"text"|"image"|"web"|"section",
             "chart_id", "code",
             "title", "text", "url", "alt", "desktop": {"x","y","w","h"}, "phone": {...} | null}]}],
             "filters": [{"id", "label", "kind", "control", "targets": [{"dataset", "field"}], "value",
                          "grain"}],
             "options": {"cross_filter": true, "view_data": true, "phone_layout": false}}
    Viewers query a dashboard's charts through the dashboard (with their own row-level security); they do not
    need access to the datasets themselves.
    """

    __tablename__ = "dashboards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    # None = personal (only the owner and people it is shared with)
    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), index=True, nullable=True
    )
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    def __init__(self, **kwargs):
        kwargs.setdefault("description", "")
        kwargs.setdefault("config", {})
        super().__init__(**kwargs)


class DashboardShare(Base):
    """principal: everyone | user | group; level: view | edit."""

    __tablename__ = "dashboard_shares"
    __table_args__ = (
        UniqueConstraint("dashboard_id", "principal", "principal_id", name="uq_dashboard_share_principal"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dashboard_id: Mapped[int] = mapped_column(ForeignKey("dashboards.id", ondelete="CASCADE"), index=True)
    principal: Mapped[str] = mapped_column(String(20))
    principal_id: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[str] = mapped_column(String(10), default="view")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class DashboardFavorite(Base):
    __tablename__ = "dashboard_favorites"
    __table_args__ = (UniqueConstraint("user_id", "dashboard_id", name="uq_dashboard_favorite"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    dashboard_id: Mapped[int] = mapped_column(ForeignKey("dashboards.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
