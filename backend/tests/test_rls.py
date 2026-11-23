"""Row-level security suite: users must never see rows they are not allowed to see.

Every way of reading data is checked: queries (aggregate and rows), totals, % of total, top-N, value lists,
exports, previews, formula samples, cache, joined and combined datasets, preview-as-user.
"""

import csv
import io

import pytest

from app.db import SessionLocal
from app.models import AuditLog, Group, Role, User
from tests.conftest import Client, make_user
from tests.test_model import add_calc, csv_bytes, upload_dataset

API = "/api/v1"

SALES = (
    ["id", "region", "branch", "amount", "rep_email", "office"],
    [
        [1, "UAE", "Dubai", 100.0, "amal@test.com", 10],
        [2, "UAE", "Abu Dhabi", 200.0, "amal@test.com", 10],
        [3, "KSA", "Riyadh", 300.0, "badr@test.com", 20],
        [4, "KSA", "Jeddah", 400.0, "badr@test.com", 20],
        [5, "QAT", "Doha", 500.0, "carl@test.com", 30],
        [6, None, "Unknown", 600.0, "", 40],
    ],
)
ACCESS = (
    ["user_email", "branch"],
    [
        ["AMAL@test.com", "Dubai"],
        ["badr@test.com", "Riyadh"],
        ["badr@test.com", "Jeddah"],
        ["boss@test.com", "*"],
    ],
)
TOTAL = 2100.0


def login(app, email: str) -> Client:
    c = Client(app, base_url="http://testserver")
    c.__enter__()
    c.login(email)
    return c


@pytest.fixture
def setup(creator_client):
    """Sales dataset shared with everyone, a rule Region = user.region; users with various access."""
    ds = upload_dataset(creator_client, "Sales", SALES)
    assert creator_client.put(f"{API}/datasets/{ds['id']}/access", json={"everyone": True}).status_code == 200
    r = creator_client.post(
        f"{API}/datasets/{ds['id']}/security/rules",
        json={"name": "Region", "kind": "attribute", "config": {"field": "region", "attribute": "region"}},
    )
    assert r.status_code == 201, r.text
    make_user("amal@test.com", Role.viewer, attributes={"region": "uae"})
    make_user("badr@test.com", Role.explorer, attributes={"region": ["KSA", "QAT"]})
    make_user("nobody@test.com", Role.viewer)
    make_user("admin@test.com", Role.admin)
    make_user("other-creator@test.com", Role.creator, attributes={"region": "QAT"})
    app = creator_client.app
    clients: dict[str, Client] = {}

    def as_(email: str) -> Client:
        if email not in clients:
            clients[email] = login(app, email)
        return clients[email]

    yield {"ds": ds, "id": ds["id"], "owner": creator_client, "as": as_, "rule": r.json()}
    for c in clients.values():
        c.__exit__(None, None, None)


def total(client, ds_id, **spec):
    r = client.post(f"{API}/query", json={"dataset": ds_id, "measures": ["amount"], **spec})
    assert r.status_code == 200, r.text
    return r.json()


def regions(client, ds_id) -> dict:
    out = total(client, ds_id, dimensions=["region"])
    return {row[0]: row[1] for row in out["rows"]}


# ------------------------------------------------------------------ the basics
def test_attribute_rule_filters_each_user(setup):
    s = setup
    assert regions(s["as"]("amal@test.com"), s["id"]) == {"UAE": 300.0}  # value matched case-insensitively
    assert regions(s["as"]("badr@test.com"), s["id"]) == {"KSA": 700.0, "QAT": 500.0}  # list of values
    out = total(s["as"]("amal@test.com"), s["id"])
    assert out["rows"] == [[300.0]] and out["restricted"] is True


def test_no_attribute_means_no_rows(setup):
    s = setup
    out = total(s["as"]("nobody@test.com"), s["id"], dimensions=["region"])
    assert out["rows"] == []
    assert total(s["as"]("nobody@test.com"), s["id"])["rows"] == [[None]]


