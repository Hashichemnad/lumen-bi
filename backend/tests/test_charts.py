"""Saved charts: create, open, change, copy, delete, and who may do each."""

import pytest

from app.db import SessionLocal
from app.models import AuditLog, Role
from tests.conftest import Client, make_user
from tests.test_model import upload_dataset

API = "/api/v1"
DATA = (["region", "amount"], [["UAE", 1.0], ["KSA", 2.0]])
CONFIG = {"type": "bar", "shelves": {"columns": [{"id": "p1", "field": "region"}], "rows": []}}
QUERY = {"dimensions": [{"field": "region", "key": "p1"}], "measures": [{"field": "amount", "key": "p2"}]}


def login(app, email):
    c = Client(app, base_url="http://testserver")
    c.__enter__()
    c.login(email)
    return c


@pytest.fixture
def world(creator_client):
    ds = upload_dataset(creator_client, "Sales", DATA)
    private = upload_dataset(creator_client, "Private", DATA, filename="p.csv")
    creator_client.put(f"{API}/datasets/{ds['id']}/access", json={"everyone": True})
    for email, role in [
        ("ex@test.com", Role.explorer),
        ("ex2@test.com", Role.explorer),
        ("v@test.com", Role.viewer),
    ]:
        make_user(email, role)
    make_user("admin@test.com", Role.admin)
    clients = {
        e: login(creator_client.app, e)
        for e in ["ex@test.com", "ex2@test.com", "v@test.com", "admin@test.com"]
    }
    yield {"ds": ds, "private": private, "creator": creator_client, **clients}
    for c in clients.values():
        c.__exit__(None, None, None)


def body(ds_id, **kw):
    return {"name": "Sales by region", "dataset_id": ds_id, "config": CONFIG, "query": QUERY, **kw}


def test_create_open_update_delete(world):
    ex = world["ex@test.com"]
    r = ex.post(f"{API}/charts", json=body(world["ds"]["id"]))
    assert r.status_code == 201, r.text
    chart = r.json()
    assert chart["dataset_name"] == "Sales" and chart["owner_name"] == "ex" and chart["can_edit"] is True
    assert chart["query"] == QUERY and chart["config"] == CONFIG
    assert [c["id"] for c in ex.get(f"{API}/charts").json()] == [chart["id"]]
    r = ex.put(
        f"{API}/charts/{chart['id']}", json=body(world["ds"]["id"], name="Renamed", config={"type": "line"})
    )
    assert r.status_code == 200 and r.json()["name"] == "Renamed" and r.json()["config"] == {"type": "line"}
    assert ex.delete(f"{API}/charts/{chart['id']}").status_code == 204
    assert ex.get(f"{API}/charts/{chart['id']}").status_code == 404
    with SessionLocal() as db:
        assert {a.action for a in db.query(AuditLog).filter(AuditLog.action.like("chart.%"))} == {
            "chart.create",
            "chart.delete",
        }


def test_others_cannot_see_or_change_a_chart(world):
    chart = world["ex@test.com"].post(f"{API}/charts", json=body(world["ds"]["id"])).json()
    other = world["ex2@test.com"]
    assert other.get(f"{API}/charts").json() == []
    assert other.get(f"{API}/charts/{chart['id']}").status_code == 404
    assert other.put(f"{API}/charts/{chart['id']}", json=body(world["ds"]["id"])).status_code == 404
    assert other.delete(f"{API}/charts/{chart['id']}").status_code == 404
    assert other.post(f"{API}/charts/{chart['id']}/copy").status_code == 404
    admin = world["admin@test.com"]
    assert [c["id"] for c in admin.get(f"{API}/charts").json()] == [chart["id"]]
    assert admin.get(f"{API}/charts/{chart['id']}").json()["can_edit"] is True


def test_viewers_cannot_make_charts(world):
    r = world["v@test.com"].post(f"{API}/charts", json=body(world["ds"]["id"]))
    assert r.status_code == 403
    assert world["v@test.com"].get(f"{API}/charts").json() == []


def test_dataset_must_be_usable_and_query_valid(world):
    ex = world["ex@test.com"]
    assert ex.post(f"{API}/charts", json=body(world["private"]["id"])).status_code == 422
    assert ex.post(f"{API}/charts", json=body(999)).status_code == 422
    r = ex.post(f"{API}/charts", json=body(world["ds"]["id"], query={"limit": -5}))
    assert r.status_code == 422 and "query" in r.json()["detail"]
    r = ex.post(f"{API}/charts", json=body(world["ds"]["id"], config={"x": "y" * 300_000}))
    assert r.status_code == 422 and "too large" in r.json()["detail"]


def test_copy(world):
    admin = world["admin@test.com"]
    chart = world["ex@test.com"].post(f"{API}/charts", json=body(world["ds"]["id"])).json()
    r = admin.post(f"{API}/charts/{chart['id']}/copy")
    assert r.status_code == 201 and r.json()["name"] == "Sales by region (copy)"
    assert r.json()["owner_name"] == "admin"


def test_dataset_with_charts_cannot_be_deleted(world):
    world["ex@test.com"].post(f"{API}/charts", json=body(world["ds"]["id"]))
    r = world["creator"].delete(f"{API}/datasets/{world['ds']['id']}")
    assert r.status_code == 409 and "chart" in r.json()["detail"]


def test_charts_require_login(client):
    assert client.get(f"{API}/charts").status_code == 401
