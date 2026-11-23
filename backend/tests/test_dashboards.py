"""Dashboards: layout checks, sharing levels, viewers running charts through a dashboard with their own
row-level security, what a viewer may change (filters, drill) and what they may not."""

import csv
import io

import pytest

from app.db import SessionLocal
from app.models import Group, Role, User
from tests.conftest import Client, make_user
from tests.test_model import upload_dataset

API = "/api/v1"
SALES = (
    ["region", "branch", "amount", "order_date"],
    [
        ["UAE", "Dubai", 100.0, "2026-01-05"],
        ["UAE", "Abu Dhabi", 200.0, "2026-02-05"],
        ["KSA", "Riyadh", 300.0, "2026-02-10"],
        ["QAT", "Doha", 400.0, "2026-03-01"],
    ],
)
QUERY = {"dimensions": [{"field": "region", "key": "d1"}], "measures": [{"field": "amount", "key": "m1"}]}


def login(app, email):
    c = Client(app, base_url="http://testserver")
    c.__enter__()
    c.login(email)
    return c


def config(chart_id: int, **options) -> dict:
    return {
        "tabs": [
            {
                "id": "t1",
                "name": "Overview",
                "items": [
                    {
                        "id": "c1",
                        "kind": "chart",
                        "chart_id": chart_id,
                        "desktop": {"x": 0, "y": 0, "w": 6, "h": 8},
                    },
                    {
                        "id": "x1",
                        "kind": "text",
                        "text": "# Sales",
                        "desktop": {"x": 6, "y": 0, "w": 6, "h": 2},
                    },
                ],
            }
        ],
        "filters": [],
        "options": options,
    }


@pytest.fixture
def world(creator_client):
    ds = upload_dataset(creator_client, "Sales", SALES)
    r = creator_client.post(
        f"{API}/datasets/{ds['id']}/security/rules",
        json={"kind": "attribute", "config": {"field": "region", "attribute": "region"}},
    )
    assert r.status_code == 201
    creator_client.post(
        f"{API}/datasets/{ds['id']}/hierarchies", json={"name": "Place", "levels": ["region", "branch"]}
    )
    make_user("admin@test.com", Role.admin)
    make_user("viewer@test.com", Role.viewer, attributes={"region": "UAE"})
    make_user("ex@test.com", Role.explorer, attributes={"region": "KSA"})
    make_user("nobody@test.com", Role.viewer)
    app = creator_client.app
    clients = {
        e: login(app, e) for e in ["admin@test.com", "viewer@test.com", "ex@test.com", "nobody@test.com"]
    }
    # The creator owns the chart and the dashboard; the dataset itself is NOT shared with anyone
    chart = creator_client.post(
        f"{API}/charts",
        json={"name": "By region", "dataset_id": ds["id"], "config": {"type": "bar"}, "query": QUERY},
    ).json()
    dash = creator_client.post(f"{API}/dashboards", json={"name": "Sales", "config": config(chart["id"])})
    assert dash.status_code == 201, dash.text
    yield {"ds": ds, "chart": chart, "dash": dash.json(), "owner": creator_client, **clients}
    for c in clients.values():
        c.__exit__(None, None, None)