def test_null_values_are_never_matched(setup, db):
    s = setup
    user = db.query(User).filter_by(email="amal@test.com").one()
    user.attributes = {"region": ["", "None", "null"]}
    db.commit()
    assert total(s["as"]("amal@test.com"), s["id"])["rows"] == [[None]]


def test_admin_bypasses_creators_do_not(setup):
    s = setup
    out = total(s["as"]("admin@test.com"), s["id"])
    assert out["rows"] == [[TOTAL]] and out["restricted"] is False
    # The dataset owner (a creator without a region) sees nothing through queries or previews
    assert total(s["owner"], s["id"])["rows"] == [[None]]
    preview = s["owner"].get(f"{API}/datasets/{s['id']}/preview").json()
    assert preview["rows"] == [] and preview["restricted"] is True
    preview = s["as"]("other-creator@test.com").get(f"{API}/datasets/{s['id']}/preview").json()
    assert [r[1] for r in preview["rows"]] == ["QAT"]
    assert len(s["as"]("admin@test.com").get(f"{API}/datasets/{s['id']}/preview").json()["rows"]) == 6


def test_rules_combine_with_and(setup):
    s = setup
    r = s["owner"].post(
        f"{API}/datasets/{s['id']}/security/rules",
        json={"kind": "attribute", "config": {"field": "rep_email", "attribute": "email"}},
    )
    assert r.status_code == 201, r.text
    assert regions(s["as"]("badr@test.com"), s["id"]) == {"KSA": 700.0}  # QAT rows belong to carl
    assert regions(s["as"]("amal@test.com"), s["id"]) == {"UAE": 300.0}


def test_disabled_rule_and_exempt_group(setup, db):
    s = setup
    group = Group(name="Management")
    db.add(group)
    db.flush()
    member = db.query(User).filter_by(email="nobody@test.com").one()
    member.groups.append(group)
    db.commit()
    rule = s["rule"]
    body = {"name": "Region", "kind": "attribute", "config": rule["config"], "exempt_groups": [group.id]}
    r = s["owner"].put(f"{API}/datasets/{s['id']}/security/rules/{rule['id']}", json=body)
    assert r.status_code == 200 and r.json()["exempt_group_names"] == ["Management"]
    assert total(s["as"]("nobody@test.com"), s["id"])["rows"] == [[TOTAL]]
    assert regions(s["as"]("amal@test.com"), s["id"]) == {"UAE": 300.0}
    body |= {"exempt_groups": [], "enabled": False}
    s["owner"].put(f"{API}/datasets/{s['id']}/security/rules/{rule['id']}", json=body)
    assert total(s["as"]("amal@test.com"), s["id"])["rows"] == [[TOTAL]]


def test_numeric_field_attribute(setup):
    s = setup
    s["owner"].delete(f"{API}/datasets/{s['id']}/security/rules/{s['rule']['id']}")
    s["owner"].post(
        f"{API}/datasets/{s['id']}/security/rules",
        json={"kind": "attribute", "config": {"field": "office", "attribute": "office"}},
    )
    with SessionLocal() as db:
        u = db.query(User).filter_by(email="amal@test.com").one()
        u.attributes = {"office": ["10", "x", "30.5"]}
        db.commit()
    assert total(s["as"]("amal@test.com"), s["id"])["rows"] == [[300.0]]


def test_custom_attribute_cannot_override_email(setup):
    s = setup
    s["owner"].delete(f"{API}/datasets/{s['id']}/security/rules/{s['rule']['id']}")
    s["owner"].post(
        f"{API}/datasets/{s['id']}/security/rules",
        json={"kind": "attribute", "config": {"field": "rep_email", "attribute": "email"}},
    )
    with SessionLocal() as db:
        u = db.query(User).filter_by(email="amal@test.com").one()
        u.attributes = {"Email": "badr@test.com"}
        db.commit()
    assert total(s["as"]("amal@test.com"), s["id"])["rows"] == [[300.0]]


