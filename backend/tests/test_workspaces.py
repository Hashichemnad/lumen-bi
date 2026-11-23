"""Workspaces: who creates them, what each member role allows on the dashboards and charts in them, that
membership never widens row-level security, member management, and what happens on delete."""

import pytest

from app.db import SessionLocal
from app.models import AuditLog, Group, Role, User
from tests.conftest import make_user
from tests.test_dashboards import QUERY, SALES, config, login
from tests.test_model import upload_dataset

API = "/api/v1"


@pytest.fixture
def ws(creator_client):
    """Ops workspace: ex = editor, viewer = 'editor' (capped to viewer by their role), the Managers group
    (mgr) = manager, creator = editor; nobody is not a member."""
    ds = upload_dataset(creator_client, "Sales", SALES)
    creator_client.post(
        f"{API}/datasets/{ds['id']}/security/rules",
        json={"kind": "attribute", "config": {"field": "region", "attribute": "region"}},
    )
    make_user("admin@test.com", Role.admin)
    make_user("ex@test.com", Role.explorer, attributes={"region": "KSA"})
    make_user("viewer@test.com", Role.viewer, attributes={"region": "UAE"})
    make_user("mgr@test.com", Role.creator, attributes={"region": "QAT"})
    make_user("nobody@test.com", Role.explorer, attributes={"region": "UAE"})
    with SessionLocal() as s:
        g = Group(name="Managers")
        g.members.append(s.query(User).filter_by(email="mgr@test.com").one())
        s.add(g)
        s.commit()
        group_id = g.id
        ids = {u.email: u.id for u in s.query(User)}
    app = creator_client.app
    c = {
        e: login(app, e)
        for e in ["admin@test.com", "ex@test.com", "viewer@test.com", "mgr@test.com", "nobody@test.com"]
    }
    admin = c["admin@test.com"]
    w = admin.post(f"{API}/workspaces", json={"name": "Operations", "description": "Ops team"}).json()
    r = admin.put(
        f"{API}/workspaces/{w['id']}/members",
        json=[
            {"principal": "user", "id": ids["ex@test.com"], "role": "editor"},
            {"principal": "user", "id": ids["viewer@test.com"], "role": "editor"},
            {"principal": "user", "id": ids["creator@test.com"], "role": "editor"},
            {"principal": "group", "id": group_id, "role": "manager"},
        ],
    )
    assert r.status_code == 200, r.text
    chart = creator_client.post(
        f"{API}/charts",
        json={"name": "By region", "dataset_id": ds["id"], "config": {"type": "bar"}, "query": QUERY},
    ).json()
    dash = creator_client.post(
        f"{API}/dashboards", json={"name": "Ops KPIs", "config": config(chart["id"])}
    ).json()
    yield {
        "ws": w,
        "ds": ds,
        "chart": chart,
        "dash": dash,
        "owner": creator_client,
        "ids": ids,
        "group": group_id,
        **c,
    }
    for x in c.values():
        x.__exit__(None, None, None)


def move(client, kind, item_id, workspace_id):
    return client.put(f"{API}/{kind}/{item_id}/workspace", json={"workspace_id": workspace_id})


def test_only_admins_create_and_delete_workspaces(ws):
    for email in ("ex@test.com", "viewer@test.com", "mgr@test.com"):
        assert ws[email].post(f"{API}/workspaces", json={"name": "Mine"}).status_code == 403
        assert ws[email].delete(f"{API}/workspaces/{ws['ws']['id']}").status_code == 403
    assert ws["owner"].post(f"{API}/workspaces", json={"name": "Mine"}).status_code == 403
    assert ws["admin@test.com"].post(f"{API}/workspaces", json={"name": "operations"}).status_code == 409


def test_members_see_their_role(ws):
    def roles(email):
        return {w["name"]: w["my_role"] for w in ws[email].get(f"{API}/workspaces").json()}

    assert roles("ex@test.com") == {"Operations": "editor"}
    assert roles("viewer@test.com") == {"Operations": "viewer"}  # the Viewer role never edits
    assert roles("mgr@test.com") == {"Operations": "manager"}  # through the group
    assert roles("nobody@test.com") == {}
    assert roles("admin@test.com") == {"Operations": "manager"}
    assert ws["nobody@test.com"].get(f"{API}/workspaces/{ws['ws']['id']}").status_code == 404
    assert ws["nobody@test.com"].get(f"{API}/workspaces/{ws['ws']['id']}/members").status_code == 404


