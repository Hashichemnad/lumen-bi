"""The connector interface. Adding a source system = one subclass of Connector, registered in __init__.py."""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

import pyarrow as pa


class ConnectorError(Exception):
    """A problem with the source system whose message is safe to show to users."""


@dataclass
class ObjectInfo:
    name: str  # what goes into a dataset's source {"object": name}
    kind: str  # table | view | sheet | file | object | endpoint
    schema: str | None = None
    label: str | None = None  # friendly name, e.g. Salesforce "Opportunity Product"

    def dict(self) -> dict:
        return asdict(self)


@dataclass
class ColumnInfo:
    name: str
    type: str  # string | integer | number | boolean | date | datetime | other

    def dict(self) -> dict:
        return asdict(self)


def lumen_type(t: pa.DataType) -> str:
    if pa.types.is_boolean(t):
        return "boolean"
    if pa.types.is_integer(t):
        return "integer"
    if pa.types.is_floating(t) or pa.types.is_decimal(t):
        return "number"
    if pa.types.is_timestamp(t):
        return "datetime"
    if pa.types.is_date(t):
        return "date"
    if pa.types.is_string(t) or pa.types.is_large_string(t) or pa.types.is_null(t):
        return "string"
    return "other"


def unique_column_names(names: list[Any]) -> list[str]:
    """Blank headers become column_N; duplicates get _2, _3 suffixes."""
    out: list[str] = []
    seen: dict[str, int] = {}
    for i, raw in enumerate(names, start=1):
        name = str(raw).strip() if raw is not None and str(raw).strip() else f"column_{i}"
        base, n = name, seen.get(name.lower(), 0)
        while name.lower() in seen:
            n += 1
            name = f"{base}_{n + 1}"
        seen[base.lower()] = n
        seen[name.lower()] = 0
        out.append(name)
    return out


# A connector may add this boolean column to mark rows deleted at the source (e.g. Salesforce IsDeleted).
# The snapshot writer removes those rows (and their older versions) and drops the column.
DELETED_COLUMN = "_lumen_deleted"


class Connector(ABC):
    type: ClassVar[str]
    label: ClassVar[str]
    supports_incremental: ClassVar[bool] = False
    supports_sql: ClassVar[bool] = False
    secret_fields: ClassVar[tuple[str, ...]] = ()

    def __init__(self, config: dict[str, Any], secrets: dict[str, str]):
        self.config = config or {}
        self.secrets = secrets or {}

    @classmethod
    def incremental_ready(cls, config: dict[str, Any]) -> str | None:
        """None if incremental sync can be used with this configuration, else the reason it cannot."""
        return None if cls.supports_incremental else "This connection only supports full syncs"

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        """Check and normalise settings before saving. Raise ConnectorError with a readable message."""
        return config

    @abstractmethod
    def test_connection(self) -> None:
        """Raise ConnectorError if the source cannot be reached with these settings."""

    @abstractmethod
    def list_objects(self) -> list[ObjectInfo]: ...

    @abstractmethod
    def extract(
        self,
        source: dict[str, Any],
        since: Any = None,
        incremental_column: str | None = None,
        batch_size: int = 50_000,
        inclusive: bool = False,
    ) -> Iterator[pa.RecordBatch]:
        """Stream rows as Arrow record batches. Always yields at least one (possibly empty) batch, so the
        column names are known even for an empty table. With `since`, only rows where
        incremental_column > since (>= when inclusive, used when a primary key removes the overlap)."""

    def get_schema(self, source: dict[str, Any]) -> list[ColumnInfo]:
        batch = next(iter(self.extract(source, batch_size=100)))
        return [ColumnInfo(f.name, lumen_type(f.type)) for f in batch.schema]
