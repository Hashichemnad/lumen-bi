"""Oracle Database via python-oracledb in thin mode (no Oracle Client installation needed)."""

from typing import Any

from app.connectors.base import ConnectorError
from app.connectors.sql import IDENT, SQLConnector


class OracleConnector(SQLConnector):
    type = "oracle"
    label = "Oracle Database"
    param_style = "named"

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        host = str(config.get("host", "")).strip()
        user = str(config.get("user", "")).strip()
        service = str(config.get("service_name", "")).strip()
        sid = str(config.get("sid", "")).strip()
        if not host or not user:
            raise ConnectorError("Host and username are required.")
        if bool(service) == bool(sid):
            raise ConnectorError("Enter either a service name or a SID (not both).")
        try:
            port = int(config.get("port") or 1521)
        except (TypeError, ValueError) as exc:
            raise ConnectorError("Port must be a number.") from exc
        schemas = config.get("schemas") or []
        if isinstance(schemas, str):
            schemas = [s for s in (x.strip() for x in schemas.split(",")) if s]
        schemas = [s.upper() for s in schemas]
        for s in schemas:
            if not IDENT.match(s):
                raise ConnectorError(f"Invalid schema name: {s}")
        return {
            "host": host,
            "port": port,
            "service_name": service,
            "sid": sid,
            "user": user,
            "schemas": schemas,
        }

    def connect(self):
        import oracledb

        # Return numbers as Python numbers and LOBs as plain text/bytes
        oracledb.defaults.fetch_lobs = False
        oracledb.defaults.fetch_decimals = False
        c = self.config
        if c.get("service_name"):
            dsn = oracledb.makedsn(c["host"], int(c.get("port") or 1521), service_name=c["service_name"])
        else:
            dsn = oracledb.makedsn(c["host"], int(c.get("port") or 1521), sid=c["sid"])
        return oracledb.connect(
            user=c["user"], password=self.secrets.get("password", ""), dsn=dsn, tcp_connect_timeout=15
        )

    def connect_error(self, exc: Exception) -> ConnectorError:
        text = str(exc)
        if "ORA-01017" in text:
            return ConnectorError("Oracle rejected the username or password (ORA-01017).")
        if "DPY-6005" in text or "timed out" in text.lower():
            return ConnectorError("Could not reach the Oracle server. Check host, port and firewall.")
        if "ORA-12514" in text or "DPY-6001" in text:
            return ConnectorError("The Oracle service name was not found on that server (ORA-12514).")
        if "ORA-12505" in text or "DPY-6003" in text:
            return ConnectorError("The Oracle SID was not found on that server (ORA-12505).")
        return ConnectorError(f"Could not connect to Oracle: {text.splitlines()[0]}")

    def ping_sql(self) -> str:
        return "SELECT 1 FROM dual"

    def objects_query(self) -> tuple[str, dict]:
        schemas = self.config.get("schemas") or []
        if not schemas:
            return (
                "SELECT NULL, table_name, 'TABLE' FROM user_tables "
                "UNION ALL SELECT NULL, view_name, 'VIEW' FROM user_views ORDER BY 2",
                {},
            )
        binds = {f"s{i}": s for i, s in enumerate(schemas)}
        placeholders = ", ".join(f":{k}" for k in binds)
        return (
            f"SELECT owner, table_name, 'TABLE' FROM all_tables WHERE owner IN ({placeholders}) "
            f"UNION ALL SELECT owner, view_name, 'VIEW' FROM all_views WHERE owner IN ({placeholders}) "
            "ORDER BY 1, 2",
            binds,
        )
