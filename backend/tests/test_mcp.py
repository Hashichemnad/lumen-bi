# ruff: noqa: F811 - pytest fixtures imported from test_dashboards are used as parameters
"""Milestone 13 MCP server: OAuth 2.1 (registration, PKCE S256, consent, rotating refresh tokens with replay
detection, revoke), read-only tools with each person's row-level security, audit, admin switch."""

import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from app.db import SessionLocal
from app.models import AuditLog
from tests.test_dashboards import world  # noqa: F401 - fixture

API = "/api/v1"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def register(client, uris=None):
    return client.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": uris or [CALLBACK]})


def authorize(person, client_id, challenge, state="xyz"):
    params = {
        "client_id": client_id,
        "redirect_uri": CALLBACK,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": "http://localhost:8080/mcp",
    }
    return params, person.get(f"{API}/oauth/request?{urlencode(params)}")


def connect(person, client_id):
    """Full sign-in: consent, code, tokens."""
    verifier, challenge = pkce()
    params, r = authorize(person, client_id, challenge)
    assert r.status_code == 200, r.text
    r = person.post(f"{API}/oauth/approve", json=params)
    assert r.status_code == 200, r.text
    q = parse_qs(urlparse(r.json()["redirect"]).query)
    assert q["state"] == ["xyz"]
    r = person.post(
        "/oauth/token",
        data={"grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": CALLBACK,
              "client_id": client_id, "code_verifier": verifier},
    )  # fmt: skip
    assert r.status_code == 200, r.text
    return r.json()


def rpc(client, token, method, params=None, id_=1, **headers):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}},
        headers={"Authorization": f"Bearer {token}", **headers},
    )


def call(client, token, tool, **args):
    r = rpc(client, token, "tools/call", {"name": tool, "arguments": args})
    assert r.status_code == 200, r.text
    return r.json()["result"]


@pytest.fixture
def mcp(world):
    admin = world["admin@test.com"]
    r = admin.put(f"{API}/admin/mcp", json={"enabled": True, "min_role": "explorer"})
    assert r.status_code == 200, r.text
    world["owner"].put(f"{API}/datasets/{world['ds']['id']}/access", json={"everyone": True})
    world["owner"].put(
        f"{API}/dashboards/{world['dash']['id']}/sharing",
        json={"everyone": "view", "users": [], "groups": []},
    )
    client_id = register(world["owner"]).json()["client_id"]
    return {**world, "client_id": client_id}


# ------------------------------------------------------------------ discovery
def test_metadata(client):
    pr = client.get("/.well-known/oauth-protected-resource").json()
    assert pr["resource"] == "http://localhost:8080/mcp" and pr["authorization_servers"] == [
        "http://localhost:8080"
    ]
    assert client.get("/.well-known/oauth-protected-resource/mcp").json() == pr
    asm = client.get("/.well-known/oauth-authorization-server").json()
    assert asm["code_challenge_methods_supported"] == ["S256"]
    assert asm["registration_endpoint"].endswith("/oauth/register")
    assert asm["authorization_endpoint"] == "http://localhost:8080/oauth/authorize"


def test_mcp_needs_a_token(mcp):
    r = mcp["owner"].post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r.status_code == 401
    assert (
        'resource_metadata="http://localhost:8080/.well-known/oauth-protected-resource"'
        in r.headers["www-authenticate"]
    )
    r = rpc(mcp["owner"], "lmn_at_nonsense", "initialize")
    assert r.status_code == 401 and "invalid_token" in r.headers["www-authenticate"]


# ------------------------------------------------------------------ admin
def test_admin_switch(world):
    for c in (world["owner"], world["ex@test.com"]):
        assert c.get(f"{API}/admin/mcp").status_code == 403
        assert c.put(f"{API}/admin/mcp", json={"enabled": True}).status_code == 403
    assert world["admin@test.com"].get(f"{API}/admin/mcp").json()["enabled"] is False
    # Off: no registration, no consent, no MCP
    assert register(world["owner"]).status_code == 403
    assert world["owner"].post("/mcp", json={}).status_code == 404
    with SessionLocal() as s:
        world["admin@test.com"].put(f"{API}/admin/mcp", json={"enabled": True})
        assert s.query(AuditLog).filter_by(action="mcp.settings").count() == 1


# ------------------------------------------------------------------ OAuth
def test_registration_checks_redirects(mcp):
    owner = mcp["owner"]
    assert register(owner, ["http://evil.example/cb"]).status_code == 400
    assert register(owner, ["https://ok.example/cb#frag"]).status_code == 400
    assert register(owner, ["http://localhost:33418/callback"]).status_code == 201
    r = owner.post(
        "/oauth/register",
        json={"redirect_uris": [CALLBACK], "token_endpoint_auth_method": "client_secret_basic"},
    )
    assert r.status_code == 400