def test_rule_on_calculated_field_and_broken_field_fails_closed(setup):
    s = setup
    add_calc(s["owner"], s["id"], "Country", "UPPER([region])")
    s["owner"].delete(f"{API}/datasets/{s['id']}/security/rules/{s['rule']['id']}")
    r = s["owner"].post(
        f"{API}/datasets/{s['id']}/security/rules",
        json={
            "kind": "attribute",
            "config": {"field": "Country", "attribute": "region", "ignore_case": False},
        },
    )
    assert r.status_code == 201
    assert total(s["as"]("badr@test.com"), s["id"])["rows"] == [[1200.0]]
    assert total(s["as"]("amal@test.com"), s["id"])["rows"] == [[None]]  # "uae" != "UAE" when case matters
    # The field a rule uses cannot be deleted...
    fid = next(
        f["id"] for f in s["owner"].get(f"{API}/datasets/{s['id']}/fields").json() if f["name"] == "Country"
    )
    assert s["owner"].delete(f"{API}/datasets/{s['id']}/fields/{fid}").status_code == 409
    # ...renaming it updates the rule
    assert (
        s["owner"].patch(f"{API}/datasets/{s['id']}/fields/{fid}", json={"name": "Nation"}).status_code == 200
    )
    rule = s["owner"].get(f"{API}/datasets/{s['id']}/security").json()["rules"][0]
    assert rule["config"]["field"] == "Nation"
    # ...and when the source column disappears, the rule blocks everything instead of showing everything
    header, rows = SALES
    keep = [i for i, h in enumerate(header) if h != "region"]
    r = s["owner"].put(
        f"{API}/connections/{s['ds']['connection']['id']}/file",
        files={
            "file": (
                "data.csv",
                csv_bytes([header[i] for i in keep], [[row[i] for i in keep] for row in rows]),
            )
        },
    )
    assert r.status_code == 200, r.text
    assert total(s["as"]("badr@test.com"), s["id"])["rows"] == [[None]]
    assert total(s["as"]("admin@test.com"), s["id"])["rows"] == [[TOTAL]]


# ------------------------------------------------------------------ every way of reading data
def test_totals_pct_of_total_and_top_n_only_count_visible_rows(setup):
    s = setup
    amal = s["as"]("amal@test.com")
    out = total(amal, s["id"], dimensions=["branch"], totals=True)
    assert out["totals"] == [None, 300.0]
    out = amal.post(
        f"{API}/query",
        json={
            "dataset": s["id"],
            "dimensions": ["branch"],
            "measures": [{"field": "amount", "calc": {"type": "pct_total"}}],
        },
    ).json()
    assert {r[0]: r[1] for r in out["rows"]} == {
        "Abu Dhabi": pytest.approx(2 / 3),
        "Dubai": pytest.approx(1 / 3),
    }
    top = {"field": "branch", "op": "top", "n": 1, "by": {"field": "amount"}}
    out = total(amal, s["id"], dimensions=["branch"], filters=[top])
    assert out["rows"] == [["Abu Dhabi", 200.0]]  # not "Unknown" (600), which amal cannot see
    rank = amal.post(
        f"{API}/query",
        json={
            "dataset": s["id"],
            "dimensions": ["branch"],
            "measures": [{"field": "amount", "calc": {"type": "rank"}}],
        },
    ).json()
    assert sorted(r[1] for r in rank["rows"]) == [1, 2]


def test_aggregate_formulas_and_count_respect_rules(setup):
    s = setup
    add_calc(s["owner"], s["id"], "Everything", "SUM([amount]) + COUNT([id])")
    amal = s["as"]("amal@test.com")
    out = amal.post(
        f"{API}/query", json={"dataset": s["id"], "measures": ["Everything", {"field": "*", "agg": "count"}]}
    )
    assert out.json()["rows"] == [[302.0, 2]]


def test_rows_mode_filters_and_value_lists(setup):
    s = setup
    badr = s["as"]("badr@test.com")
    out = badr.post(f"{API}/query", json={"dataset": s["id"], "mode": "rows", "fields": ["branch"]}).json()
    assert sorted(r[0] for r in out["rows"]) == ["Doha", "Jeddah", "Riyadh"]
    # A filter cannot widen access
    out = total(badr, s["id"], filters=[{"field": "region", "op": "in", "values": ["UAE", "KSA", None]}])
    assert out["rows"] == [[700.0]]
    vals = badr.get(f"{API}/datasets/{s['id']}/values", params={"field": "branch", "order": "value"}).json()
    assert vals["values"] == ["Doha", "Jeddah", "Riyadh"] and vals["restricted"] is True
    vals = badr.get(f"{API}/datasets/{s['id']}/values", params={"field": "region"}).json()
    assert set(vals["values"]) == {"KSA", "QAT"}


