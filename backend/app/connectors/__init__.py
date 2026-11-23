from app.connectors.base import ColumnInfo, Connector, ConnectorError, ObjectInfo
from app.connectors.files import FileConnector
from app.connectors.oracle import OracleConnector
from app.connectors.postgres import PostgresConnector

# Salesforce and REST connectors are added in milestone 5
REGISTRY: dict[str, type[Connector]] = {
    c.type: c for c in (FileConnector, OracleConnector, PostgresConnector)
}


def connector_class(type_: str) -> type[Connector]:
    try:
        return REGISTRY[type_]
    except KeyError as exc:
        raise ConnectorError(f"Unknown connection type: {type_}") from exc


__all__ = ["REGISTRY", "ColumnInfo", "Connector", "ConnectorError", "ObjectInfo", "connector_class"]