def test_consent_checks_the_request(mcp):
    ex = mcp["ex@test.com"]
    _, challenge = pkce()
    params, r = authorize(ex, mcp["client_id"], challenge)
    assert r.status_code == 200 and r.json()["client_name"] == "Claude" and r.json()["allowed"] is True
    bad = {**params, "redirect_uri": "https://evil.example/cb"}
    assert ex.get(f"{API}/oauth/request?{urlencode(bad)}").status_code == 400
    assert ex.post(f"{API}/oauth/approve", json=bad).status_code == 400  # never redirect to an unknown URI
    no_pkce = {**params, "code_challenge_method": "plain"}
    assert ex.get(f"{API}/oauth/request?{urlencode(no_pkce)}").status_code == 400
    other = {**params, "resource": "https://elsewhere.example/mcp"}
    assert ex.get(f"{API}/oauth/request?{urlencode(other)}").status_code == 400
    r = ex.post(f"{API}/oauth/deny", json=params)
    assert "error=access_denied" in r.json()["redirect"]
    # The consent page needs a signed-in person
    from tests.conftest import Client

    with Client(ex.app, base_url="http://testserver") as anon:
        assert anon.get(f"{API}/oauth/request?{urlencode(params)}").status_code == 401


def test_people_below_the_minimum_role_cannot_connect(mcp):
    viewer = mcp["viewer@test.com"]
    verifier, challenge = pkce()
    params, r = authorize(viewer, mcp["client_id"], challenge)
    assert r.json()["allowed"] is False
    redirect = viewer.post(f"{API}/oauth/approve", json=params).json()["redirect"]
    assert "error=access_denied" in redirect and "code=" not in redirect


def test_code_needs_the_right_verifier_and_works_once(mcp):
    ex, cid = mcp["ex@test.com"], mcp["client_id"]
    verifier, challenge = pkce()
    params, _ = authorize(ex, cid, challenge)
    code = parse_qs(urlparse(ex.post(f"{API}/oauth/approve", json=params).json()["redirect"]).query)["code"][
        0
    ]
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK, "client_id": cid}
    r = ex.post("/oauth/token", data={**form, "code_verifier": pkce()[0]})
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    # A failed attempt uses the code up
    assert ex.post("/oauth/token", data={**form, "code_verifier": verifier}).status_code == 400
    tokens = connect(ex, cid)
    assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600
    assert (
        ex.post(
            "/oauth/token", data={**form, "client_id": "someone-else", "code_verifier": verifier}
        ).status_code
        == 400
    )


def test_access_tokens_only_work_on_mcp(mcp):
    tokens = connect(mcp["ex@test.com"], mcp["client_id"])
    from tests.conftest import Client

    with Client(mcp["owner"].app, base_url="http://testserver") as anon:
        r = anon.get(f"{API}/catalog/datasets", headers={"Authorization": f"Bearer {tokens['access_token']}"})
        assert r.status_code == 401
        assert rpc(anon, tokens["access_token"], "ping").json()["result"] == {}


def test_refresh_rotates_and_reuse_cuts_the_app_off(mcp):
    ex, cid = mcp["ex@test.com"], mcp["client_id"]
    first = connect(ex, cid)
    r = ex.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": first["refresh_token"], "client_id": cid},
    )
    assert r.status_code == 200
    second = r.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert rpc(ex, first["access_token"], "ping").status_code == 401  # the old pair is gone
    assert rpc(ex, second["access_token"], "ping").status_code == 200
    # The old refresh token is used again: everything for this app and person is revoked
    r = ex.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": first["refresh_token"], "client_id": cid},
    )
    assert r.status_code == 400
    assert rpc(ex, second["access_token"], "ping").status_code == 401


def test_revoke_and_connected_apps(mcp):
    ex, cid = mcp["ex@test.com"], mcp["client_id"]
    tokens = connect(ex, cid)
    apps = ex.get(f"{API}/me/connected-apps").json()
    assert [a["name"] for a in apps] == ["Claude"]
    listed = mcp["admin@test.com"].get(f"{API}/admin/mcp").json()["connections"]
    assert listed[0]["email"] == "ex@test.com"
    assert ex.delete(f"{API}/me/connected-apps/{cid}").status_code == 204
    assert rpc(ex, tokens["access_token"], "ping").status_code == 401
    assert ex.get(f"{API}/me/connected-apps").json() == []
    tokens = connect(ex, cid)
    assert ex.post("/oauth/revoke", data={"token": tokens["access_token"]}).status_code == 200
    assert rpc(ex, tokens["access_token"], "ping").status_code == 401
    # Turning the server off stops existing tokens too
    tokens = connect(ex, cid)
    mcp["admin@test.com"].put(f"{API}/admin/mcp", json={"enabled": False})
    assert rpc(ex, tokens["access_token"], "ping").status_code == 404