def test_export_respects_rules(setup):
    s = setup
    r = s["as"]("badr@test.com").post(
        f"{API}/query/export", json={"dataset": s["id"], "mode": "rows", "fields": ["branch"]}
    )
    assert r.status_code == 200
    rows = list(csv.reader(io.StringIO(r.text.lstrip("﻿"))))[1:]
    assert sorted(r[0] for r in rows) == ["Doha", "Jeddah", "Riyadh"]
    with SessionLocal() as db:
        assert db.query(AuditLog).filter_by(action="data.export").count() == 1


def test_formula_samples_respect_rules(setup):
    s = setup
    oc = s["as"]("other-creator@test.com")
    r = oc.post(
        f"{API}/datasets/{s['id']}/formula/check", json={"formula": "SUM([amount])", "kind": "calculated"}
    )
    assert r.status_code == 200 and r.json()["sample"] == [500.0]
    r = oc.post(f"{API}/datasets/{s['id']}/formula/check", json={"formula": "[branch]", "kind": "calculated"})
    assert r.json()["sample"] == ["Doha"]


def test_cache_is_never_shared_between_users(setup):
    s = setup
    assert total(s["as"]("amal@test.com"), s["id"])["rows"] == [[300.0]]
    badr = total(s["as"]("badr@test.com"), s["id"])
    assert badr["rows"] == [[1200.0]] and badr["cached"] is False
    assert total(s["as"]("amal@test.com"), s["id"])["cached"] is True
    # A change of attributes takes effect at once (the user's values are part of the cache key)
    with SessionLocal() as db:
        u = db.query(User).filter_by(email="amal@test.com").one()
        u.attributes = {"region": "QAT"}
        db.commit()
    assert total(s["as"]("amal@test.com"), s["id"])["rows"] == [[500.0]]


# ------------------------------------------------------------------ access tables (entitlements)
@pytest.fixture
def with_access_table(setup):
    s = setup
    access = upload_dataset(s["owner"], "Branch access", ACCESS, filename="access.csv")
    s["owner"].delete(f"{API}/datasets/{s['id']}/security/rules/{s['rule']['id']}")
    r = s["owner"].post(
        f"{API}/datasets/{s['id']}/security/rules",
        json={
            "name": "Branches",
            "kind": "entitlement",
            "config": {
                "field": "branch",
                "dataset": access["id"],
                "user_column": "user_email",
                "value_column": "branch",
                "all_value": "*",
            },
        },
    )
    assert r.status_code == 201, r.text
    make_user("boss@test.com", Role.viewer)
    return {**s, "access": access}


def test_entitlement_rule(with_access_table):
    s = with_access_table
    assert total(s["as"]("amal@test.com"), s["id"])["rows"] == [[100.0]]  # email matched ignoring case
    assert total(s["as"]("badr@test.com"), s["id"])["rows"] == [[700.0]]
    assert total(s["as"]("boss@test.com"), s["id"])["rows"] == [[TOTAL]]  # "*" gives all rows
    assert total(s["as"]("nobody@test.com"), s["id"])["rows"] == [[None]]  # not in the table


def test_access_table_cannot_be_deleted_while_used(with_access_table):
    s = with_access_table
    r = s["owner"].delete(f"{API}/datasets/{s['access']['id']}")
    assert r.status_code == 409 and "access table" in r.json()["detail"]