def test_dashboard_access_through_the_workspace(ws):
    dash_id, wid = ws["dash"]["id"], ws["ws"]["id"]
    # Before the move only the owner sees it
    assert ws["ex@test.com"].get(f"{API}/dashboards/{dash_id}").status_code == 404
    # Only a manager of the dashboard can move it, and only into a workspace where they can add items
    assert move(ws["ex@test.com"], "dashboards", dash_id, wid).status_code == 404
    r = move(ws["owner"], "dashboards", dash_id, wid)
    assert r.status_code == 200, r.text
    assert r.json()["workspace_name"] == "Operations"

    ex = ws["ex@test.com"].get(f"{API}/dashboards/{dash_id}")
    assert ex.status_code == 200 and ex.json()["level"] == "edit"
    body = {"name": "Ops KPIs", "config": ex.json()["config"]}
    assert ws["ex@test.com"].put(f"{API}/dashboards/{dash_id}", json=body).status_code == 200
    assert ws["ex@test.com"].delete(f"{API}/dashboards/{dash_id}").status_code == 403
    assert ws["ex@test.com"].get(f"{API}/dashboards/{dash_id}/sharing").status_code == 403
    assert move(ws["ex@test.com"], "dashboards", dash_id, None).status_code == 403

    viewer = ws["viewer@test.com"].get(f"{API}/dashboards/{dash_id}")
    assert viewer.status_code == 200 and viewer.json()["level"] == "view"
    assert ws["viewer@test.com"].put(f"{API}/dashboards/{dash_id}", json=body).status_code == 403

    assert ws["mgr@test.com"].get(f"{API}/dashboards/{dash_id}").json()["level"] == "manage"
    assert ws["mgr@test.com"].get(f"{API}/dashboards/{dash_id}/sharing").status_code == 200
    assert ws["nobody@test.com"].get(f"{API}/dashboards/{dash_id}").status_code == 404

    listed = {d["id"]: d for d in ws["ex@test.com"].get(f"{API}/dashboards").json()}
    assert listed[dash_id]["workspace_id"] == wid
    assert [d["id"] for d in ws["ex@test.com"].get(f"{API}/dashboards?workspace_id={wid}").json()] == [
        dash_id
    ]
    assert ws["ex@test.com"].get(f"{API}/dashboards?workspace_id=0").json() == []


def test_membership_never_widens_row_level_security(ws):
    move(ws["owner"], "dashboards", ws["dash"]["id"], ws["ws"]["id"])
    expected = {"viewer@test.com": ["UAE"], "ex@test.com": ["KSA"], "mgr@test.com": ["QAT"]}
    for email, regions in expected.items():
        r = ws[email].post(f"{API}/dashboards/{ws['dash']['id']}/query", json={"item": "c1"})
        assert r.status_code == 200, r.text
        assert [row[0] for row in r.json()["rows"]] == regions, email
    # Workspace members do not get the dataset itself
    q = {"dataset": ws["ds"]["id"], "measures": ["amount"]}
    assert ws["ex@test.com"].post(f"{API}/query", json=q).status_code in (403, 404)


def test_charts_in_a_workspace(ws):
    chart_id, wid = ws["chart"]["id"], ws["ws"]["id"]
    assert ws["ex@test.com"].get(f"{API}/charts/{chart_id}").status_code == 404
    assert move(ws["owner"], "charts", chart_id, wid).status_code == 200
    got = ws["ex@test.com"].get(f"{API}/charts/{chart_id}")
    assert got.status_code == 200 and got.json()["level"] == "edit" and got.json()["can_edit"]
    assert [c["id"] for c in ws["ex@test.com"].get(f"{API}/charts").json()] == [chart_id]
    assert ws["ex@test.com"].get(f"{API}/charts?workspace_id=0").json() == []
    # An editor changes it only with a dataset they can use; they cannot delete or move it
    body = {"name": "Renamed", "dataset_id": ws["ds"]["id"], "config": {"type": "bar"}, "query": QUERY}
    assert ws["ex@test.com"].put(f"{API}/charts/{chart_id}", json=body).status_code == 422
    ws["owner"].put(
        f"{API}/datasets/{ws['ds']['id']}/access",
        json={"everyone": False, "user_ids": [ws["ids"]["ex@test.com"]]},
    )
    assert ws["ex@test.com"].put(f"{API}/charts/{chart_id}", json=body).status_code == 200
    assert ws["ex@test.com"].delete(f"{API}/charts/{chart_id}").status_code == 403
    assert move(ws["ex@test.com"], "charts", chart_id, None).status_code == 403
    # Anyone who can open it may copy it (the copy is personal)
    copy = ws["ex@test.com"].post(f"{API}/charts/{chart_id}/copy")
    assert copy.status_code == 201 and copy.json()["workspace_id"] is None
    # Managers delete it (once no dashboard uses it)
    assert ws["mgr@test.com"].delete(f"{API}/charts/{chart_id}").status_code == 409
    assert ws["owner"].delete(f"{API}/dashboards/{ws['dash']['id']}").status_code == 204
    assert ws["mgr@test.com"].delete(f"{API}/charts/{chart_id}").status_code == 204
    assert ws["nobody@test.com"].get(f"{API}/charts/{copy.json()['id']}").status_code == 404