# ------------------------------------------------------------------ MCP
def test_initialize_and_tools(mcp):
    token = connect(mcp["ex@test.com"], mcp["client_id"])["access_token"]
    ex = mcp["ex@test.com"]
    r = rpc(
        ex,
        token,
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}},
    )
    res = r.json()["result"]
    assert res["protocolVersion"] == "2025-06-18" and "tools" in res["capabilities"]
    r = ex.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 202
    tools = rpc(ex, token, "tools/list").json()["result"]["tools"]
    assert {t["name"] for t in tools} == {
        "search",
        "describe_dataset",
        "run_query",
        "get_dashboard",
        "query_dashboard_tile",
        "get_kpis",
    }
    assert all(t["annotations"]["readOnlyHint"] for t in tools)
    assert rpc(ex, token, "nope").json()["error"]["code"] == -32601


def test_tools_apply_row_level_security(mcp):
    ex = mcp["ex@test.com"]  # region KSA only
    token = connect(ex, mcp["client_id"])["access_token"]
    found = call(ex, token, "search", query="sales")["structuredContent"]
    assert [d["name"] for d in found["dashboards"]] == ["Sales"] and found["datasets"][0]["id"] == mcp["ds"][
        "id"
    ]
    desc = call(ex, token, "describe_dataset", dataset_id=mcp["ds"]["id"])["structuredContent"]
    assert {f["name"] for f in desc["fields"]} >= {"region", "amount"}
    assert '"KSA"' in desc["overview"] and "UAE" not in desc["overview"]
    q = call(
        ex,
        token,
        "run_query",
        dataset_id=mcp["ds"]["id"],
        dimensions=[{"field": "region"}],
        measures=[{"field": "amount", "agg": "sum"}],
        sort=[{"field": "amount", "dir": "desc"}],
    )
    data = q["structuredContent"]
    assert data["rows"] == [["KSA", 300.0]] and data["restricted_by_row_security"] is True
    assert json.loads(q["content"][0]["text"])["rows"] == [["KSA", 300.0]]
    tile = call(ex, token, "query_dashboard_tile", dashboard_id=mcp["dash"]["id"], tile_id="c1")[
        "structuredContent"
    ]
    assert tile["rows"] == [["KSA", 300.0]]
    dash = call(ex, token, "get_dashboard", dashboard_id=mcp["dash"]["id"])["structuredContent"]
    assert dash["tabs"][0]["tiles"][0]["id"] == "c1" and dash["url"].endswith(
        f"/dashboards/{mcp['dash']['id']}"
    )
    assert call(ex, token, "get_kpis", dashboard_id=mcp["dash"]["id"])["structuredContent"]["kpis"] == []
    bad = call(ex, token, "run_query", dataset_id=mcp["ds"]["id"], measures=[{"field": "nope"}])
    assert bad["isError"] is True
    with SessionLocal() as s:
        rows = s.query(AuditLog).filter_by(action="mcp.tool").all()
        assert len(rows) == 7 and {r.details["tool"] for r in rows} >= {"search", "run_query"}
        assert all(r.user_email == "ex@test.com" for r in rows)


def test_tools_hide_what_the_person_cannot_open(mcp):
    owner = mcp["owner"]
    r = owner.put(f"{API}/datasets/{mcp['ds']['id']}/access", json={"everyone": False})
    assert r.status_code == 200
    r = owner.put(
        f"{API}/dashboards/{mcp['dash']['id']}/sharing", json={"everyone": "none", "users": [], "groups": []}
    )
    assert r.status_code == 200, r.text
    ex = mcp["ex@test.com"]
    token = connect(ex, mcp["client_id"])["access_token"]
    found = call(ex, token, "search")["structuredContent"]
    assert found["dashboards"] == [] and found["datasets"] == [] and found["charts"] == []
    assert call(ex, token, "run_query", dataset_id=mcp["ds"]["id"], measures=[{"field": "amount"}])["isError"]
    assert call(ex, token, "get_dashboard", dashboard_id=mcp["dash"]["id"])["isError"]
    assert call(ex, token, "query_dashboard_tile", dashboard_id=mcp["dash"]["id"], tile_id="c1")["isError"]


def test_raw_rows_need_explorer(mcp):
    mcp["admin@test.com"].put(f"{API}/admin/mcp", json={"enabled": True, "min_role": "viewer"})
    viewer = mcp["viewer@test.com"]
    token = connect(viewer, mcp["client_id"])["access_token"]
    r = call(viewer, token, "run_query", dataset_id=mcp["ds"]["id"], mode="rows", fields=["region"])
    assert r["isError"] and "Explorer" in r["content"][0]["text"]
    ex = mcp["ex@test.com"]
    token = connect(ex, mcp["client_id"])["access_token"]
    rows = call(ex, token, "run_query", dataset_id=mcp["ds"]["id"], mode="rows", fields=["region", "branch"])
    assert rows["structuredContent"]["rows"] == [["KSA", "Riyadh"]]


def test_browser_origins_are_checked(mcp):
    token = connect(mcp["ex@test.com"], mcp["client_id"])["access_token"]
    r = rpc(mcp["ex@test.com"], token, "ping", Origin="https://evil.example")
    assert r.status_code == 403
    assert rpc(mcp["ex@test.com"], token, "ping", Origin="http://localhost:8080").status_code == 200