def test_entitlement_rule_validation(setup):
    s = setup
    access = upload_dataset(s["owner"], "Branch access", ACCESS, filename="access.csv")
    bad = {"field": "branch", "dataset": access["id"], "user_column": "nope", "value_column": "branch"}
    r = s["owner"].post(
        f"{API}/datasets/{s['id']}/security/rules", json={"kind": "entitlement", "config": bad}
    )
    assert r.status_code == 422
    selfref = {**bad, "dataset": s["id"], "user_column": "rep_email"}
    r = s["owner"].post(
        f"{API}/datasets/{s['id']}/security/rules", json={"kind": "entitlement", "config": selfref}
    )
    assert r.status_code == 422
    agg = {"field": "amount", "attribute": "region"}
    add_calc(s["owner"], s["id"], "Total", "SUM([amount])")
    r = s["owner"].post(
        f"{API}/datasets/{s['id']}/security/rules",
        json={"kind": "attribute", "config": {**agg, "field": "Total"}},
    )
    assert r.status_code == 422 and "row-level" in r.json()["detail"]


# ------------------------------------------------------------------ joins and unions inherit rules
def test_joined_dataset_inherits_rules_of_its_sources(setup):
    s = setup
    targets = (["branch", "target"], [["Dubai", 1], ["Riyadh", 2], ["Doha", 3], ["Unknown", 4]])
    t = upload_dataset(s["owner"], "Targets", targets, filename="targets.csv")
    joined = s["owner"].post(
        f"{API}/datasets",
        json={
            "name": "Targets with sales",
            "source": {
                "join": {
                    "base": t["id"],
                    "joins": [
                        {
                            "dataset": s["id"],
                            "type": "left",
                            "alias": "S",
                            "on": [{"left": "branch", "right": "branch"}],
                        }
                    ],
                }
            },
        },
    )
    assert joined.status_code == 201, joined.text
    jid = joined.json()["id"]
    s["owner"].put(f"{API}/datasets/{jid}/access", json={"everyone": True})
    sec = s["owner"].get(f"{API}/datasets/{jid}/security").json()
    assert sec["inherited"][0]["dataset"]["name"] == "Sales" and sec["inherited"][0]["mapped"] is True
    out = s["as"]("amal@test.com").post(
        f"{API}/query", json={"dataset": jid, "dimensions": ["branch"], "measures": ["target"]}
    )
    assert out.json()["rows"] == [["Dubai", 1]]
    # The owner of the joined dataset gets no way around it either
    out = s["owner"].post(f"{API}/query", json={"dataset": jid, "measures": [{"field": "*", "agg": "count"}]})
    assert out.json()["rows"] == [[0]]
    out = s["as"]("admin@test.com").post(
        f"{API}/query", json={"dataset": jid, "measures": [{"field": "*", "agg": "count"}]}
    )
    assert out.json()["rows"] == [[4]]


def test_combined_dataset_inherits_rules(setup):
    s = setup
    more = upload_dataset(
        s["owner"], "More sales", (SALES[0], [[7, "UAE", "Sharjah", 50.0, "x", 10]]), filename="m.csv"
    )
    union = s["owner"].post(
        f"{API}/datasets",
        json={
            "name": "All sales",
            "source": {"union": {"datasets": [s["id"], more["id"]], "source_column": "From"}},
        },
    )
    assert union.status_code == 201, union.text
    uid = union.json()["id"]
    s["owner"].put(f"{API}/datasets/{uid}/access", json={"everyone": True})
    assert total(s["as"]("amal@test.com"), uid)["rows"] == [[350.0]]
    assert total(s["as"]("nobody@test.com"), uid)["rows"] == [[None]]


def test_unmapped_inherited_rule_blocks_everything(setup):
    s = setup
    # A join that drops nothing but a rule whose field lives only in the source as a formula on a column
    # the join renames: the inherited rule must still find it through the mapping
    add_calc(s["owner"], s["id"], "Region upper", "UPPER([region])")
    s["owner"].post(
        f"{API}/datasets/{s['id']}/security/rules",
        json={"kind": "attribute", "config": {"field": "Region upper", "attribute": "region"}},
    )
    other = upload_dataset(s["owner"], "Branches", (["branch", "city"], [["Dubai", "DXB"]]), filename="b.csv")
    joined = (
        s["owner"]
        .post(
            f"{API}/datasets",
            json={
                "name": "Branch cities",
                "source": {
                    "join": {
                        "base": other["id"],
                        "joins": [
                            {
                                "dataset": s["id"],
                                "type": "inner",
                                "alias": "S",
                                "on": [{"left": "branch", "right": "branch"}],
                            }
                        ],
                    }
                },
            },
        )
        .json()
    )
    s["owner"].put(f"{API}/datasets/{joined['id']}/access", json={"everyone": True})
    out = s["as"]("amal@test.com").post(
        f"{API}/query", json={"dataset": joined["id"], "mode": "rows", "fields": ["city"]}
    )
    assert out.json()["rows"] == [["DXB"]]  # both inherited rules mapped to S.region