def test_workspace_charts_can_go_on_dashboards(ws):
    move(ws["owner"], "charts", ws["chart"]["id"], ws["ws"]["id"])
    r = ws["mgr@test.com"].post(
        f"{API}/dashboards", json={"name": "Mine", "config": config(ws["chart"]["id"])}
    )
    assert r.status_code == 201, r.text
    r = ws["nobody@test.com"].post(
        f"{API}/dashboards", json={"name": "Mine", "config": config(ws["chart"]["id"])}
    )
    assert r.status_code == 403


def test_create_directly_in_a_workspace(ws):
    wid = ws["ws"]["id"]
    body = {"name": "New", "config": config(ws["chart"]["id"]), "workspace_id": wid}
    r = ws["owner"].post(f"{API}/dashboards", json=body)
    assert r.status_code == 201 and r.json()["workspace_id"] == wid
    # Not an editor there
    other = ws["admin@test.com"].post(f"{API}/workspaces", json={"name": "Finance"}).json()
    assert (
        ws["owner"].post(f"{API}/dashboards", json={**body, "workspace_id": other["id"]}).status_code == 403
    )
    assert ws["owner"].post(f"{API}/dashboards", json={**body, "workspace_id": 999}).status_code == 403
    chart = {
        "name": "C",
        "dataset_id": ws["ds"]["id"],
        "config": {},
        "query": QUERY,
        "workspace_id": other["id"],
    }
    assert ws["owner"].post(f"{API}/charts", json=chart).status_code == 403
    assert ws["owner"].post(f"{API}/charts", json={**chart, "workspace_id": wid}).status_code == 201


def test_member_management(ws):
    wid, ids = ws["ws"]["id"], ws["ids"]
    members = ws["ex@test.com"].get(f"{API}/workspaces/{wid}/members").json()
    assert {(m["principal"], m["role"]) for m in members} == {("user", "editor"), ("group", "manager")}
    new = [{"principal": "user", "id": ids["nobody@test.com"], "role": "viewer"}]
    assert ws["ex@test.com"].put(f"{API}/workspaces/{wid}/members", json=new).status_code == 403
    # A manager cannot remove their own manager role
    assert ws["mgr@test.com"].put(f"{API}/workspaces/{wid}/members", json=new).status_code == 422
    keep = new + [{"principal": "group", "id": ws["group"], "role": "manager"}]
    r = ws["mgr@test.com"].put(f"{API}/workspaces/{wid}/members", json=keep)
    assert r.status_code == 200, r.text
    assert ws["ex@test.com"].get(f"{API}/workspaces/{wid}").status_code == 404
    assert ws["nobody@test.com"].get(f"{API}/workspaces/{wid}").json()["my_role"] == "viewer"
    bad = keep + [{"principal": "user", "id": 999, "role": "viewer"}]
    assert ws["mgr@test.com"].put(f"{API}/workspaces/{wid}/members", json=bad).status_code == 422
    assert ws["mgr@test.com"].put(f"{API}/workspaces/{wid}", json={"name": "Ops"}).status_code == 200
    assert ws["nobody@test.com"].put(f"{API}/workspaces/{wid}", json={"name": "X"}).status_code == 403
    with SessionLocal() as s:
        actions = [a.action for a in s.query(AuditLog).filter(AuditLog.action.like("workspace.%"))]
    assert actions.count("workspace.members") == 2 and "workspace.update" in actions


def test_deleting_a_workspace_keeps_its_items(ws):
    wid = ws["ws"]["id"]
    move(ws["owner"], "dashboards", ws["dash"]["id"], wid)
    move(ws["owner"], "charts", ws["chart"]["id"], wid)
    assert ws["admin@test.com"].delete(f"{API}/workspaces/{wid}").status_code == 204
    d = ws["owner"].get(f"{API}/dashboards/{ws['dash']['id']}")
    assert d.status_code == 200 and d.json()["workspace_id"] is None
    assert ws["owner"].get(f"{API}/charts/{ws['chart']['id']}").json()["workspace_id"] is None
    assert ws["ex@test.com"].get(f"{API}/dashboards/{ws['dash']['id']}").status_code == 404


def test_deleted_groups_leave_workspaces(ws):
    wid = ws["ws"]["id"]
    assert ws["admin@test.com"].delete(f"{API}/groups/{ws['group']}").status_code == 204
    members = ws["admin@test.com"].get(f"{API}/workspaces/{wid}/members").json()
    assert all(m["principal"] == "user" for m in members)
    assert ws["mgr@test.com"].get(f"{API}/workspaces/{wid}").status_code == 404


def test_workspaces_need_sign_in(client):
    assert client.get(f"{API}/workspaces").status_code == 401
