"""Shared logic for relational databases (Oracle, PostgreSQL, and later SQL Server etc.)."""

import re
from abc import abstractmethod
from collections.abc import Iterator
from decimal import Decimal
from typing import Any, ClassVar

import pyarrow as pa

from app.connectors.base import Connector, ConnectorError, ObjectInfo, unique_column_names

# Plain identifiers only: letters, digits, _, $, #. Anything else is rejected rather than escaped.
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]{0,127}$")
SQL_COMMENT = re.compile(r"(--[^\n]*)|(/\*.*?\*/)", re.S)


def check_select(sql: str) -> str:
    """Custom SQL must be a single read-only query (SELECT or WITH)."""
    stripped = SQL_COMMENT.sub(" ", sql).strip().rstrip(";").strip()
    if not stripped:
        raise ConnectorError("The SQL query is empty.")
    if ";" in stripped:
        raise ConnectorError("Use a single query (no semicolons).")
    if not re.match(r"^(select|with)\b", stripped, re.I):
        raise ConnectorError("Only SELECT queries are allowed.")
    return stripped


def _clean(value: Any) -> Any:
    # Decimals with no fraction become ints, others floats, so Arrow gets consistent numeric types
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, memoryview | bytearray):
        return bytes(value)
    return value


class SQLConnector(Connector):
    supports_incremental = True
    supports_sql = True
    secret_fields = ("password",)
    param_style: ClassVar[str] = "named"  # named -> :since, pyformat -> %(since)s
    quote_char: ClassVar[str] = '"'

    @abstractmethod
    def connect(self):
        """Return an open DB-API connection."""

    @abstractmethod
    def objects_query(self) -> tuple[str, dict]:
        """SQL returning rows of (schema, name, kind) for tables and views the user can read."""

    def connect_error(self, exc: Exception) -> ConnectorError:
        return ConnectorError(f"Could not connect: {exc}")

    # ---- helpers
    def quote(self, ident: str) -> str:
        if not IDENT.match(ident):
            raise ConnectorError(f"Invalid name: {ident!r}")
        return f"{self.quote_char}{ident}{self.quote_char}"

    def qualified(self, name: str) -> str:
        parts = name.split(".")
        if len(parts) > 2:
            raise ConnectorError(f"Invalid table name: {name!r}")
        return ".".join(self.quote(p) for p in parts)

    def bind(self, name: str) -> str:
        return f":{name}" if self.param_style == "named" else f"%({name})s"

    def source_sql(self, source: dict[str, Any]) -> str:
        if source.get("sql"):
            return f"SELECT * FROM ({check_select(source['sql'])}) lumen_src"
        if source.get("object"):
            return f"SELECT * FROM {self.qualified(source['object'])}"
        raise ConnectorError("Choose a table or enter a SQL query.")

    def _open(self):
        try:
            return self.connect()
        except ConnectorError:
            raise
        except Exception as exc:  # driver-specific errors
            raise self.connect_error(exc) from exc

    # ---- interface
    def test_connection(self) -> None:
        conn = self._open()
        try:
            cur = conn.cursor()
            cur.execute(self.ping_sql())
            cur.fetchall()
        finally:
            conn.close()

    def ping_sql(self) -> str:
        return "SELECT 1"

    def list_objects(self) -> list[ObjectInfo]:
        sql, params = self.objects_query()
        conn = self._open()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        finally:
            conn.close()
        return [
            ObjectInfo(name=f"{schema}.{name}" if schema else name, kind=str(kind).lower(), schema=schema)
            for schema, name, kind in rows
        ]

    def extract(
        self,
        source: dict[str, Any],
        since: Any = None,
        incremental_column: str | None = None,
        batch_size: int = 50_000,
        inclusive: bool = False,
    ) -> Iterator[pa.RecordBatch]:
        sql = self.source_sql(source)
        params: dict[str, Any] = {}
        if since is not None and incremental_column:
            op = ">=" if inclusive else ">"
            col = self.quote(incremental_column)
            sql = f"SELECT * FROM ({sql}) lumen_inc WHERE {col} {op} {self.bind('since')}"
            params["since"] = since
        conn = self._open()
        try:
            cur = conn.cursor()
            if hasattr(cur, "arraysize"):
                cur.arraysize = min(batch_size, 10_000)
            try:
                cur.execute(sql, params)
            except Exception as exc:
                raise ConnectorError(f"The query failed: {exc}") from exc
            names = unique_column_names([d[0] for d in cur.description])
            yielded = False
            while True:
                rows = cur.fetchmany(batch_size)
                if not rows:
                    break
                columns = list(zip(*rows, strict=True))
                arrays = []
                for col in columns:
                    values = [_clean(v) for v in col]
                    try:
                        arrays.append(pa.array(values))
                    except (pa.ArrowInvalid, pa.ArrowTypeError):
                        arrays.append(pa.array([None if v is None else str(v) for v in values]))
                yield pa.RecordBatch.from_arrays(arrays, names=names)
                yielded = True
            if not yielded:
                yield pa.RecordBatch.from_arrays([pa.array([], pa.string()) for _ in names], names=names)
        finally:
            conn.close()