# ------------------------------------------------------------------ who may query, and who may manage
def test_dataset_access_by_user_and_group(creator_client, db):
    ds = upload_dataset(creator_client, "Private", SALES)
    make_user("v1@test.com", Role.viewer)
    make_user("v2@test.com", Role.viewer)
    app = creator_client.app
    v1, v2 = login(app, "v1@test.com"), login(app, "v2@test.com")
    try:
        for c in (v1, v2):
            assert (
                c.post(f"{API}/query", json={"dataset": ds["id"], "measures": ["amount"]}).status_code == 404
            )
            assert c.get(f"{API}/catalog/datasets").json() == []
            assert c.get(f"{API}/datasets/{ds['id']}/values", params={"field": "region"}).status_code == 404
            assert c.get(f"{API}/catalog/datasets/{ds['id']}").status_code == 404
        v1_id = db.query(User).filter_by(email="v1@test.com").one().id
        group = Group(name="Sales team")
        db.add(group)
        db.flush()
        member = db.query(User).filter_by(email="v2@test.com").one()
        member.groups.append(group)
        db.commit()
        r = creator_client.put(f"{API}/datasets/{ds['id']}/access", json={"user_ids": [v1_id]})
        assert r.status_code == 200 and r.json()["users"][0]["email"] == "v1@test.com"
        assert v1.post(f"{API}/query", json={"dataset": ds["id"], "measures": ["amount"]}).status_code == 200
        assert v2.post(f"{API}/query", json={"dataset": ds["id"], "measures": ["amount"]}).status_code == 404
        creator_client.put(f"{API}/datasets/{ds['id']}/access", json={"group_ids": [group.id]})
        assert v1.post(f"{API}/query", json={"dataset": ds["id"], "measures": ["amount"]}).status_code == 404
        assert v2.post(f"{API}/query", json={"dataset": ds["id"], "measures": ["amount"]}).status_code == 200
        assert [d["name"] for d in v2.get(f"{API}/catalog/datasets").json()] == ["Private"]
    finally:
        v1.__exit__(None, None, None)
        v2.__exit__(None, None, None)


def test_deleting_a_group_or_user_removes_their_access(setup, db):
    s = setup
    group = Group(name="Temp")
    db.add(group)
    db.commit()
    gid = group.id
    rule = s["rule"]
    s["owner"].put(
        f"{API}/datasets/{s['id']}/security/rules/{rule['id']}",
        json={"kind": "attribute", "config": rule["config"], "exempt_groups": [gid]},
    )
    s["owner"].put(f"{API}/datasets/{s['id']}/access", json={"group_ids": [gid]})
    admin = s["as"]("admin@test.com")
    assert admin.delete(f"{API}/groups/{gid}").status_code == 204
    sec = s["owner"].get(f"{API}/datasets/{s['id']}/security").json()
    assert sec["rules"][0]["exempt_groups"] == [] and sec["access"]["group_ids"] == []


@pytest.mark.parametrize("email", ["amal@test.com", "badr@test.com", "other-creator@test.com"])
def test_only_owner_or_admin_manage_security(setup, email):
    s = setup
    c = s["as"](email)
    rule = {"kind": "attribute", "config": {"field": "region", "attribute": "region"}}
    expected = 403
    assert c.post(f"{API}/datasets/{s['id']}/security/rules", json=rule).status_code == expected
    assert c.delete(f"{API}/datasets/{s['id']}/security/rules/{s['rule']['id']}").status_code == expected
    assert c.put(f"{API}/datasets/{s['id']}/access", json={"everyone": False}).status_code == expected
    assert c.get(f"{API}/datasets/{s['id']}/security/check", params={"user_id": 1}).status_code == expected
    admin = s["as"]("admin@test.com")
    assert admin.post(f"{API}/datasets/{s['id']}/security/rules", json=rule).status_code == 201


