from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.common import JSONType, UTCDateTime, utcnow


class Connection(Base):
    """A source system: a database, an uploaded file, (later) Salesforce or a REST API."""

    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(30))  # oracle | postgres | file | ...
    config: Mapped[dict] = mapped_column(JSONType, default=dict)  # non-secret settings
    secrets_enc: Mapped[str] = mapped_column(Text, default="")  # encrypted JSON of secret fields
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    datasets: Mapped[list["Dataset"]] = relationship(
        back_populates="connection", cascade="all, delete-orphan", passive_deletes=True
    )

    def __init__(self, **kwargs):
        kwargs.setdefault("config", {})
        kwargs.setdefault("secrets_enc", "")
        super().__init__(**kwargs)


class Dataset(Base):
    """What to pull from a connection, how and when. Data lives in Parquet snapshots, not here."""

    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    connection_id: Mapped[int] = mapped_column(ForeignKey("connections.id", ondelete="CASCADE"), index=True)
    source: Mapped[dict] = mapped_column(
        JSONType, default=dict
    )  # {"object": "SCHEMA.TABLE"} or {"sql": "..."}
    sync_mode: Mapped[str] = mapped_column(String(20), default="full")  # full | incremental
    incremental_column: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_key: Mapped[list] = mapped_column(JSONType, default=list)
    schedule: Mapped[str | None] = mapped_column(String(100), nullable=True)  # cron, in settings.timezone
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    current_version: Mapped[int] = mapped_column(Integer, default=0)  # 0 = never synced
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    columns: Mapped[list] = mapped_column(JSONType, default=list)  # [{"name", "type"}]
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(20), default="never")  # never | success | failed
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    connection: Mapped[Connection] = relationship(back_populates="datasets")

    def __init__(self, **kwargs):
        for key, value in {
            "description": "",
            "source": {},
            "sync_mode": "full",
            "primary_key": [],
            "current_version": 0,
            "row_count": 0,
            "columns": [],
            "last_status": "never",
        }.items():
            kwargs.setdefault(key, value)
        super().__init__(**kwargs)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # manual | schedule | upload
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued | running | success | failed
    mode: Mapped[str] = mapped_column(String(20), default="full")
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)  # snapshot written, if any
    rows_read: Mapped[int] = mapped_column(Integer, default=0)
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict] = mapped_column(JSONType, default=dict)  # e.g. {"schema_changes": {...}}
    requested_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    def __init__(self, **kwargs):
        for key, value in {
            "trigger": "manual",
            "status": "queued",
            "mode": "full",
            "rows_read": 0,
            "rows_total": 0,
            "details": {},
        }.items():
            kwargs.setdefault(key, value)
        super().__init__(**kwargs)
