"""Salesforce and REST connectors against the fake servers in tools/fake_sources.py."""

import socket

import duckdb
import httpx
import pyarrow.parquet as pq
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.connectors import ConnectorError, http
from app.connectors.rest import RestConnector, dig, flatten
from app.connectors.salesforce import SalesforceConnector, add_condition, convert, soql_literal
from app.services import snapshots
from tests.conftest import make_user  # noqa: F401
from tools.fake_sources import FakeRestApi, FakeSalesforce

API = "/api/v1"
SF_HOST, REST_HOST = "acme.my.salesforce.com", "api.example.com"


@pytest.fixture
def fakes(monkeypatch):
    sf, api = FakeSalesforce(), FakeRestApi()
    routes = {SF_HOST: sf, REST_HOST: api}

    def router(request: httpx.Request) -> httpx.Response:
        fake = routes.get(request.url.host)
        return fake.handler(request) if fake else httpx.Response(502)

    monkeypatch.setattr(http, "transport", httpx.MockTransport(router))
    waits: list[float] = []
    monkeypatch.setattr(http, "sleep", waits.append)
    real_getaddrinfo = socket.getaddrinfo

    def fake_dns(host, *args, **kwargs):  # no real DNS in tests: fake hosts resolve to a public address
        if host in routes:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", 443))]
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_dns)
    return {"sf": sf, "api": api, "routes": routes, "waits": waits}


def sf_body(**config) -> dict:
    secrets = config.pop("secrets", {"client_secret": "sf-secret"})
    return {
        "name": "Salesforce",
        "type": "salesforce",
        "secrets": secrets,
        "config": {"domain_url": f"https://{SF_HOST}", "client_id": "lumen-client", **config},
    }


def create(client, body) -> dict:
    r = client.post(f"{API}/connections", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def dataset(client, conn_id: int, source: dict, **kw) -> dict:
    r = client.post(
        f"{API}/datasets",
        json={"name": kw.pop("name", "ds"), "connection_id": conn_id, "source": source, **kw},
    )
    assert r.status_code == 201, r.text
    return r.json()


def snapshot_rows(ds: dict, sql: str):
    path = snapshots.snapshot_path(ds["id"], ds["current_version"])
    return duckdb.sql(sql.replace("{T}", f"read_parquet('{path}')")).fetchall()


# ===================================================================== Salesforce: units
def test_salesforce_config_validation():
    cfg = SalesforceConnector.validate_config({"domain_url": "acme.my.salesforce.com/", "client_id": "x"})
    assert cfg["domain_url"] == "https://acme.my.salesforce.com" and cfg["auth"] == "client_credentials"
    for bad in [
        {"domain_url": "http://acme.my.salesforce.com", "client_id": "x"},
        {"domain_url": "https://acme.my.salesforce.com"},
        {"domain_url": "https://a.my.salesforce.com", "client_id": "x", "auth": "jwt"},
        {"domain_url": "https://a.my.salesforce.com", "client_id": "x", "api_version": "latest"},
    ]:
        with pytest.raises(ConnectorError):
            SalesforceConnector.validate_config(bad)


def test_soql_helpers():
    from datetime import UTC, date, datetime

    assert soql_literal(datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=UTC)) == "2026-01-02T03:04:05Z"
    assert soql_literal(date(2026, 1, 2)) == "2026-01-02" and soql_literal("O'Brien") == "'O\\'Brien'"
    assert add_condition("SELECT Id FROM Account", "X > 1") == "SELECT Id FROM Account WHERE X > 1"
    assert (
        add_condition("SELECT Id FROM Account WHERE Type = 'A'", "X > 1")
        == "SELECT Id FROM Account WHERE (X > 1) AND (Type = 'A')"
    )
    with pytest.raises(ConnectorError):
        add_condition("SELECT Id FROM Account ORDER BY Name", "X > 1")
    assert convert("2026-01-01T10:00:00.000+0000", "datetime").hour == 10
    assert (
        convert("true", "boolean") is True and convert("", "currency") is None and convert("12", "int") == 12
    )


# ===================================================================== Salesforce: through the API
def test_salesforce_connect_list_and_describe(creator_client, fakes):
    assert creator_client.post(f"{API}/connections/test", json=sf_body()).json() == {
        "ok": True,
        "message": "Connected successfully",
    }
    bad = creator_client.post(
        f"{API}/connections/test", json=sf_body(secrets={"client_secret": "nope"})
    ).json()
    assert bad["ok"] is False and "consumer key or secret is wrong" in bad["message"]

    conn = create(creator_client, sf_body())
    assert conn["secrets_set"] == ["client_secret"]
    objects = creator_client.get(f"{API}/connections/{conn['id']}/objects").json()
    assert [(o["name"], o["label"]) for o in objects] == [
        ("Account", "Account"),
        ("Opportunity", "Opportunity"),
    ]
    cols = creator_client.post(f"{API}/connections/{conn['id']}/schema", json={"object": "Account"}).json()
    types = {c["name"]: c["type"] for c in cols}
    assert "BillingAddress" not in types  # compound fields are skipped
    assert types["AnnualRevenue"] == "number" and types["NumberOfEmployees"] == "integer"
    assert types["SystemModstamp"] == "datetime"
    # newest API version was picked automatically
    assert any("/services/data/v66.0/" in c for c in fakes["sf"].calls)