def test_viewers_cannot_read_security_settings(setup):
    s = setup
    assert s["as"]("amal@test.com").get(f"{API}/datasets/{s['id']}/security").status_code == 403
    assert s["as"]("amal@test.com").get(f"{API}/directory").status_code == 403


# ------------------------------------------------------------------ preview as a user
def test_preview_as_user(setup, db):
    s = setup
    badr = db.query(User).filter_by(email="badr@test.com").one()
    out = total(s["owner"], s["id"], as_user_id=badr.id)
    assert out["rows"] == [[1200.0]]
    check = s["owner"].get(f"{API}/datasets/{s['id']}/security/check", params={"user_id": badr.id}).json()
    assert (
        check["has_access"]
        and not check["bypass"]
        and check["visible_rows"] == 3
        and check["total_rows"] == 6
    )
    assert check["rules"][0]["status"] == "applies" and check["rules"][0]["values"] == ["ksa", "qat"]
    nobody = db.query(User).filter_by(email="nobody@test.com").one()
    check = s["owner"].get(f"{API}/datasets/{s['id']}/security/check", params={"user_id": nobody.id}).json()
    assert check["visible_rows"] == 0 and check["rules"][0]["status"] == "blocked"
    assert "no region" in check["rules"][0]["reason"]
    with SessionLocal() as session:
        assert session.query(AuditLog).filter_by(action="query.preview_as").count() == 3


def test_preview_as_requires_owner_or_admin(setup, db):
    s = setup
    amal = db.query(User).filter_by(email="amal@test.com").one()
    for email in ("badr@test.com", "other-creator@test.com"):
        r = s["as"](email).post(
            f"{API}/query", json={"dataset": s["id"], "measures": ["amount"], "as_user_id": amal.id}
        )
        assert r.status_code == 403
    # Previewing someone who cannot use the dataset is refused
    s["owner"].put(f"{API}/datasets/{s['id']}/access", json={"everyone": False})
    r = s["owner"].post(
        f"{API}/query", json={"dataset": s["id"], "measures": ["amount"], "as_user_id": amal.id}
    )
    assert r.status_code == 403 and "cannot use" in r.json()["detail"]


# ------------------------------------------------------------------ injection attempts
@pytest.mark.parametrize(
    "value",
    [
        "UAE') OR TRUE --",
        "' OR '1'='1",
        "UAE\"; SELECT * FROM read_parquet('/etc/passwd'); --",
        "$1",
        "%",
    ],
)
def test_sql_in_attribute_values_is_only_data(setup, value):
    s = setup
    with SessionLocal() as db:
        u = db.query(User).filter_by(email="amal@test.com").one()
        u.attributes = {"region": value}
        db.commit()
    assert total(s["as"]("amal@test.com"), s["id"])["rows"] == [[None]]


@pytest.mark.parametrize(
    "spec",
    [
        {"filters": [{"field": "region", "op": "eq", "value": "x' OR TRUE OR 'a'='a"}]},
        {"filters": [{"field": "region", "op": "contains", "value": "%' OR 1=1 --"}]},
        {"filters": [{"field": "region) OR (TRUE", "op": "is_null"}]},
        {"dimensions": ["region\" FROM base) UNION SELECT * FROM read_parquet('x') --"]},
        {"sort": [{"key": "1; DROP TABLE users"}]},
    ],
)
def test_sql_in_query_specs_cannot_widen_access(setup, spec):
    s = setup
    r = s["as"]("amal@test.com").post(
        f"{API}/query", json={"dataset": s["id"], "measures": ["amount"], **spec}
    )
    assert r.status_code in (200, 400)
    if r.status_code == 200:
        assert r.json()["rows"] in ([[None]], [])
