"""PostgreSQL via psycopg 3."""

from typing import Any

from app.connectors.base import ConnectorError
from app.connectors.sql import IDENT, SQLConnector

SSL_MODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}


class PostgresConnector(SQLConnector):
    type = "postgres"
    label = "PostgreSQL"
    param_style = "pyformat"

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        host = str(config.get("host", "")).strip()
        database = str(config.get("database", "")).strip()
        user = str(config.get("user", "")).strip()
        if not host or not database or not user:
            raise ConnectorError("Host, database and username are required.")
        try:
            port = int(config.get("port") or 5432)
        except (TypeError, ValueError) as exc:
            raise ConnectorError("Port must be a number.") from exc
        sslmode = str(config.get("sslmode") or "prefer")
        if sslmode not in SSL_MODES:
            raise ConnectorError(f"SSL mode must be one of: {', '.join(sorted(SSL_MODES))}")
        schemas = config.get("schemas") or []
        if isinstance(schemas, str):
            schemas = [s for s in (x.strip() for x in schemas.split(",")) if s]
        for s in schemas:
            if not IDENT.match(s):
                raise ConnectorError(f"Invalid schema name: {s}")
        return {
            "host": host,
            "port": port,
            "database": database,
            "user": user,
            "sslmode": sslmode,
            "schemas": schemas,
        }

    def connect(self):
        import psycopg

        c = self.config
        return psycopg.connect(
            host=c["host"],
            port=int(c.get("port") or 5432),
            dbname=c["database"],
            user=c["user"],
            password=self.secrets.get("password", ""),
            sslmode=c.get("sslmode") or "prefer",
            connect_timeout=15,
        )

    def connect_error(self, exc: Exception) -> ConnectorError:
        text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        if "password authentication failed" in text:
            return ConnectorError("PostgreSQL rejected the username or password.")
        return ConnectorError(f"Could not connect to PostgreSQL: {text}")

    def objects_query(self) -> tuple[str, dict]:
        schemas = self.config.get("schemas") or []
        sql = (
            "SELECT table_schema, table_name, CASE table_type WHEN 'VIEW' THEN 'view' ELSE 'table' END "
            "FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
        )
        if schemas:
            sql += "AND table_schema = ANY(%(schemas)s) "
        return sql + "ORDER BY 1, 2", {"schemas": schemas} if schemas else {}
