from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import JSONType, UTCDateTime, utcnow


class MapBoundary(Base):
    """An uploaded boundary file (GeoJSON) for Geo Map charts: emirates, districts, sales territories.

    The file is stored at DATA_DIR/boundaries/<id>.geojson; name_property is the feature property that
    holds each area's name (matched against a dimension's values).
    """

    __tablename__ = "map_boundaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    name_property: Mapped[str] = mapped_column(String(100))
    properties: Mapped[list] = mapped_column(JSONType, default=list)  # property names found in the file
    feature_count: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    def __init__(self, **kwargs):
        kwargs.setdefault("properties", [])
        kwargs.setdefault("feature_count", 0)
        kwargs.setdefault("size_bytes", 0)
        super().__init__(**kwargs)