def share(world, **body):
    r = world["owner"].put(f"{API}/dashboards/{world['dash']['id']}/sharing", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def tile(client, world, **body):
    return client.post(f"{API}/dashboards/{world['dash']['id']}/query", json={"item": "c1", **body})


def user_id(email):
    with SessionLocal() as db:
        return db.query(User).filter_by(email=email).one().id


# ------------------------------------------------------------------ basics
def test_dashboard_detail_contains_charts_and_dataset_fields(world):
    d = world["owner"].get(f"{API}/dashboards/{world['dash']['id']}").json()
    assert d["level"] == "manage" and d["can_edit"] and d["can_manage"]
    assert str(world["chart"]["id"]) in {str(k) for k in d["charts"]}
    ds = next(iter(d["datasets"].values()))
    assert {f["name"] for f in ds["fields"]} >= {"region", "amount"} and ds["hierarchies"][0]["levels"] == [
        "region",
        "branch",
    ]
    assert d["config"]["options"] == {"cross_filter": True, "view_data": True, "phone_layout": False}


def test_not_shared_means_not_visible(world):
    for email in ("viewer@test.com", "ex@test.com"):
        c = world[email]
        assert c.get(f"{API}/dashboards").json() == []
        assert c.get(f"{API}/dashboards/{world['dash']['id']}").status_code == 404
        assert tile(c, world).status_code == 404


def test_viewer_runs_charts_through_the_dashboard_with_their_own_rows(world):
    share(world, everyone="view")
    viewer = world["viewer@test.com"]
    # The viewer has no access to the dataset itself...
    assert (
        viewer.post(f"{API}/query", json={"dataset": world["ds"]["id"], "measures": ["amount"]}).status_code
        == 404
    )
    # ...but sees the chart on the dashboard, filtered to their region
    r = tile(viewer, world)
    assert r.status_code == 200 and r.json()["rows"] == [["UAE", 300.0]] and r.json()["restricted"]
    assert tile(world["ex@test.com"], world).json()["rows"] == [["KSA", 300.0]]
    assert tile(world["nobody@test.com"], world).json()["rows"] == []
    assert tile(world["admin@test.com"], world).json()["rows"] == [
        ["KSA", 300.0],
        ["QAT", 400.0],
        ["UAE", 300.0],
    ]
    listed = viewer.get(f"{API}/dashboards").json()
    assert listed[0]["level"] == "view" and listed[0]["tiles"] == 2


def test_filters_narrow_but_never_widen(world):
    share(world, everyone="view")
    viewer = world["viewer@test.com"]
    r = tile(viewer, world, filters=[{"field": "branch", "op": "eq", "value": "Dubai"}])
    assert r.json()["rows"] == [["UAE", 100.0]]
    r = tile(viewer, world, filters=[{"field": "region", "op": "in", "values": ["KSA", "QAT", "UAE"]}])
    assert r.json()["rows"] == [["UAE", 300.0]]
    r = tile(viewer, world, filters=[{"field": "region') OR TRUE --", "op": "is_null"}])
    assert r.status_code == 400
    r = tile(viewer, world, filters=[{"field": "region", "op": "nope"}])
    assert r.status_code == 422


def test_drill_only_along_dates_and_hierarchies(world):
    share(world, everyone="view")
    viewer = world["viewer@test.com"]
    r = tile(
        viewer,
        world,
        drill=[{"key": "d1", "field": "branch"}],
        filters=[{"field": "region", "op": "eq", "value": "UAE"}],
    )
    assert r.status_code == 200 and sorted(x[0] for x in r.json()["rows"]) == ["Abu Dhabi", "Dubai"]
    r = tile(viewer, world, drill=[{"key": "d1", "field": "amount"}])
    assert r.status_code == 403
    r = tile(viewer, world, drill=[{"key": "nope", "field": "branch"}])
    assert r.status_code == 403
    assert tile(viewer, world, item="x1").status_code == 404
    assert tile(viewer, world, item="zz").status_code == 404


def test_view_data_rows_follow_the_dashboard_setting(world):
    share(world, everyone="view")
    viewer = world["viewer@test.com"]
    r = tile(viewer, world, rows=True)
    assert r.status_code == 200 and sorted(x[1] for x in r.json()["rows"]) == ["Abu Dhabi", "Dubai"]
    cfg = config(world["chart"]["id"], view_data=False)
    world["owner"].put(f"{API}/dashboards/{world['dash']['id']}", json={"name": "Sales", "config": cfg})
    assert tile(viewer, world, rows=True).status_code == 403
    assert tile(world["owner"], world, rows=True).status_code == 200


def test_filter_values_and_export(world):
    share(world, everyone="view")
    viewer = world["viewer@test.com"]
    did, dsid = world["dash"]["id"], world["ds"]["id"]
    vals = viewer.get(f"{API}/dashboards/{did}/values", params={"dataset": dsid, "field": "branch"}).json()
    assert sorted(vals["values"]) == ["Abu Dhabi", "Dubai"]
    other = upload_dataset(world["owner"], "Other", SALES, filename="o.csv")
    assert (
        viewer.get(
            f"{API}/dashboards/{did}/values", params={"dataset": other["id"], "field": "branch"}
        ).status_code
        == 404
    )
    # Viewers cannot export; explorers can, with their own rows
    assert viewer.post(f"{API}/dashboards/{did}/export", json={"item": "c1"}).status_code == 403
    r = world["ex@test.com"].post(f"{API}/dashboards/{did}/export", json={"item": "c1"})
    assert r.status_code == 200
    assert list(csv.reader(io.StringIO(r.text.lstrip("﻿"))))[1:] == [["KSA", "300.0"]]


# ------------------------------------------------------------------ editing and sharing
def test_edit_share_lets_explorers_change_layout_but_not_share_or_delete(world):
    did = world["dash"]["id"]
    share(
        world,
        users=[
            {"id": user_id("ex@test.com"), "level": "edit"},
            {"id": user_id("viewer@test.com"), "level": "edit"},
        ],
    )
    ex, viewer = world["ex@test.com"], world["viewer@test.com"]
    d = ex.get(f"{API}/dashboards/{did}").json()
    assert d["level"] == "edit" and d["can_edit"] and not d["can_manage"]
    r = ex.put(f"{API}/dashboards/{did}", json={"name": "Renamed", "config": d["config"]})
    assert r.status_code == 200 and r.json()["name"] == "Renamed"
    assert ex.put(f"{API}/dashboards/{did}/sharing", json={"everyone": "view"}).status_code == 403
    assert ex.get(f"{API}/dashboards/{did}/sharing").status_code == 403
    assert ex.delete(f"{API}/dashboards/{did}").status_code == 403
    # A viewer given "edit" still only views
    assert viewer.get(f"{API}/dashboards/{did}").json()["level"] == "view"
    assert viewer.put(f"{API}/dashboards/{did}", json={"name": "x", "config": d["config"]}).status_code == 403


def test_editors_can_only_add_charts_they_own(world):
    did = world["dash"]["id"]
    share(world, users=[{"id": user_id("ex@test.com"), "level": "edit"}])
    ex = world["ex@test.com"]
    world["owner"].put(f"{API}/datasets/{world['ds']['id']}/access", json={"everyone": True})
    other = (
        world["owner"]
        .post(
            f"{API}/charts",
            json={"name": "Owner only", "dataset_id": world["ds"]["id"], "config": {}, "query": QUERY},
        )
        .json()
    )
    cfg = config(world["chart"]["id"])
    cfg["tabs"][0]["items"].append(
        {"id": "c2", "kind": "chart", "chart_id": other["id"], "desktop": {"x": 0, "y": 8, "w": 6, "h": 6}}
    )
    r = ex.put(f"{API}/dashboards/{did}", json={"name": "Sales", "config": cfg})
    assert r.status_code == 403 and "Owner only" in r.json()["detail"]
    mine = ex.post(
        f"{API}/charts", json={"name": "Mine", "dataset_id": world["ds"]["id"], "config": {}, "query": QUERY}
    ).json()
    cfg["tabs"][0]["items"][-1]["chart_id"] = mine["id"]
    assert ex.put(f"{API}/dashboards/{did}", json={"name": "Sales", "config": cfg}).status_code == 200


@pytest.mark.parametrize(
    "change, message",
    [
        (
            lambda c: c["tabs"][0]["items"].append(
                {
                    "id": "w",
                    "kind": "web",
                    "url": "http://insecure.example",
                    "desktop": {"x": 0, "y": 9, "w": 4, "h": 4},
                }
            ),
            "https",
        ),
        (
            lambda c: c["tabs"][0]["items"].append(
                {
                    "id": "i",
                    "kind": "image",
                    "url": "javascript:alert(1)",
                    "desktop": {"x": 0, "y": 9, "w": 4, "h": 4},
                }
            ),
            "https",
        ),
        (lambda c: c["tabs"][0]["items"][0]["desktop"].update(x=10, w=6), "outside"),
        (
            lambda c: c["tabs"][0]["items"].append(
                {"id": "c1", "kind": "text", "desktop": {"x": 0, "y": 0, "w": 1, "h": 1}}
            ),
            "unique",
        ),
        (lambda c: c.update(tabs=[]), "tabs"),
        (
            lambda c: c["tabs"][0]["items"].append(
                {"id": "q", "kind": "script", "desktop": {"x": 0, "y": 0, "w": 1, "h": 1}}
            ),
            "Unknown",
        ),
        (
            lambda c: c.update(filters=[{"id": "f1", "label": "Region", "kind": "values", "targets": []}]),
            "applies",
        ),
    ],
)
def test_layout_is_checked(world, change, message):
    cfg = config(world["chart"]["id"])
    change(cfg)
    r = world["owner"].put(f"{API}/dashboards/{world['dash']['id']}", json={"name": "Sales", "config": cfg})
    assert r.status_code == 422 and message in r.json()["detail"], r.text


def test_sharing_with_groups_and_cleanup(world, db):
    group = Group(name="Sales team")
    db.add(group)
    db.flush()
    member = db.query(User).filter_by(email="viewer@test.com").one()
    member.groups.append(group)
    db.commit()
    gid = group.id
    shared = share(world, groups=[{"id": gid, "level": "view"}])
    assert shared["groups"] == [{"id": gid, "level": "view", "name": "Sales team"}]
    assert tile(world["viewer@test.com"], world).status_code == 200
    assert tile(world["ex@test.com"], world).status_code == 404
    assert world["admin@test.com"].delete(f"{API}/groups/{gid}").status_code == 204
    assert world["owner"].get(f"{API}/dashboards/{world['dash']['id']}/sharing").json()["groups"] == []


def test_favorites_copy_and_delete(world):
    share(world, everyone="view")
    ex, did = world["ex@test.com"], world["dash"]["id"]
    assert ex.put(f"{API}/dashboards/{did}/favorite").status_code == 204
    assert ex.put(f"{API}/dashboards/{did}/favorite").status_code == 204
    assert ex.get(f"{API}/dashboards").json()[0]["favorite"] is True
    assert world["owner"].get(f"{API}/dashboards").json()[0]["favorite"] is False
    copy = ex.post(f"{API}/dashboards/{did}/copy")
    assert copy.status_code == 201 and copy.json()["level"] == "manage"
    assert world["viewer@test.com"].post(f"{API}/dashboards/{did}/copy").status_code == 403
    # A chart on a dashboard cannot be deleted until the dashboards are gone
    r = world["owner"].delete(f"{API}/charts/{world['chart']['id']}")
    assert r.status_code == 409 and "dashboard" in r.json()["detail"]
    assert ex.delete(f"{API}/dashboards/{copy.json()['id']}").status_code == 204
    assert world["owner"].delete(f"{API}/dashboards/{did}").status_code == 204
    assert world["owner"].delete(f"{API}/charts/{world['chart']['id']}").status_code == 204


def test_viewers_cannot_create_dashboards(world):
    r = world["viewer@test.com"].post(
        f"{API}/dashboards", json={"name": "x", "config": config(world["chart"]["id"])}
    )
    assert r.status_code == 403
