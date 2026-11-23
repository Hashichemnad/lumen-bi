"""Connector unit tests. Oracle's driver is replaced by SQLite so its SQL and batching logic still run."""

import sqlite3

import pyarrow as pa
import pytest

from app.connectors import ConnectorError, connector_class
from app.connectors.base import lumen_type, unique_column_names
from app.connectors.oracle import OracleConnector
from app.connectors.sql import check_select


def test_unique_column_names():
    assert unique_column_names(["a", "A", "", None, "a", " b "]) == [
        "a",
        "A_2",
        "column_3",
        "column_4",
        "a_3",
        "b",
    ]


@pytest.mark.parametrize(
    "sql", ["select * from t", "  WITH x AS (select 1) select * from x;", "-- note\nselect 1"]
)
def test_check_select_allows_queries(sql):
    assert check_select(sql)


@pytest.mark.parametrize(
    "sql",
    ["delete from t", "select 1; drop table t", "", "/* x */ update t set a=1", "insert into t values (1)"],
)
def test_check_select_blocks_others(sql):
    with pytest.raises(ConnectorError):
        check_select(sql)


def test_lumen_types():
    assert lumen_type(pa.int64()) == "integer"
    assert lumen_type(pa.decimal128(10, 2)) == "number"
    assert lumen_type(pa.timestamp("us")) == "datetime"
    assert lumen_type(pa.date32()) == "date"
    assert lumen_type(pa.string()) == "string"


# ------------------------------------------------------------------ Oracle
def test_oracle_config_validation():
    cfg = OracleConnector.validate_config(
        {"host": " db.local ", "user": "bi", "service_name": "ORCLPDB1", "schemas": "sales, hr"}
    )
    assert cfg == {
        "host": "db.local",
        "port": 1521,
        "service_name": "ORCLPDB1",
        "sid": "",
        "user": "bi",
        "schemas": ["SALES", "HR"],
    }
    for bad in [
        {"host": "h", "user": "u"},  # neither service nor sid
        {"host": "h", "user": "u", "service_name": "a", "sid": "b"},
        {"user": "u", "sid": "X"},
        {"host": "h", "user": "u", "sid": "X", "port": "abc"},
        {"host": "h", "user": "u", "sid": "X", "schemas": ["bad name"]},
    ]:
        with pytest.raises(ConnectorError):
            OracleConnector.validate_config(bad)


def test_oracle_objects_query_uses_binds_for_schemas():
    sql, binds = OracleConnector({"schemas": ["SALES", "HR"]}, {}).objects_query()
    assert "owner IN (:s0, :s1)" in sql and binds == {"s0": "SALES", "s1": "HR"}
    sql, binds = OracleConnector({}, {}).objects_query()
    assert "user_tables" in sql and binds == {}


def test_oracle_connect_builds_dsn(monkeypatch):
    import oracledb

    captured = {}
    monkeypatch.setattr(oracledb, "connect", lambda **kw: captured.update(kw) or "conn")
    OracleConnector(
        {"host": "db", "port": 1522, "service_name": "PDB1", "user": "bi"}, {"password": "pw"}
    ).connect()
    assert captured["user"] == "bi" and captured["password"] == "pw"
    assert "SERVICE_NAME=PDB1" in captured["dsn"] and "PORT=1522" in captured["dsn"]
    OracleConnector({"host": "db", "sid": "ORCL", "user": "bi"}, {}).connect()
    assert "SID=ORCL" in captured["dsn"]


@pytest.mark.parametrize(
    "message,expected",
    [
        ("ORA-01017: invalid username/password", "username or password"),
        ("DPY-6005: cannot connect to database", "Could not reach"),
        ("ORA-12514: TNS:listener does not currently know of service", "service name"),
    ],
)
def test_oracle_error_messages(message, expected):
    assert expected in str(OracleConnector({}, {}).connect_error(Exception(message)))


@pytest.fixture
def sqlite_oracle(tmp_path, monkeypatch):
    """OracleConnector whose driver connection is SQLite (same named-bind style as Oracle)."""
    db = tmp_path / "src.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE ORDERS (ID INTEGER PRIMARY KEY, REGION TEXT, AMOUNT REAL, UPDATED_AT TEXT)")
    con.executemany(
        "INSERT INTO ORDERS VALUES (?, ?, ?, ?)",
        [(i, ["UAE", "KSA"][i % 2], i * 1.5, f"2026-01-{1 + i % 28:02d}") for i in range(1, 101)],
    )
    con.commit()
    con.close()
    monkeypatch.setattr(OracleConnector, "connect", lambda self: sqlite3.connect(db))
    return OracleConnector({"host": "x", "user": "u", "sid": "X"}, {}), db


def test_oracle_extract_batches_and_incremental(sqlite_oracle):
    conn, _ = sqlite_oracle
    batches = list(conn.extract({"object": "ORDERS"}, batch_size=30))
    assert [b.num_rows for b in batches] == [30, 30, 30, 10]
    assert batches[0].schema.names == ["ID", "REGION", "AMOUNT", "UPDATED_AT"]
    newer = list(conn.extract({"object": "ORDERS"}, since=95, incremental_column="ID"))
    assert sum(b.num_rows for b in newer) == 5
    inclusive = list(conn.extract({"object": "ORDERS"}, since=95, incremental_column="ID", inclusive=True))
    assert sum(b.num_rows for b in inclusive) == 6
    custom = list(conn.extract({"sql": "SELECT REGION, SUM(AMOUNT) AS TOTAL FROM ORDERS GROUP BY REGION"}))
    assert custom[0].num_rows == 2


def test_oracle_empty_result_still_has_columns(sqlite_oracle):
    conn, _ = sqlite_oracle
    batches = list(conn.extract({"object": "ORDERS"}, since=10_000, incremental_column="ID"))
    assert len(batches) == 1 and batches[0].num_rows == 0 and batches[0].schema.names[0] == "ID"


def test_sql_identifiers_validated(sqlite_oracle):
    conn, _ = sqlite_oracle
    for bad in ["ORDERS; DROP TABLE X", 'ORDERS"--', "a.b.c"]:
        with pytest.raises(ConnectorError):
            list(conn.extract({"object": bad}))
    with pytest.raises(ConnectorError):
        list(conn.extract({"object": "ORDERS"}, since=1, incremental_column="ID) OR (1=1"))


def test_registry_has_types():
    assert {"file", "oracle", "postgres"} <= {t for t in ("file", "oracle", "postgres") if connector_class(t)}
    with pytest.raises(ConnectorError):
        connector_class("nope")
