"""Map boundary uploads for Geo Map charts."""

import json

from app.models import Role
from tests.conftest import Client, make_user
from tests.test_model import upload_dataset

API = "/api/v1"


def square(x: float, y: float) -> dict:
    return {"type": "Polygon", "coordinates": [[[x, y], [x + 1, y], [x + 1, y + 1], [x, y + 1], [x, y]]]}


GEO = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"NAME_1": "Dubai", "id": 1}, "geometry": square(55, 25)},
        {"type": "Feature", "properties": {"NAME_1": "Sharjah", "id": 2}, "geometry": square(56, 25)},
        {
            "type": "Feature",
            "properties": {"NAME_1": "Somewhere"},
            "geometry": {"type": "Point", "coordinates": [1, 2]},
        },
    ],
}


def upload(client, data=GEO, name="Emirates", **form):
    raw = data if isinstance(data, bytes) else json.dumps(data).encode()
    return client.post(f"{API}/boundaries", files={"file": ("e.geojson", raw)}, data={"name": name, **form})


def test_upload_list_and_read(creator_client):
    r = upload(creator_client)
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["name_property"] == "NAME_1" and b["features"] == 2 and set(b["properties"]) == {"NAME_1", "id"}
    assert [x["name"] for x in creator_client.get(f"{API}/boundaries").json()] == ["Emirates"]
    geo = creator_client.get(f"{API}/boundaries/{b['id']}").json()
    assert len(geo["geojson"]["features"]) == 2  # points are dropped


def test_bad_files_are_refused(creator_client):
    assert upload(creator_client, b"not json").status_code == 422
    assert upload(creator_client, {"type": "Topology"}).status_code == 422
    only_points = {"type": "FeatureCollection", "features": [GEO["features"][2]]}
    assert upload(creator_client, only_points).status_code == 422
    assert upload(creator_client, name_property="nope").status_code == 422


def test_permissions_and_delete(creator_client):
    b = upload(creator_client).json()
    make_user("viewer@test.com", Role.viewer)
    make_user("other@test.com", Role.creator)
    with Client(creator_client.app, base_url="http://testserver") as v:
        v.login("viewer@test.com")
        assert v.get(f"{API}/boundaries").status_code == 200  # anyone can use maps
        assert upload(v).status_code == 403
    with Client(creator_client.app, base_url="http://testserver") as o:
        o.login("other@test.com")
        assert (
            o.patch(f"{API}/boundaries/{b['id']}", json={"name": "x", "name_property": "id"}).status_code
            == 403
        )
        assert o.delete(f"{API}/boundaries/{b['id']}").status_code == 403
    r = creator_client.patch(
        f"{API}/boundaries/{b['id']}", json={"name": "UAE emirates", "name_property": "id"}
    )
    assert r.status_code == 200 and r.json()["name_property"] == "id"
    assert (
        creator_client.patch(
            f"{API}/boundaries/{b['id']}", json={"name": "x", "name_property": "zz"}
        ).status_code
        == 422
    )
    # A map used by a chart cannot be deleted
    ds = upload_dataset(creator_client, "Sales", (["emirate", "amount"], [["Dubai", 1.0]]))
    chart = creator_client.post(
        f"{API}/charts",
        json={
            "name": "Map",
            "dataset_id": ds["id"],
            "config": {"type": "geomap", "options": {"boundary": b["id"]}},
            "query": {},
        },
    ).json()
    assert creator_client.delete(f"{API}/boundaries/{b['id']}").status_code == 409
    creator_client.delete(f"{API}/charts/{chart['id']}")
    assert creator_client.delete(f"{API}/boundaries/{b['id']}").status_code == 204
    assert creator_client.get(f"{API}/boundaries/{b['id']}").status_code == 404