def test_salesforce_full_sync_pages_and_types(creator_client, fakes):
    conn = create(creator_client, sf_body())
    ds = dataset(creator_client, conn["id"], {"object": "Opportunity"})
    assert ds["last_status"] == "success" and ds["row_count"] == 300
    assert any("/query/01g" in c for c in fakes["sf"].calls)  # followed nextRecordsUrl
    assert not any("jobs/query" in c for c in fakes["sf"].calls)  # small: REST, not Bulk
    schema = pq.ParquetFile(snapshots.snapshot_path(ds["id"], 1)).schema_arrow
    assert str(schema.field("CloseDate").type) == "date32[day]"
    assert str(schema.field("SystemModstamp").type).endswith("tz=UTC]")
    assert str(schema.field("IsWon").type) == "bool" and "_lumen_deleted" not in schema.names


def test_salesforce_incremental_handles_updates_inserts_and_deletes(creator_client, fakes):
    sf = fakes["sf"]
    conn = create(creator_client, sf_body())
    ds = dataset(
        creator_client,
        conn["id"],
        {"object": "Opportunity"},
        sync_mode="incremental",
        incremental_column="SystemModstamp",
        primary_key=["Id"],
    )
    for i in range(301, 306):
        sf.add_opportunity(i, amount=1.0)
    sf.update("Opportunity", "006000000000000001", Amount=555.0, StageName="Closed Won")
    sf.delete("Opportunity", "006000000000000002")
    sf.delete("Opportunity", "006000000000000003")
    sf.calls.clear()

    run = creator_client.post(f"{API}/datasets/{ds['id']}/sync").json()
    assert run["status"] == "success" and run["mode"] == "incremental", run
    assert run["rows_total"] == 303  # 300 + 5 new - 2 deleted
    assert any("/queryAll" in c for c in sf.calls)
    ds = creator_client.get(f"{API}/datasets/{ds['id']}").json()
    rows = dict(snapshot_rows(ds, "SELECT Id, Amount FROM {T}"))
    assert rows["006000000000000001"] == 555.0
    assert "006000000000000002" not in rows and "006000000000000003" not in rows
    assert (
        "_lumen_deleted"
        not in pq.ParquetFile(snapshots.snapshot_path(ds["id"], ds["current_version"])).schema_arrow.names
    )


def test_salesforce_bulk_api_for_large_objects(creator_client, fakes):
    sf = fakes["sf"]
    conn = create(creator_client, sf_body(bulk_threshold=100))
    ds = dataset(
        creator_client,
        conn["id"],
        {"object": "Opportunity"},
        sync_mode="incremental",
        incremental_column="SystemModstamp",
        primary_key=["Id"],
    )
    assert ds["row_count"] == 300 and any("POST /services/data/v66.0/jobs/query" in c for c in sf.calls)
    assert fakes["waits"]  # polled the job until complete
    for i in range(301, 451):  # 150 changes: still bulk
        sf.add_opportunity(i, amount=2.0)
    sf.delete("Opportunity", "006000000000000010")
    run = creator_client.post(f"{API}/datasets/{ds['id']}/sync").json()
    assert run["status"] == "success" and run["rows_total"] == 449, run
    assert any(job["query"].count("SystemModstamp >=") for job in sf.jobs.values())


def test_salesforce_bulk_results_are_paged_with_locator(fakes):
    connector = SalesforceConnector(
        SalesforceConnector.validate_config(
            {"domain_url": f"https://{SF_HOST}", "client_id": "lumen-client", "bulk_threshold": 1}
        ),
        {"client_secret": "sf-secret"},
    )
    batches = list(connector.extract({"object": "Opportunity"}, batch_size=120))
    assert [b.num_rows for b in batches] == [120, 120, 60]
    assert str(batches[0].schema.field("Amount").type) == "double"


def test_salesforce_custom_soql(creator_client, fakes):
    conn = create(creator_client, sf_body())
    ds = dataset(
        creator_client,
        conn["id"],
        {"sql": "SELECT Id, Name, Amount, Account.Name FROM Opportunity WHERE StageName = 'Prospecting'"},
    )
    assert ds["row_count"] == 300
    assert {c["name"] for c in ds["columns"]} == {"Id", "Name", "Amount", "Account.Name"}
    bad = dataset(creator_client, conn["id"], {"sql": "SELECT Id, Nope__c FROM Opportunity"})
    run = creator_client.get(f"{API}/datasets/{bad['id']}/runs").json()[0]
    assert run["status"] == "failed" and "rejected the query" in run["error"]


