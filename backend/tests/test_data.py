import csv
import io
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import openpyxl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.connectors.oracle import OracleConnector
from app.core.config import get_settings
from app.db import SessionLocal
from app.models import Connection, Dataset, Role
from app.services import snapshots
from app.services.sync import dispatch_due
from tests.conftest import Client, make_user

API = "/api/v1"


def csv_bytes(rows: int = 50, extra: bool = False) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Branch", "Emirate", "Staff", "Revenue"] + (["Manager"] if extra else []))
    for i in range(1, rows + 1):
        w.writerow(
            [f"Branch {i}", ["Dubai", "Abu Dhabi", "Sharjah"][i % 3], i, i * 1000.5]
            + ([f"M{i}"] if extra else [])
        )
    return out.getvalue().encode()


def xlsx_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Targets"
    ws.append(["Month", "Region", "Target"])
    for m in range(1, 13):
        ws.append([datetime(2026, m, 1), "UAE", m * 1000])
    wb.create_sheet("Notes").append(["Note"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload(client, content: bytes, filename: str = "branches.csv", name: str = "") -> dict:
    r = client.post(f"{API}/connections/upload", files={"file": (filename, content)}, data={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def make_dataset(client, connection_id: int, **body) -> dict:
    payload = {"name": "Branches", "connection_id": connection_id, "source": {"object": "data"}, **body}
    r = client.post(f"{API}/datasets", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------------ permissions
def test_viewers_and_explorers_cannot_use_data_admin(client):
    for role in (Role.viewer, Role.explorer):
        client.cookies.clear()
        make_user(f"{role.value}@test.com", role)
        client.login(f"{role.value}@test.com")
        assert client.get(f"{API}/connections").status_code == 403
        assert client.get(f"{API}/datasets").status_code == 403
        assert client.post(f"{API}/connections/upload", files={"file": ("a.csv", b"a\n1")}).status_code == 403


def test_creators_share_but_only_owner_or_admin_edits(creator_client):
    conn = upload(creator_client, csv_bytes())
    ds = make_dataset(creator_client, conn["id"])
    make_user("other@test.com", Role.creator)
    make_user("boss@test.com", Role.admin)
    with Client(creator_client.app) as other:
        other.login("other@test.com")
        assert other.get(f"{API}/connections/{conn['id']}").status_code == 200  # can see and reuse
        assert other.get(f"{API}/datasets/{ds['id']}/preview").status_code == 200
        assert (
            other.put(f"{API}/connections/{conn['id']}", json={"name": "x", "type": "file"}).status_code
            == 403
        )
        assert other.delete(f"{API}/datasets/{ds['id']}").status_code == 403
    with Client(creator_client.app) as boss:
        boss.login("boss@test.com")
        assert boss.delete(f"{API}/datasets/{ds['id']}").status_code == 204


# ------------------------------------------------------------------ file uploads
def test_csv_upload_sync_preview(creator_client):
    conn = upload(creator_client, csv_bytes(), name="Branch list")
    assert conn["type"] == "file" and conn["config"]["filename"] == "branches.csv"
    assert "path" in conn["config"]
    objects = creator_client.get(f"{API}/connections/{conn['id']}/objects").json()
    assert objects == [{"name": "data", "kind": "file", "schema": None}]

    ds = make_dataset(creator_client, conn["id"])
    assert ds["last_status"] == "success" and ds["row_count"] == 50 and ds["current_version"] == 1
    assert ds["columns"] == [
        {"name": "Branch", "type": "string"},
        {"name": "Emirate", "type": "string"},
        {"name": "Staff", "type": "integer"},
        {"name": "Revenue", "type": "number"},
    ]
    pv = creator_client.get(f"{API}/datasets/{ds['id']}/preview?limit=5").json()
    assert pv["columns"][0] == "Branch" and len(pv["rows"]) == 5 and pv["rows"][0][0] == "Branch 1"
    runs = creator_client.get(f"{API}/datasets/{ds['id']}/runs").json()
    assert runs[0]["status"] == "success" and runs[0]["rows_total"] == 50 and runs[0]["trigger"] == "manual"


def test_excel_upload_lists_sheets(creator_client):
    conn = upload(creator_client, xlsx_bytes(), "targets.xlsx")
    names = [o["name"] for o in creator_client.get(f"{API}/connections/{conn['id']}/objects").json()]
    assert names == ["Targets", "Notes"]
    ds = make_dataset(creator_client, conn["id"], name="Targets", source={"object": "Targets"})
    assert ds["row_count"] == 12
    assert {c["name"]: c["type"] for c in ds["columns"]} == {
        "Month": "datetime",
        "Region": "string",
        "Target": "integer",
    }


def test_parquet_upload(creator_client):
    buf = io.BytesIO()
    pq.write_table(pa.table({"id": [1, 2, 3], "name": ["a", "b", "c"]}), buf)
    conn = upload(creator_client, buf.getvalue(), "items.parquet")
    assert make_dataset(creator_client, conn["id"])["row_count"] == 3


def test_upload_rejects_bad_files(creator_client, monkeypatch):
    r = creator_client.post(f"{API}/connections/upload", files={"file": ("virus.exe", b"MZ")})
    assert r.status_code == 422
    r = creator_client.post(f"{API}/connections/upload", files={"file": ("broken.xlsx", b"not excel")})
    assert r.status_code == 422
    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    r = creator_client.post(f"{API}/connections/upload", files={"file": ("big.csv", csv_bytes())})
    assert r.status_code == 422 and "larger" in r.json()["detail"]


def test_replacing_file_resyncs_detects_schema_change_and_keeps_three_versions(creator_client):
    conn = upload(creator_client, csv_bytes(10))
    ds = make_dataset(creator_client, conn["id"])
    for rows in (20, 30, 40):
        r = creator_client.put(
            f"{API}/connections/{conn['id']}/file", files={"file": ("b.csv", csv_bytes(rows))}
        )
        assert r.status_code == 200
    r = creator_client.put(
        f"{API}/connections/{conn['id']}/file", files={"file": ("b.csv", csv_bytes(5, extra=True))}
    )
    ds = creator_client.get(f"{API}/datasets/{ds['id']}").json()
    assert ds["current_version"] == 5 and ds["row_count"] == 5
    run = creator_client.get(f"{API}/datasets/{ds['id']}/runs").json()[0]
    assert run["trigger"] == "upload" and run["details"]["schema_changes"] == {"added": ["Manager"]}
    assert snapshots.versions(ds["id"]) == [3, 4, 5]


# ------------------------------------------------------------------ validation
def test_dataset_validation(creator_client):
    conn = upload(creator_client, csv_bytes())
    cid = conn["id"]
    cases = [
        {
            "source": {"object": "data"},
            "sync_mode": "incremental",
            "incremental_column": "Staff",
        },  # files: full only
        {"source": {"sql": "select 1"}},  # files: no SQL
        {"source": {}},
        {"source": {"object": "data"}, "schedule": "every day"},
        {"source": {"object": "data"}, "schedule": "* * * * *"},  # every minute is too often
    ]
    for case in cases:
        r = creator_client.post(f"{API}/datasets", json={"name": "x", "connection_id": cid, **case})
        assert r.status_code == 422, case


# ---------------------------------------------- database source (Oracle code path, SQLite driver)
@pytest.fixture
def oracle_source(tmp_path, monkeypatch):
    db = tmp_path / "erp.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE ORDERS (ID INTEGER PRIMARY KEY, REGION TEXT, AMOUNT REAL, UPDATED_AT TEXT)")
    con.executemany(
        "INSERT INTO ORDERS VALUES (?, ?, ?, ?)",
        [(i, ["UAE", "KSA"][i % 2], float(i), f"2026-01-01 00:{i % 60:02d}:00") for i in range(1, 101)],
    )
    con.commit()
    con.close()
    monkeypatch.setattr(OracleConnector, "connect", lambda self: sqlite3.connect(db))
    monkeypatch.setattr(OracleConnector, "ping_sql", lambda self: "SELECT 1")
    monkeypatch.setattr(
        OracleConnector,
        "objects_query",
        lambda self: ("SELECT NULL, name, 'TABLE' FROM sqlite_master WHERE type='table'", {}),
    )
    return db


def oracle_connection(client) -> dict:
    r = client.post(
        f"{API}/connections",
        json={
            "name": "ERP",
            "type": "oracle",
            "secrets": {"password": "Sup3rSecret", "junk": "ignored"},
            "config": {"host": "erp.local", "user": "BI_READER", "service_name": "ERPPDB"},
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_connection_secrets_are_encrypted_and_never_returned(creator_client, oracle_source):
    conn = oracle_connection(creator_client)
    assert conn["secrets_set"] == ["password"] and "Sup3rSecret" not in str(conn)
    with SessionLocal() as s:
        stored = s.get(Connection, conn["id"]).secrets_enc
    assert stored and "Sup3rSecret" not in stored and "junk" not in stored
    # updating without secrets keeps them
    r = creator_client.put(
        f"{API}/connections/{conn['id']}",
        json={
            "name": "ERP prod",
            "type": "oracle",
            "config": {"host": "erp.local", "user": "BI_READER", "sid": "ERP"},
        },
    )
    assert r.json()["secrets_set"] == ["password"] and r.json()["config"]["sid"] == "ERP"
    assert creator_client.post(f"{API}/connections/{conn['id']}/test").json()["ok"] is True
    objects = creator_client.get(f"{API}/connections/{conn['id']}/objects").json()
    assert [o["name"] for o in objects] == ["ORDERS"]


def test_incremental_sync_merges_by_primary_key(creator_client, oracle_source):
    conn = oracle_connection(creator_client)
    ds = make_dataset(
        creator_client,
        conn["id"],
        name="Orders",
        source={"object": "ORDERS"},
        sync_mode="incremental",
        incremental_column="UPDATED_AT",
        primary_key=["ID"],
    )
    assert ds["row_count"] == 100
    first_run = creator_client.get(f"{API}/datasets/{ds['id']}/runs").json()[0]
    assert first_run["mode"] == "full"  # nothing to merge into the first time

    con = sqlite3.connect(oracle_source)
    con.executemany(
        "INSERT INTO ORDERS VALUES (?, ?, ?, ?)",
        [(i, "QAT", float(i), "2026-02-01 00:00:00") for i in range(101, 111)],
    )
    con.execute("UPDATE ORDERS SET AMOUNT = 9999, UPDATED_AT = '2026-02-01 00:00:00' WHERE ID <= 5")
    con.commit()
    con.close()

    run = creator_client.post(f"{API}/datasets/{ds['id']}/sync").json()
    assert run["status"] == "success" and run["mode"] == "incremental", run
    # 15 changed rows + the row at the previous high-water mark (re-read on purpose: same-second safety)
    assert run["rows_read"] == 16 and run["rows_total"] == 110
    path = snapshots.snapshot_path(ds["id"], run["version"])
    import duckdb

    amounts = duckdb.sql(
        f"SELECT ID, AMOUNT FROM read_parquet('{path}') WHERE ID <= 5 ORDER BY ID"
    ).fetchall()
    assert amounts == [(i, 9999.0) for i in range(1, 6)]
    assert duckdb.sql(f"SELECT count(DISTINCT ID) FROM read_parquet('{path}')").fetchall()[0][0] == 110


def test_failed_sync_keeps_live_data_and_emails_owner(creator_client, oracle_source, caplog):
    conn = oracle_connection(creator_client)
    ds = make_dataset(creator_client, conn["id"], name="Orders", source={"object": "ORDERS"})
    con = sqlite3.connect(oracle_source)
    con.execute("DROP TABLE ORDERS")
    con.commit()
    con.close()
    caplog.set_level("WARNING", logger="lumen.email")
    run = creator_client.post(f"{API}/datasets/{ds['id']}/sync").json()
    after = creator_client.get(f"{API}/datasets/{ds['id']}").json()
    runs = creator_client.get(f"{API}/datasets/{ds['id']}/runs").json()
    assert run["id"] == runs[0]["id"] and runs[0]["status"] == "failed" and "query failed" in runs[0]["error"]
    assert after["last_status"] == "failed" and after["current_version"] == 1 and after["row_count"] == 100
    assert (
        creator_client.get(f"{API}/datasets/{ds['id']}/preview").status_code == 200
    )  # old data still served
    assert "Data sync failed: Orders" in caplog.text and "creator@test.com" in caplog.text


def test_changing_source_rebuilds_snapshot(creator_client, oracle_source):
    conn = oracle_connection(creator_client)
    ds = make_dataset(creator_client, conn["id"], name="Orders", source={"object": "ORDERS"})
    r = creator_client.put(
        f"{API}/datasets/{ds['id']}",
        json={
            "name": "UAE orders",
            "connection_id": conn["id"],
            "source": {"sql": "SELECT * FROM ORDERS WHERE REGION = 'UAE'"},
        },
    )
    assert r.status_code == 200
    ds = creator_client.get(f"{API}/datasets/{ds['id']}").json()
    assert ds["row_count"] == 50 and ds["name"] == "UAE orders"


def test_injection_in_table_name_rejected(creator_client, oracle_source):
    conn = oracle_connection(creator_client)
    r = creator_client.post(
        f"{API}/datasets",
        json={"name": "x", "connection_id": conn["id"], "source": {"object": "ORDERS; DROP TABLE ORDERS"}},
    )
    run = creator_client.get(f"{API}/datasets/{r.json()['id']}/runs").json()[0]
    assert run["status"] == "failed" and "Invalid name" in run["error"]
    con = sqlite3.connect(oracle_source)
    assert con.execute("SELECT count(*) FROM ORDERS").fetchone()[0] == 100  # table untouched


# ------------------------------------------------------------------ scheduling and deletion
def test_scheduler_runs_due_datasets(creator_client):
    conn = upload(creator_client, csv_bytes())
    ds = make_dataset(creator_client, conn["id"], schedule="*/15 * * * *")
    assert ds["next_run_at"] is not None
    with SessionLocal() as s:
        s.get(Dataset, ds["id"]).next_run_at = datetime.now(UTC) - timedelta(minutes=1)
        s.commit()
    assert dispatch_due() == 1
    runs = creator_client.get(f"{API}/datasets/{ds['id']}/runs").json()
    assert runs[0]["trigger"] == "schedule" and runs[0]["status"] == "success"
    after = creator_client.get(f"{API}/datasets/{ds['id']}").json()
    assert datetime.fromisoformat(after["next_run_at"]) > datetime.now(UTC)
    assert dispatch_due() == 0  # not due again yet


def test_delete_connection_removes_datasets_snapshots_and_file(creator_client):
    conn = upload(creator_client, csv_bytes())
    ds = make_dataset(creator_client, conn["id"])
    path = conn["config"]["path"]
    assert os.path.exists(path) and snapshots.versions(ds["id"]) == [1]
    assert creator_client.delete(f"{API}/connections/{conn['id']}").status_code == 204
    assert not os.path.exists(path) and snapshots.versions(ds["id"]) == []
    assert creator_client.get(f"{API}/datasets/{ds['id']}").status_code == 404


def test_audit_records_data_actions(creator_client):
    conn = upload(creator_client, csv_bytes())
    make_dataset(creator_client, conn["id"])
    make_user("admin2@test.com", Role.admin)
    with Client(creator_client.app) as admin:
        admin.login("admin2@test.com")
        actions = {e["action"] for e in admin.get(f"{API}/admin/audit").json()["items"]}
    assert {"connection.upload", "dataset.create"} <= actions


# ------------------------------------------------------------------ real PostgreSQL source (when available)
def _pg_params():
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        return None
    u = urlparse(url.replace("postgresql+psycopg", "postgresql"))
    query = dict(p.split("=", 1) for p in u.query.split("&") if "=" in p)
    return {
        "host": query.get("host") or u.hostname,
        "port": int(query.get("port") or u.port or 5432),
        "database": u.path.lstrip("/"),
        "user": u.username,
        "password": u.password or "",
    }


@pytest.fixture
def pg_source():
    params = _pg_params()
    if not params:
        pytest.skip("Set TEST_DATABASE_URL to a PostgreSQL database to test the PostgreSQL connector")
    import psycopg

    with psycopg.connect(
        host=params["host"],
        port=params["port"],
        dbname=params["database"],
        user=params["user"],
        password=params["password"],
        autocommit=True,
    ) as con:
        con.execute("DROP SCHEMA IF EXISTS src CASCADE")
        con.execute("CREATE SCHEMA src")
        con.execute(
            "CREATE TABLE src.sales (id int PRIMARY KEY, region text, amount numeric(12,2), "
            "updated_at timestamptz, active boolean)"
        )
        con.execute(
            "INSERT INTO src.sales SELECT g, (ARRAY['UAE','KSA','QAT'])[1 + g % 3], g * 10.25, "
            "timestamptz '2026-01-01' + g * interval '1 minute', g % 2 = 0 FROM generate_series(1, 2000) g"
        )
        con.execute("CREATE VIEW src.uae_sales AS SELECT * FROM src.sales WHERE region = 'UAE'")
    yield params
    with psycopg.connect(
        host=params["host"],
        port=params["port"],
        dbname=params["database"],
        user=params["user"],
        password=params["password"],
        autocommit=True,
    ) as con:
        con.execute("DROP SCHEMA IF EXISTS src CASCADE")


def test_postgres_source_end_to_end(creator_client, pg_source):
    p = pg_source
    body = {
        "name": "Warehouse",
        "type": "postgres",
        "secrets": {"password": p["password"]},
        "config": {
            "host": p["host"],
            "port": p["port"],
            "database": p["database"],
            "user": p["user"],
            "schemas": "src",
            "sslmode": "disable" if p["host"].startswith("/") else "prefer",
        },
    }
    assert creator_client.post(f"{API}/connections/test", json=body).json()["ok"] is True
    bad = {**body, "config": {**body["config"], "database": "no_such_db"}}
    assert creator_client.post(f"{API}/connections/test", json=bad).json()["ok"] is False
    conn = creator_client.post(f"{API}/connections", json=body).json()

    objects = creator_client.get(f"{API}/connections/{conn['id']}/objects").json()
    assert {(o["name"], o["kind"]) for o in objects} == {("src.sales", "table"), ("src.uae_sales", "view")}
    cols = creator_client.post(f"{API}/connections/{conn['id']}/schema", json={"object": "src.sales"}).json()
    assert {c["name"]: c["type"] for c in cols} == {
        "id": "integer",
        "region": "string",
        "amount": "number",
        "updated_at": "datetime",
        "active": "boolean",
    }

    ds = make_dataset(
        creator_client,
        conn["id"],
        name="Sales",
        source={"object": "src.sales"},
        sync_mode="incremental",
        incremental_column="updated_at",
        primary_key=["id"],
    )
    assert ds["row_count"] == 2000

    import psycopg

    with psycopg.connect(
        host=p["host"],
        port=p["port"],
        dbname=p["database"],
        user=p["user"],
        password=p["password"],
        autocommit=True,
    ) as con:
        con.execute(
            "INSERT INTO src.sales SELECT g, 'OMN', 1, timestamptz '2026-03-01', true "
            "FROM generate_series(2001, 2100) g"
        )
        con.execute("UPDATE src.sales SET amount = 0, updated_at = timestamptz '2026-03-01' WHERE id <= 10")
    run = creator_client.post(f"{API}/datasets/{ds['id']}/sync").json()
    assert run["mode"] == "incremental" and run["rows_total"] == 2100 and run["rows_read"] == 111, run

    view_ds = make_dataset(creator_client, conn["id"], name="UAE", source={"object": "src.uae_sales"})
    assert view_ds["row_count"] == 666  # ids 3, 6, ... 1998
    sql_ds = make_dataset(
        creator_client,
        conn["id"],
        name="By region",
        source={"sql": "select region, sum(amount) as total from src.sales group by region"},
    )
    assert sql_ds["row_count"] == 4
