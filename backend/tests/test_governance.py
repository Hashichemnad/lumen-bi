# ruff: noqa: F811 - pytest fixtures imported from test_dashboards are used as parameters
"""Milestone 12 governance: versions (last 2) and roll back, KPI goals with each viewer's own data,
comments and annotations, column masking (never leaks to non-exempt people), data quality checks."""

import pytest

from app.db import SessionLocal
from app.models import AuditLog, Group, ItemVersion, User
from app.services import versions
from tests.test_dashboards import QUERY, SALES, config, world  # noqa: F401 - fixture
from tests.test_model import upload_dataset

API = "/api/v1"


def _update(client, dash, **change):
    body = {"name": dash["name"], "description": "", "config": dash["config"], **change}
    r = client.put(f"{API}/dashboards/{dash['id']}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _age_versions():
    """Make existing versions look old, so the next change keeps a new one."""
    from datetime import UTC, datetime, timedelta

    with SessionLocal() as s:
        for v in s.query(ItemVersion):
            v.created_at = datetime.now(UTC) - timedelta(hours=1)
        s.commit()


# ------------------------------------------------------------------ versions
def test_dashboard_versions_keep_two_and_roll_back(world):
    owner, dash = world["owner"], world["dash"]
    d1 = _update(owner, dash, name="Second")
    # Saves within a few minutes by the same person do not each make a version
    _update(owner, d1, name="Third")
    versions_now = owner.get(f"{API}/dashboards/{dash['id']}/versions").json()
    assert [v["name"] for v in versions_now] == ["Sales"]
    for name in ("Fourth", "Fifth"):
        _age_versions()
        _update(owner, d1, name=name)
    names = [v["name"] for v in owner.get(f"{API}/dashboards/{dash['id']}/versions").json()]
    assert names == ["Fourth", "Third"]  # only the last 2 are kept
    vid = owner.get(f"{API}/dashboards/{dash['id']}/versions").json()[1]["id"]
    assert owner.post(f"{API}/dashboards/{dash['id']}/versions/{vid}/restore").status_code == 200
    assert owner.get(f"{API}/dashboards/{dash['id']}").json()["name"] == "Third"
    after = [v["name"] for v in owner.get(f"{API}/dashboards/{dash['id']}/versions").json()]
    assert after[0] == "Fifth" and len(after) == 2  # the state before rolling back is kept
    with SessionLocal() as s:
        assert s.query(AuditLog).filter_by(action="dashboard.restore").count() == 1


def test_versions_need_edit_rights(world):
    owner, dash = world["owner"], world["dash"]
    _update(owner, dash, name="Changed")
    owner.put(
        f"{API}/dashboards/{dash['id']}/sharing",
        json={"everyone": "view", "users": [], "groups": []},
    )
    viewer = world["viewer@test.com"]
    assert viewer.get(f"{API}/dashboards/{dash['id']}/versions").status_code == 403
    vid = owner.get(f"{API}/dashboards/{dash['id']}/versions").json()[0]["id"]
    assert viewer.post(f"{API}/dashboards/{dash['id']}/versions/{vid}/restore").status_code == 403
    assert world["nobody@test.com"].get(f"{API}/dashboards/{dash['id']}/versions").status_code in (403, 404)
    other = owner.post(
        f"{API}/dashboards", json={"name": "Other", "config": config(world["chart"]["id"])}
    ).json()
    assert owner.post(f"{API}/dashboards/{other['id']}/versions/{vid}/restore").status_code == 404


def test_chart_versions(world):
    owner, chart, ds = world["owner"], world["chart"], world["ds"]
    body = {"name": "Renamed", "dataset_id": ds["id"], "config": {"type": "line"}, "query": QUERY}
    assert owner.put(f"{API}/charts/{chart['id']}", json=body).status_code == 200
    vs = owner.get(f"{API}/charts/{chart['id']}/versions").json()
    assert [v["name"] for v in vs] == ["By region"]
    assert owner.post(f"{API}/charts/{chart['id']}/versions/{vs[0]['id']}/restore").status_code == 200
    back = owner.get(f"{API}/charts/{chart['id']}").json()
    assert back["name"] == "By region" and back["config"] == {"type": "bar"}
    assert world["ex@test.com"].get(f"{API}/charts/{chart['id']}/versions").status_code == 404


def test_deleting_forgets_versions(world):
    owner, dash = world["owner"], world["dash"]
    _update(owner, dash, name="Changed")
    assert owner.delete(f"{API}/dashboards/{dash['id']}").status_code == 204
    with SessionLocal() as s:
        assert s.query(ItemVersion).filter_by(kind="dashboard", item_id=dash["id"]).count() == 0


# ------------------------------------------------------------------ KPI goals
def _kpi_config(chart_id: int, target: float, direction: str = "higher", warn: float = 10) -> dict:
    cfg = config(chart_id)
    cfg["tabs"][0]["items"].insert(
        0,
        {
            "id": "sec1",
            "kind": "section",
            "code": "KPI-01",
            "title": "Sales",
            "goal": {"tile": "c1", "target": target, "direction": direction, "warn": warn},
            "desktop": {"x": 0, "y": 20, "w": 12, "h": 2},
        },
    )
    return cfg


def test_kpi_status_uses_each_viewers_rows(world):
    owner, dash = world["owner"], world["dash"]
    r = owner.put(
        f"{API}/dashboards/{dash['id']}",
        json={"name": "Sales", "config": _kpi_config(world["chart"]["id"], 300)},
    )
    assert r.status_code == 200, r.text
    owner.put(f"{API}/dashboards/{dash['id']}/sharing", json={"everyone": "view", "users": [], "groups": []})

    def status(client):
        out = client.post(f"{API}/dashboards/{dash['id']}/kpis", json={}).json()
        return out[0]["actual"], out[0]["status"]

    # The creator owns the dataset but row-level security applies to them too: no region attribute, no rows
    assert status(world["admin@test.com"]) == (1000.0, "on_track")
    assert status(world["viewer@test.com"]) == (300.0, "on_track")  # UAE: 100 + 200
    # KSA explorer: 300 of 300 is on track; with a higher target it is at risk, then off track
    owner.put(
        f"{API}/dashboards/{dash['id']}",
        json={"name": "Sales", "config": _kpi_config(world["chart"]["id"], 320)},
    )
    assert status(world["ex@test.com"]) == (300.0, "at_risk")
    owner.put(
        f"{API}/dashboards/{dash['id']}",
        json={"name": "Sales", "config": _kpi_config(world["chart"]["id"], 500)},
    )
    assert status(world["ex@test.com"]) == (300.0, "off_track")
    # Lower is better
    cfg = _kpi_config(world["chart"]["id"], 500, "lower")
    owner.put(f"{API}/dashboards/{dash['id']}", json={"name": "Sales", "config": cfg})
    assert status(world["ex@test.com"]) == (300.0, "on_track")
    assert world["nobody@test.com"].post(f"{API}/dashboards/{dash['id']}/kpis", json={}).json()[0][
        "status"
    ] == ("no_data")


def test_kpi_goal_validation(world):
    owner, dash = world["owner"], world["dash"]
    cfg = _kpi_config(world["chart"]["id"], 300)
    cfg["tabs"][0]["items"][0]["goal"]["tile"] = "x1"  # a text tile
    r = owner.put(f"{API}/dashboards/{dash['id']}", json={"name": "Sales", "config": cfg})
    assert r.status_code == 422
    cfg["tabs"][0]["items"][0]["goal"] = {"tile": "c1", "target": "lots"}
    assert (
        owner.put(f"{API}/dashboards/{dash['id']}", json={"name": "Sales", "config": cfg}).status_code == 422
    )


# ------------------------------------------------------------------ comments and annotations
def test_comments(world):
    owner, dash = world["owner"], world["dash"]
    owner.put(f"{API}/dashboards/{dash['id']}/sharing", json={"everyone": "view", "users": [], "groups": []})
    viewer, ex = world["viewer@test.com"], world["ex@test.com"]
    c = viewer.post(
        f"{API}/dashboards/{dash['id']}/comments", json={"body": "Why is UAE low?", "tile_id": "c1"}
    )
    assert c.status_code == 201
    cid = c.json()["id"]
    note = ex.post(
        f"{API}/dashboards/{dash['id']}/comments",
        json={"body": "Ramadan", "tile_id": "c1", "anchor": {"label": "UAE"}},
    )
    assert note.status_code == 201 and note.json()["anchor"] == {"label": "UAE"}
    reply = ex.post(f"{API}/dashboards/{dash['id']}/comments", json={"body": "Looking", "parent_id": cid})
    assert reply.status_code == 201
    # replies to replies, foreign tiles, annotations without a chart are refused
    bad = [
        {"body": "x", "parent_id": reply.json()["id"]},
        {"body": "x", "tile_id": "nope"},
        {"body": "x", "anchor": {"label": "UAE"}},
    ]
    for b in bad:
        assert ex.post(f"{API}/dashboards/{dash['id']}/comments", json=b).status_code == 422
    assert len(owner.get(f"{API}/dashboards/{dash['id']}/comments").json()) == 3
    # Only the author edits; the author or a manager resolves and deletes
    assert ex.patch(f"{API}/comments/{cid}", json={"body": "changed"}).status_code == 403
    assert ex.patch(f"{API}/comments/{cid}", json={"resolved": True}).status_code == 403
    assert viewer.patch(f"{API}/comments/{cid}", json={"body": "Why is UAE so low?"}).json()["edited_at"]
    assert owner.patch(f"{API}/comments/{cid}", json={"resolved": True}).json()["resolved"] is True
    assert ex.delete(f"{API}/comments/{cid}").status_code == 403
    assert owner.delete(f"{API}/comments/{cid}").status_code == 204
    assert len(owner.get(f"{API}/dashboards/{dash['id']}/comments").json()) == 1  # its reply went with it
    # People who cannot open the dashboard see nothing
    owner.put(f"{API}/dashboards/{dash['id']}/sharing", json={"everyone": "none", "users": [], "groups": []})
    assert viewer.get(f"{API}/dashboards/{dash['id']}/comments").status_code == 404
    assert viewer.post(f"{API}/dashboards/{dash['id']}/comments", json={"body": "hi"}).status_code == 404


# ------------------------------------------------------------------ column masking
@pytest.fixture
def people(world):
    owner, ds = world["owner"], world["ds"]
    # Everyone may query; branch is personal data
    owner.put(f"{API}/datasets/{ds['id']}/access", json={"everyone": True})
    with SessionLocal() as s:
        g = Group(name="HR")
        g.members.append(s.query(User).filter_by(email="ex@test.com").one())
        s.add(g)
        s.commit()
        gid = g.id
    return {**world, "hr": gid}


def _branches(client, ds_id):
    r = client.post(f"{API}/query", json={"dataset": ds_id, "dimensions": ["branch"], "measures": ["amount"]})
    assert r.status_code == 200, r.text
    return [row[0] for row in r.json()["rows"]]


def test_masked_columns_never_reach_other_people(people):
    owner, ds = people["owner"], people["ds"]
    r = owner.put(f"{API}/datasets/{ds['id']}/masks", json=[{"column": "branch", "method": "full"}])
    assert r.status_code == 200, r.text
    viewer = people["viewer@test.com"]
    assert _branches(viewer, ds["id"]) == ["••••"]
    # Filtering on the real value finds nothing (filters see the masked value)
    r = viewer.post(
        f"{API}/query",
        json={
            "dataset": ds["id"],
            "measures": ["amount"],
            "filters": [{"field": "branch", "op": "eq", "value": "Dubai"}],
        },
    )
    assert r.json()["rows"] == [[None]]
    # Rows behind charts, calculated fields and exports are masked too
    rows = viewer.post(f"{API}/query", json={"dataset": ds["id"], "mode": "rows", "limit": 10}).json()
    idx = [c["field"] for c in rows["columns"]].index("branch")
    assert {row[idx] for row in rows["rows"]} == {"••••"}
    calc = owner.post(
        f"{API}/datasets/{ds['id']}/fields", json={"name": "Where", "formula": "UPPER([branch])"}
    )
    assert calc.status_code == 201, calc.text
    r = viewer.post(
        f"{API}/query", json={"dataset": ds["id"], "dimensions": ["Where"], "measures": ["amount"]}
    )
    assert [row[0] for row in r.json()["rows"]] == ["••••"]
    export = viewer.post(f"{API}/query/export", json={"dataset": ds["id"], "mode": "rows"})
    assert "Dubai" not in export.text
    # Admins see real values
    assert "Dubai" in _branches(people["admin@test.com"], ds["id"])


def test_mask_methods_and_exempt_groups(people):
    owner, ds = people["owner"], people["ds"]
    owner.put(
        f"{API}/datasets/{ds['id']}/masks",
        json=[{"column": "branch", "method": "partial", "exempt_groups": [people["hr"]]}],
    )
    # The viewer sees UAE rows (row-level security) with the branch partly hidden
    assert sorted(_branches(people["viewer@test.com"], ds["id"])) == ["••••habi", "••••ubai"]
    assert _branches(people["ex@test.com"], ds["id"]) == ["Riyadh"]  # in the exempt group (KSA rows)
    owner.put(f"{API}/datasets/{ds['id']}/masks", json=[{"column": "branch", "method": "hash"}])
    hashed = _branches(people["viewer@test.com"], ds["id"])
    assert len(hashed) == 2 and all(len(h) == 10 for h in hashed)  # still countable, not readable


def test_masks_are_managed_by_owner_or_admin(people):
    ds = people["ds"]
    for email in ("viewer@test.com", "ex@test.com"):
        assert people[email].put(f"{API}/datasets/{ds['id']}/masks", json=[]).status_code == 403
    assert (
        people["owner"].put(f"{API}/datasets/{ds['id']}/masks", json=[{"column": "nope"}]).status_code == 422
    )
    assert (
        people["owner"]
        .put(f"{API}/datasets/{ds['id']}/masks", json=[{"column": "branch", "exempt_groups": [999]}])
        .status_code
        == 422
    )
    with SessionLocal() as s:
        assert s.query(AuditLog).filter_by(action="dataset.masks").count() == 0


def test_masks_carry_into_joined_datasets(people):
    owner, ds = people["owner"], people["ds"]
    owner.put(f"{API}/datasets/{ds['id']}/masks", json=[{"column": "branch", "method": "full"}])
    other = upload_dataset(owner, "More sales", SALES)
    r = owner.post(
        f"{API}/datasets",
        json={"name": "All sales", "source": {"union": {"datasets": [ds["id"], other["id"]]}}},
    )
    assert r.status_code == 201, r.text
    union = r.json()
    owner.put(f"{API}/datasets/{union['id']}/access", json={"everyone": True})
    # The whole combined column is masked (fail closed), not only the rows from the masked parent
    assert set(_branches(people["viewer@test.com"], union["id"])) == {"••••"}
    masks = owner.get(f"{API}/datasets/{union['id']}/masks").json()
    assert masks[0]["inherited_from"]["id"] == ds["id"]


# ------------------------------------------------------------------ data checks
def test_data_checks(world):
    owner, ds = world["owner"], world["ds"]
    add = lambda body: owner.post(f"{API}/datasets/{ds['id']}/checks", json=body)  # noqa: E731
    results = {
        "rows": add({"kind": "row_count", "config": {"min": 10}}).json(),
        "unique": add({"kind": "unique", "config": {"column": "region"}}).json(),
        "range": add({"kind": "range", "config": {"column": "amount", "min": 0, "max": 350}}).json(),
        "allowed": add({"kind": "allowed", "config": {"column": "region", "values": ["UAE", "KSA"]}}).json(),
        "null": add({"kind": "not_null", "config": {"column": "branch"}}).json(),
        "fresh": add({"kind": "freshness", "config": {"hours": 24}}).json(),
        "fresh_col": add({"kind": "freshness", "config": {"hours": 24, "column": "order_date"}}).json(),
    }
    status = {k: (v["last_status"], v["last_value"]) for k, v in results.items()}
    assert status == {
        "rows": ("fail", "4"),
        "unique": ("fail", "1"),  # UAE twice
        "range": ("fail", "1"),  # 400
        "allowed": ("fail", "1"),  # QAT
        "null": ("pass", "0.0%"),
        "fresh": ("pass", status["fresh"][1]),
        "fresh_col": ("fail", status["fresh_col"][1]),  # the newest order is from March
    }
    # The owner was told once about the failing checks
    from app.services.email import OUTBOX

    assert any("Data check failed" in m["Subject"] for m in OUTBOX)
    bad = [
        {"kind": "unique", "config": {"column": "nope"}},
        {"kind": "range", "config": {"column": "amount"}},
        {"kind": "freshness", "config": {"hours": 0}},
        {"kind": "allowed", "config": {"column": "region", "values": []}},
    ]
    for b in bad:
        assert add(b).status_code == 422
    # Dashboards show failing checks of their datasets
    quality = owner.get(f"{API}/dashboards/{world['dash']['id']}").json()["quality"]
    assert quality[str(ds["id"])]["failing"] == 5
    # Only the owner or an admin adds checks; anyone else is refused
    assert world["ex@test.com"].post(f"{API}/datasets/{ds['id']}/checks", json=bad[0]).status_code == 403
    assert world["viewer@test.com"].get(f"{API}/datasets/{ds['id']}/checks").status_code == 403
    cid = results["rows"]["id"]
    changed = owner.put(f"{API}/checks/{cid}", json={"kind": "row_count", "config": {"min": 1}}).json()
    assert changed["last_status"] == "pass"
    assert owner.delete(f"{API}/checks/{cid}").status_code == 204
    assert len(owner.post(f"{API}/datasets/{ds['id']}/checks/run").json()) == 6


def test_versions_service_keep_limit():
    assert versions.KEEP == 2
