from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import JSONType, UTCDateTime, utcnow


class Chart(Base):
    """A saved chart (a "sheet" in Tableau terms).

    config: the builder state the UI needs to reopen it (shelves, pills, chart type, colours, options).
    query:  the query spec it runs (without "dataset"), so the server can run it on its own later for
            dashboards, email reports and alerts. Row-level security applies to whoever views it.
    """

    __tablename__ = "charts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    query: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    def __init__(self, **kwargs):
        kwargs.setdefault("description", "")
        kwargs.setdefault("config", {})
        kwargs.setdefault("query", {})
        super().__init__(**kwargs)