def test_salesforce_jwt_bearer(creator_client, fakes, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    fakes["sf"].jwt_public_key = key.public_key()
    body = sf_body(auth="jwt", username="integration@acme.com", secrets={"private_key": pem})
    assert creator_client.post(f"{API}/connections/test", json=body).json()["ok"] is True
    wrong = sf_body(auth="jwt", username="someone@acme.com", secrets={"private_key": pem})
    assert (
        "refused the sign-in" in creator_client.post(f"{API}/connections/test", json=wrong).json()["message"]
    )
    junk = sf_body(auth="jwt", username="integration@acme.com", secrets={"private_key": "not a key"})
    assert "private key" in creator_client.post(f"{API}/connections/test", json=junk).json()["message"]


def test_salesforce_reauthenticates_when_token_expires(fakes):
    sf = fakes["sf"]
    connector = SalesforceConnector(
        SalesforceConnector.validate_config(
            {"domain_url": f"https://{SF_HOST}", "client_id": "lumen-client"}
        ),
        {"client_secret": "sf-secret"},
    )
    connector.list_objects()
    sf.tokens.clear()  # session expires
    assert len(connector.list_objects()) == 2


def test_salesforce_api_limit_message(creator_client, fakes):
    conn = create(creator_client, sf_body())
    fakes["sf"].api_calls_left = 0
    ds = dataset(creator_client, conn["id"], {"object": "Account"})
    run = creator_client.get(f"{API}/datasets/{ds['id']}/runs").json()[0]
    assert run["status"] == "failed" and "API request limit" in run["error"]


# ===================================================================== REST: units
def test_dig_and_flatten():
    body = {"data": {"items": [{"a": 1}]}, "results": [[1], [2]]}
    assert (
        dig(body, "data.items") == [{"a": 1}] and dig(body, "results.1") == [2] and dig(body, "x.y") is None
    )
    flat = flatten({"id": 1, "customer": {"name": "A", "address": {"city": "Dubai"}}, "tags": ["x", "y"]})
    assert flat == {"id": 1, "customer.name": "A", "customer.address.city": "Dubai", "tags": '["x", "y"]'}


def test_rest_config_validation():
    ok = RestConnector.validate_config(
        {"base_url": "https://api.example.com/", "endpoints": "orders\n/customers"}
    )
    assert ok["endpoints"] == ["/orders", "/customers"] and ok["base_url"] == "https://api.example.com"
    for bad in [
        {"base_url": "ftp://x", "endpoints": ["/a"]},
        {"base_url": "https://x.com", "endpoints": []},
        {"base_url": "https://x.com", "endpoints": ["/a"], "headers": {"Authorization": "Bearer x"}},
        {"base_url": "https://x.com", "endpoints": ["/a"], "pagination": "cursor"},
        {"base_url": "https://x.com", "endpoints": ["/a"], "auth": "oauth2"},
    ]:
        with pytest.raises(ConnectorError):
            RestConnector.validate_config(bad)


# ===================================================================== REST: through the API
def rest_body(**config) -> dict:
    secrets = config.pop("secrets", {"api_key": "dev-key"})
    return {
        "name": "Shop API",
        "type": "rest",
        "secrets": secrets,
        "config": {
            "base_url": f"https://{REST_HOST}",
            "auth": "api_key",
            "api_key_name": "X-API-Key",
            "endpoints": ["/v1/orders"],
            "records_path": "data.items",
            "pagination": "page",
            "page_size": 20,
            **config,
        },
    }


PAGINATION_CASES = {
    "page": {
        "endpoints": ["/v1/orders"],
        "records_path": "data.items",
        "pagination": "page",
        "page_size": 20,
    },
    "offset": {
        "endpoints": ["/v2/orders"],
        "records_path": "results",
        "pagination": "offset",
        "page_size": 20,
    },
    "cursor": {
        "endpoints": ["/v3/orders"],
        "records_path": "items",
        "pagination": "cursor",
        "cursor_path": "meta.next_cursor",
        "size_param": "limit",
        "page_size": 20,
    },
    "next_link": {
        "endpoints": ["/v4/orders"],
        "records_path": "orders",
        "pagination": "next_link",
        "next_path": "links.next",
    },
    "link_header": {"endpoints": ["/v5/orders"], "records_path": "", "pagination": "link_header"},
}


@pytest.mark.parametrize("style", list(PAGINATION_CASES))
def test_rest_pagination_styles(creator_client, fakes, style):
    conn = create(creator_client, rest_body(**PAGINATION_CASES[style]))
    ds = dataset(creator_client, conn["id"], {"object": PAGINATION_CASES[style]["endpoints"][0]})
    assert ds["last_status"] == "success" and ds["row_count"] == 95, ds
    types = {c["name"]: c["type"] for c in ds["columns"]}
    assert types["customer.name"] == "string" and types["total"] == "number"
    assert types["updated_at"] == "datetime" and types["items"] == "string"  # lists kept as JSON text
    assert len(snapshot_rows(ds, "SELECT DISTINCT id FROM {T}")) == 95


@pytest.mark.parametrize(
    "auth,config,secrets",
    [
        ("bearer", {}, {"token": "tok"}),
        ("basic", {"username": "svc"}, {"password": "pw"}),
        ("api_key", {"api_key_name": "api_key", "api_key_in": "query"}, {"api_key": "dev-key"}),
        (
            "oauth2",
            {"token_url": f"https://{REST_HOST}/oauth/token", "client_id": "rest-client"},
            {"client_secret": "rest-secret"},
        ),
    ],
)
def test_rest_auth_methods(creator_client, fakes, auth, config, secrets):
    fakes["api"].auth = auth if auth != "api_key" else "api_key"
    body = rest_body(auth=auth, secrets=secrets, **config)
    assert creator_client.post(f"{API}/connections/test", json=body).json()["ok"] is True
    wrong = rest_body(auth=auth, secrets={k: "wrong" for k in secrets}, **config)
    result = creator_client.post(f"{API}/connections/test", json=wrong).json()
    assert result["ok"] is False and (
        "refused access" in result["message"] or "sign-in failed" in result["message"]
    )


def test_rest_incremental_with_since_parameter(creator_client, fakes):
    api = fakes["api"]
    conn = create(creator_client, rest_body(since_param="updated_since", since_format="iso"))
    ds = dataset(
        creator_client,
        conn["id"],
        {"object": "/v1/orders"},
        sync_mode="incremental",
        incremental_column="updated_at",
        primary_key=["id"],
    )
    assert ds["row_count"] == 95
    api.requests.clear()
    for i in range(96, 99):
        api.add_order(i)
    api.update_order(1, total=999.0)
    run = creator_client.post(f"{API}/datasets/{ds['id']}/sync").json()
    # the API filters "updated after", so exactly the 3 new orders and the 1 changed order come back
    assert run["mode"] == "incremental" and run["rows_total"] == 98 and run["rows_read"] == 4, run
    assert all("updated_since" in str(r.url) for r in api.requests)
    ds = creator_client.get(f"{API}/datasets/{ds['id']}").json()
    assert dict(snapshot_rows(ds, "SELECT id, total FROM {T}"))[1] == 999.0


def test_rest_incremental_needs_since_parameter(creator_client, fakes):
    conn = create(creator_client, rest_body())
    r = creator_client.post(
        f"{API}/datasets",
        json={
            "name": "x",
            "connection_id": conn["id"],
            "source": {"object": "/v1/orders"},
            "sync_mode": "incremental",
            "incremental_column": "updated_at",
        },
    )
    assert r.status_code == 422 and "changed since" in r.json()["detail"]


def test_rest_retries_rate_limits(creator_client, fakes):
    fakes["api"].rate_limit_next = 2
    conn = create(creator_client, rest_body())
    ds = dataset(creator_client, conn["id"], {"object": "/v1/orders"})
    assert ds["row_count"] == 95 and fakes["waits"][:2] == [1.0, 1.0]  # honoured Retry-After


def test_rest_endpoint_with_query_string_and_max_pages(creator_client, fakes):
    api = fakes["api"]
    for o in api.orders[:10]:
        o["status"] = "refunded"
    conn = create(creator_client, rest_body(endpoints=["/v1/orders?status=paid"]))
    assert dataset(creator_client, conn["id"], {"object": "/v1/orders?status=paid"})["row_count"] == 85
    small = create(creator_client, rest_body(max_pages=2))
    ds = dataset(creator_client, small["id"], {"object": "/v1/orders"}, name="capped")
    run = creator_client.get(f"{API}/datasets/{ds['id']}/runs").json()[0]
    assert run["status"] == "failed" and "Stopped after 2 pages" in run["error"]


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254",
        "http://localhost:8000",
        "http://127.0.0.1",
        "http://[::1]:80",
        "http://metadata.google.internal",
    ],
)
def test_rest_blocks_local_and_metadata_addresses(creator_client, fakes, url):
    result = creator_client.post(f"{API}/connections/test", json=rest_body(base_url=url)).json()
    assert result["ok"] is False and "not allowed" in result["message"]


def test_rest_secrets_not_returned(creator_client, fakes):
    conn = create(creator_client, rest_body())
    assert conn["secrets_set"] == ["api_key"] and "dev-key" not in str(conn)
