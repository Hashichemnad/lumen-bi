"""Milestone 12: versions, column masking, comments, data checks, subscriptions, alerts, notifications and
embedding."""

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import JSONType, UTCDateTime, utcnow


class ItemVersion(Base):
    """An earlier state of a dashboard or chart, kept to roll back to. Only the last few are kept."""

    __tablename__ = "item_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # dashboard | chart
    item_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    query: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # charts
    dataset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # charts
    saved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ColumnMask(Base):
    """Hide a column's values (PII) from everyone except admins and the exempt groups.

    method: full (••••) | partial (keep the last 4 characters; emails keep the first letter and domain) |
    hash (a short stable code: rows can still be counted and matched, values cannot be read).
    """

    __tablename__ = "column_masks"
    __table_args__ = (UniqueConstraint("dataset_id", "column", name="uq_column_mask"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    column: Mapped[str] = mapped_column(String(200))
    method: Mapped[str] = mapped_column(String(10), default="full")
    exempt_groups: Mapped[list] = mapped_column(JSONType, default=list)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Comment(Base):
    """A comment on a dashboard or one of its tiles. With an anchor it is a chart annotation: a note pinned
    to a point of the chart, e.g. {"label": "Mar 2026"}."""

    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dashboard_id: Mapped[int] = mapped_column(ForeignKey("dashboards.id", ondelete="CASCADE"), index=True)
    tile_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("comments.id", ondelete="CASCADE"), nullable=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    anchor: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class DataCheck(Base):
    """A data quality or freshness check on a dataset, run after every sync (and freshness on a timer).

    kind / config:
      freshness  {"hours": 26, "column": "updated_at" | None}  data (or the newest date in a column) not older
      row_count  {"min": 1, "max": None}
      not_null   {"column": "email", "max_percent": 0}
      unique     {"column": "ticket_id"}
      range      {"column": "amount", "min": 0, "max": None}
      allowed    {"column": "status", "values": ["Open", "Closed"]}
    """

    __tablename__ = "data_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    severity: Mapped[str] = mapped_column(String(10), default="warning")  # warning | error
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_status: Mapped[str] = mapped_column(String(10), default="never")  # never | pass | fail | error
    last_value: Mapped[str] = mapped_column(String(200), default="")
    last_message: Mapped[str] = mapped_column(Text, default="")
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Subscription(Base):
    """A dashboard sent on a schedule. Each recipient gets it with their own row-level security.

    recipients: {"users": [ids], "groups": [ids]}; channels: ["email", "teams:<id>", "whatsapp"];
    format: pdf | png | link.
    """

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dashboard_id: Mapped[int] = mapped_column(ForeignKey("dashboards.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    recipients: Mapped[dict] = mapped_column(JSONType, default=dict)
    channels: Mapped[list] = mapped_column(JSONType, default=list)
    format: Mapped[str] = mapped_column(String(10), default="pdf")
    schedule: Mapped[str] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(Text, nullable=True)  # filters, as in a dashboard link (?s=)
    message: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(10), default="never")
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Alert(Base):
    """A data-driven alert on a dashboard tile (a value crosses a threshold) or a KPI section (its status
    gets worse). Checked as the owner, with the owner's row-level security, on a schedule and after syncs.
    Sends once when the condition becomes true; sends again only after it was false in between.

    condition: {"kind": "value", "op": "gt"|"gte"|"lt"|"lte", "value": 100}
             | {"kind": "kpi", "status": "off_track"|"at_risk"}  (at_risk = at risk or off track)
    """

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dashboard_id: Mapped[int] = mapped_column(ForeignKey("dashboards.id", ondelete="CASCADE"), index=True)
    tile_id: Mapped[str] = mapped_column(String(40))
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    condition: Mapped[dict] = mapped_column(JSONType, default=dict)
    recipients: Mapped[dict] = mapped_column(JSONType, default=dict)
    channels: Mapped[list] = mapped_column(JSONType, default=list)
    schedule: Mapped[str] = mapped_column(String(100), default="*/30 * * * *")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Each person is checked with their own data: the ids of those for whom it is currently true
    # (0 = the shared Teams message, checked as the owner)
    triggered_for: Mapped[list] = mapped_column(JSONType, default=list)
    last_value: Mapped[str] = mapped_column(String(200), default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class NotificationLog(Base):
    """Every message sent (or that failed): for the history shown on subscriptions, alerts and checks."""

    __tablename__ = "notification_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(20))  # subscription | alert | check | test
    source_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    channel: Mapped[str] = mapped_column(String(40))
    recipient: Mapped[str] = mapped_column(String(320), default="")
    status: Mapped[str] = mapped_column(String(10))  # sent | failed | skipped
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class EmbedKey(Base):
    """A key another application uses to sign embed links: a short-lived token naming one of the allowed
    dashboards and a Lumen user, whose row-level security applies."""

    __tablename__ = "embed_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    secret_encrypted: Mapped[str] = mapped_column(Text)
    dashboards: Mapped[list] = mapped_column(JSONType, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class DisplayLink(Base):
    """A link that opens one dashboard without signing in (TV screens), as a chosen user. Only a hash of
    the token is stored; links can be revoked and can expire."""

    __tablename__ = "display_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dashboard_id: Mapped[int] = mapped_column(ForeignKey("dashboards.id", ondelete="CASCADE"), index=True)
    run_as_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
