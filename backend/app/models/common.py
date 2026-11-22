from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Timezone-aware datetimes everywhere (SQLite drops tzinfo; this restores it as UTC)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value


# JSONB on PostgreSQL, plain JSON elsewhere (SQLite in tests)
JSONType = JSON().with_variant(JSONB(), "postgresql")
