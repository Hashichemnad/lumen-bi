import csv
import io

from app.models import Role
from tests.conftest import Client, make_user

API = "/api/v1"


def csv_bytes(header: list[str], rows: list[list]) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(header)
    w.writerows(rows)
    return out.getvalue().encode()


ORDERS = (
    ["order_id", "customer_id", "region", "amount", "cost", "order_date"],
    [
        [i, 1 + i % 4, ["UAE", "KSA", "QAT"][i % 3], i * 100.0, i * 60.0, f"2026-0{1 + i % 6}-1{i % 9}"]
        for i in range(1, 31)
    ],
)
CUSTOMERS = (
    ["id", "name", "segment"],
    [[1, "Emaar", "Corporate"], [2, "Noon", "Retail"], [3, "ADNOC", "Energy"]],
)


def upload_dataset(client, name: str, data: tuple, filename: str = "data.csv") -> dict:
    conn = client.post(
        f"{API}/connections/upload", files={"file": (filename, csv_bytes(*data))}, data={"name": name}
    ).json()
    r = client.post(
        f"{API}/datasets", json={"name": name, "connection_id": conn["id"], "source": {"object": "data"}}
    )
    assert r.status_code == 201, r.text
    return {**r.json(), "connection": conn}


def fields(client, ds_id: int) -> dict[str, dict]:
    return {f["name"]: f for f in client.get(f"{API}/datasets/{ds_id}/fields").json()}


def add_calc(client, ds_id: int, name: str, formula: str, **kw):
    return client.post(f"{API}/datasets/{ds_id}/fields", json={"name": name, "formula": formula, **kw})


# ------------------------------------------------------------------ fields
def test_fields_created_with_sensible_defaults(creator_client):
    ds = upload_dataset(creator_client, "Orders", ORDERS)
    f = fields(creator_client, ds["id"])
    assert list(f) == ORDERS[0]
    assert f["order_id"]["role"] == "dimension" and f["customer_id"]["role"] == "dimension"
    assert f["amount"]["role"] == "measure" and f["amount"]["label"] == "Amount"
    assert f["amount"]["format"] == {"type": "number", "decimals": 2} and f["amount"]["default_agg"] == "sum"
    assert f["order_date"]["data_type"] == "date" and f["order_date"]["label"] == "Order Date"


def test_edits_survive_resync_and_new_or_removed_columns_are_handled(creator_client):
    ds = upload_dataset(creator_client, "Orders", ORDERS)
    amount = fields(creator_client, ds["id"])["amount"]
    r = creator_client.patch(
        f"{API}/datasets/{ds['id']}/fields/{amount['id']}",
        json={
            "label": "Order value",
            "format": {"type": "currency", "currency": "AED", "decimals": 0},
            "description": "Value incl. VAT",
            "default_agg": "avg",
        },
    )
    assert r.status_code == 200 and r.json()["label"] == "Order value"
    cost = fields(creator_client, ds["id"])["cost"]
    creator_client.patch(f"{API}/datasets/{ds['id']}/fields/{cost['id']}", json={"hidden": True})

    header = [
        "order_id",
        "customer_id",
        "region",
        "amount",
        "order_date",
        "channel",
    ]  # cost removed, channel added
    rows = [[1, 1, "UAE", 5.0, "2026-01-01", "Web"]]
    r = creator_client.put(
        f"{API}/connections/{ds['connection']['id']}/file", files={"file": ("x.csv", csv_bytes(header, rows))}
    )
    assert r.status_code == 200
    f = fields(creator_client, ds["id"])
    assert f["amount"]["label"] == "Order value" and f["amount"]["format"]["currency"] == "AED"
    assert f["amount"]["default_agg"] == "avg"
    assert f["channel"]["label"] == "Channel" and f["channel"]["missing"] is False
    assert f["cost"]["missing"] is True and f["cost"]["hidden"] is True
    # a vanished column's field can now be deleted; a live one cannot
    assert creator_client.delete(f"{API}/datasets/{ds['id']}/fields/{f['cost']['id']}").status_code == 204
    assert creator_client.delete(f"{API}/datasets/{ds['id']}/fields/{f['amount']['id']}").status_code == 422


def test_column_fields_cannot_be_renamed_or_get_formulas(creator_client):
    ds = upload_dataset(creator_client, "Orders", ORDERS)
    region = fields(creator_client, ds["id"])["region"]
    r = creator_client.patch(f"{API}/datasets/{ds['id']}/fields/{region['id']}", json={"formula": "1"})
    assert r.status_code == 422
    r = creator_client.patch(
        f"{API}/datasets/{ds['id']}/fields/{region['id']}", json={"format": {"type": "weird"}}
    )
    assert r.status_code == 422


# ------------------------------------------------------------------ calculated fields
def test_formula_check_returns_type_and_sample(creator_client):
    ds = upload_dataset(creator_client, "Orders", ORDERS)
    r = creator_client.post(
        f"{API}/datasets/{ds['id']}/formula/check", json={"formula": "[amount] - [cost]"}
    ).json()
    assert r["ok"] and r["type"] == "number" and not r["is_aggregate"] and r["sample"][:2] == [40.0, 80.0]
    r = creator_client.post(
        f"{API}/datasets/{ds['id']}/formula/check", json={"formula": "SUM([amount] - [cost]) / SUM([amount])"}
    ).json()
    assert r["ok"] and r["is_aggregate"] and r["sample"] == [0.4]
    r = creator_client.post(
        f"{API}/datasets/{ds['id']}/formula/check", json={"formula": "[amount] + [nope]"}
    ).json()
    assert not r["ok"] and "Unknown field [nope]" in r["error"] and r["pos"] == 11


def test_calculated_fields_appear_in_preview_and_aggregates_are_measures(creator_client):
    ds = upload_dataset(creator_client, "Orders", ORDERS)
    profit = add_calc(creator_client, ds["id"], "Profit", "[amount] - [cost]")
    assert profit.status_code == 201, profit.text
    assert profit.json()["role"] == "measure" and profit.json()["data_type"] == "number"
    tier = add_calc(creator_client, ds["id"], "Tier", "IF [Profit] >= 1000 THEN 'Large' ELSE 'Small' END")
    assert tier.json()["role"] == "dimension"
    margin = add_calc(creator_client, ds["id"], "Margin %", "SUM([Profit]) / SUM([amount])")
    assert margin.json()["is_aggregate"] is True and margin.json()["default_agg"] == "none"

    pv = creator_client.get(f"{API}/datasets/{ds['id']}/preview?limit=3").json()
    assert pv["columns"][-2:] == ["Profit", "Tier"]  # aggregates are not row values
    assert [r[-2:] for r in pv["rows"]] == [[40.0, "Small"], [80.0, "Small"], [120.0, "Small"]]


def test_calculated_field_validation(creator_client):
    ds = upload_dataset(creator_client, "Orders", ORDERS)
    r = add_calc(creator_client, ds["id"], "Bad", "[amount] + 'x'")
    assert r.status_code == 422 and "STR()" in r.json()["detail"] and "at character" in r.json()["detail"]
    assert add_calc(creator_client, ds["id"], "Region", "1").status_code == 409  # clashes with a column
    assert add_calc(creator_client, ds["id"], "Bad [name]", "1").status_code == 422
    assert add_calc(creator_client, ds["id"], "Empty", "").status_code == 422


def test_rename_updates_references_and_delete_is_protected(creator_client):
    ds = upload_dataset(creator_client, "Orders", ORDERS)
    profit = add_calc(creator_client, ds["id"], "Profit", "[amount] - [cost]").json()
    add_calc(creator_client, ds["id"], "Double profit", "[Profit] * 2")
    r = creator_client.delete(f"{API}/datasets/{ds['id']}/fields/{profit['id']}")
    assert r.status_code == 409 and "Double profit" in r.json()["detail"]
    r = creator_client.patch(
        f"{API}/datasets/{ds['id']}/fields/{profit['id']}", json={"name": "Gross profit"}
    )
    assert r.status_code == 200
    f = fields(creator_client, ds["id"])
    assert f["Double profit"]["formula"] == "[Gross profit] * 2" and f["Double profit"]["error"] is None
    assert f["Gross profit"]["used_by"] == ["Double profit"]


def test_cycles_rejected_on_update(creator_client):
    ds = upload_dataset(creator_client, "Orders", ORDERS)
    a = add_calc(creator_client, ds["id"], "A", "[amount] + 1").json()
    add_calc(creator_client, ds["id"], "B", "[A] + 1")
    r = creator_client.patch(f"{API}/datasets/{ds['id']}/fields/{a['id']}", json={"formula": "[B] + 1"})
    assert r.status_code == 422 and "refers to itself" in r.json()["detail"]


def test_formula_breaks_visibly_when_a_column_disappears(creator_client):
    ds = upload_dataset(creator_client, "Orders", ORDERS)
    add_calc(creator_client, ds["id"], "Profit", "[amount] - [cost]")
    header = ["order_id", "customer_id", "region", "amount", "order_date"]
    creator_client.put(
        f"{API}/connections/{ds['connection']['id']}/file",
        files={"file": ("x.csv", csv_bytes(header, [[1, 1, "UAE", 5.0, "2026-01-01"]]))},
    )
    profit = fields(creator_client, ds["id"])["Profit"]
    assert "Unknown field [cost]" in profit["error"]
    pv = creator_client.get(f"{API}/datasets/{ds['id']}/preview").json()
    assert "Profit" not in pv["columns"]  # a broken field does not break the preview


# ------------------------------------------------------------------ groups and bins
def test_group_and_bin_fields(creator_client):
    ds = upload_dataset(creator_client, "Orders", ORDERS)
    g = creator_client.post(
        f"{API}/datasets/{ds['id']}/fields",
        json={
            "name": "Market",
            "kind": "group",
            "config": {
                "source": "region",
                "groups": [{"name": "Gulf", "values": ["KSA", "QAT"]}],
                "other": "Home",
            },
        },
    )
    assert g.status_code == 201, g.text
    b = creator_client.post(
        f"{API}/datasets/{ds['id']}/fields",
        json={"name": "Amount band", "kind": "bin", "config": {"source": "amount", "size": 1000}},
    )
    assert b.status_code == 201 and b.json()["role"] == "dimension"
    bad = creator_client.post(
        f"{API}/datasets/{ds['id']}/fields",
        json={"name": "Bad bins", "kind": "bin", "config": {"source": "region", "size": 10}},
    )
    assert bad.status_code == 422
    pv = creator_client.get(f"{API}/datasets/{ds['id']}/preview?limit=12").json()
    i_m, i_b = pv["columns"].index("Market"), pv["columns"].index("Amount band")
    assert [r[i_m] for r in pv["rows"][:3]] == ["Gulf", "Gulf", "Home"]  # regions KSA, QAT, UAE
    assert pv["rows"][11][i_b] == 1000.0  # 1200 -> 1000 band


# ------------------------------------------------------------------ hierarchies and parameters
def test_hierarchies(creator_client):
    ds = upload_dataset(creator_client, "Orders", ORDERS)
    r = creator_client.post(
        f"{API}/datasets/{ds['id']}/hierarchies",
        json={"name": "Geography", "levels": ["region", "customer_id"]},
    )
    assert r.status_code == 201
    assert (
        creator_client.post(
            f"{API}/datasets/{ds['id']}/hierarchies", json={"name": "x", "levels": ["region"]}
        ).status_code
        == 422
    )
    assert (
        creator_client.post(
            f"{API}/datasets/{ds['id']}/hierarchies", json={"name": "x", "levels": ["region", "nope"]}
        ).status_code
        == 422
    )
    region = fields(creator_client, ds["id"])["region"]
    assert "hierarchy Geography" in region["used_by"]
    h = creator_client.put(
        f"{API}/datasets/{ds['id']}/hierarchies/{r.json()['id']}",
        json={"name": "Geo", "levels": ["customer_id", "region"]},
    )
    assert h.json()["levels"] == ["customer_id", "region"]
    assert creator_client.delete(f"{API}/datasets/{ds['id']}/hierarchies/{h.json()['id']}").status_code == 204


def test_parameters_feed_formulas(creator_client):
    ds = upload_dataset(creator_client, "Orders", ORDERS)
    p = creator_client.post(
        f"{API}/datasets/{ds['id']}/parameters",
        json={"name": "FX Rate", "data_type": "number", "default_value": "3.67"},
    )
    assert p.status_code == 201 and p.json()["default_value"] == 3.67
    r = creator_client.post(
        f"{API}/datasets/{ds['id']}/formula/check", json={"formula": "[amount] / @[FX Rate]"}
    ).json()
    assert r["ok"] and round(r["sample"][0], 2) == 27.25
    add_calc(creator_client, ds["id"], "Amount USD", "[amount] / @[FX Rate]")
    assert creator_client.delete(f"{API}/datasets/{ds['id']}/parameters/{p.json()['id']}").status_code == 409
    choice = creator_client.post(
        f"{API}/datasets/{ds['id']}/parameters",
        json={"name": "Top N", "data_type": "integer", "allowed_values": [5, "10", 20]},
    )
    assert choice.json()["default_value"] == 5 and choice.json()["allowed_values"] == [5, 10, 20]
    bad = creator_client.post(
        f"{API}/datasets/{ds['id']}/parameters",
        json={"name": "When", "data_type": "date", "default_value": "not a date"},
    )
    assert bad.status_code == 422


# ------------------------------------------------------------------ permissions
def test_model_permissions(creator_client):
    ds = upload_dataset(creator_client, "Orders", ORDERS)
    make_user("other@test.com", Role.creator)
    make_user("viewer@test.com", Role.viewer)
    with Client(creator_client.app) as other:
        other.login("other@test.com")
        assert other.get(f"{API}/datasets/{ds['id']}/fields").status_code == 200
        assert add_calc(other, ds["id"], "X", "1").status_code == 403
        assert other.post(f"{API}/datasets/{ds['id']}/parameters", json={"name": "P"}).status_code == 403
    with Client(creator_client.app) as viewer:
        viewer.login("viewer@test.com")
        assert viewer.get(f"{API}/datasets/{ds['id']}/fields").status_code == 403


# ------------------------------------------------------------------ joined and combined datasets
def test_join_datasets_and_refresh_when_a_parent_changes(creator_client):
    orders = upload_dataset(creator_client, "Orders", ORDERS)
    customers = upload_dataset(creator_client, "Customers", CUSTOMERS, "customers.csv")
    r = creator_client.post(
        f"{API}/datasets",
        json={
            "name": "Orders with customer",
            "source": {
                "join": {
                    "base": orders["id"],
                    "joins": [
                        {
                            "dataset": customers["id"],
                            "type": "left",
                            "alias": "Customer",
                            "on": [{"left": "customer_id", "right": "id"}],
                        }
                    ],
                }
            },
        },
    )
    assert r.status_code == 201, r.text
    joined = r.json()
    assert joined["kind"] == "join" and joined["connection_id"] is None and joined["row_count"] == 30
    assert [p["name"] for p in joined["parents"]] == ["Orders", "Customers"]
    names = [c["name"] for c in joined["columns"]]
    assert names[:6] == ORDERS[0] and "Customer.name" in names and "Customer.segment" in names
    f = fields(creator_client, joined["id"])
    assert f["Customer.name"]["label"] == "Customer Name"
    pv = creator_client.get(f"{API}/datasets/{joined['id']}/preview?limit=30").json()
    i = pv["columns"].index("Customer.name")
    assert {r[i] for r in pv["rows"]} == {
        "Emaar",
        "Noon",
        "ADNOC",
        None,
    }  # customer 4 has no match (left join)

    # a new customer file refreshes the joined dataset automatically
    new_customers = (CUSTOMERS[0], CUSTOMERS[1] + [[4, "Careem", "Tech"]])
    creator_client.put(
        f"{API}/connections/{customers['connection']['id']}/file",
        files={"file": ("customers.csv", csv_bytes(*new_customers))},
    )
    after = creator_client.get(f"{API}/datasets/{joined['id']}").json()
    assert after["current_version"] == 2
    assert creator_client.get(f"{API}/datasets/{joined['id']}/runs").json()[0]["trigger"] == "upstream"
    pv = creator_client.get(f"{API}/datasets/{joined['id']}/preview?limit=30").json()
    assert None not in {r[i] for r in pv["rows"]}

    # parents cannot be deleted while used; the joined dataset lists itself as a dependent
    assert "Orders with customer" in [
        d["name"] for d in creator_client.get(f"{API}/datasets/{orders['id']}").json()["dependents"]
    ]
    r = creator_client.delete(f"{API}/datasets/{customers['id']}")
    assert r.status_code == 409 and "Orders with customer" in r.json()["detail"]
    assert creator_client.delete(f"{API}/connections/{customers['connection']['id']}").status_code == 409


def test_inner_join_and_calculated_fields_on_joined_data(creator_client):
    orders = upload_dataset(creator_client, "Orders", ORDERS)
    customers = upload_dataset(creator_client, "Customers", CUSTOMERS, "customers.csv")
    joined = creator_client.post(
        f"{API}/datasets",
        json={
            "name": "Matched",
            "source": {
                "join": {
                    "base": orders["id"],
                    "joins": [
                        {
                            "dataset": customers["id"],
                            "type": "inner",
                            "on": [{"left": "customer_id", "right": "id"}],
                        }
                    ],
                }
            },
        },
    ).json()
    assert joined["row_count"] == 23  # orders for customers 1-3 only
    r = add_calc(creator_client, joined["id"], "Label", "[Customers.name] + ' / ' + [region]")
    assert r.status_code == 201, r.text


def test_join_validation(creator_client):
    orders = upload_dataset(creator_client, "Orders", ORDERS)
    customers = upload_dataset(creator_client, "Customers", CUSTOMERS, "customers.csv")
    base = {
        "base": orders["id"],
        "joins": [{"dataset": customers["id"], "on": [{"left": "nope", "right": "id"}]}],
    }
    r = creator_client.post(f"{API}/datasets", json={"name": "x", "source": {"join": base}})
    assert r.status_code == 422 and "nope" in r.json()["detail"]
    r = creator_client.post(
        f"{API}/datasets", json={"name": "x", "source": {"join": {"base": orders["id"], "joins": []}}}
    )
    assert r.status_code == 422
    r = creator_client.post(
        f"{API}/datasets", json={"name": "x", "source": {"join": {"base": 999, "joins": []}}}
    )
    assert r.status_code == 422
    # a dataset cannot be built from itself
    j = creator_client.post(
        f"{API}/datasets",
        json={
            "name": "J",
            "source": {
                "join": {
                    "base": orders["id"],
                    "joins": [{"dataset": customers["id"], "on": [{"left": "customer_id", "right": "id"}]}],
                }
            },
        },
    ).json()
    r = creator_client.put(
        f"{API}/datasets/{j['id']}",
        json={
            "name": "J",
            "source": {
                "join": {
                    "base": j["id"],
                    "joins": [{"dataset": customers["id"], "on": [{"left": "customer_id", "right": "id"}]}],
                }
            },
        },
    )
    assert r.status_code == 422 and "itself" in r.json()["detail"]


def test_union_datasets(creator_client):
    q1 = upload_dataset(
        creator_client, "Sales Q1", (["region", "amount"], [["UAE", 1], ["KSA", 2]]), "q1.csv"
    )
    q2 = upload_dataset(
        creator_client, "Sales Q2", (["region", "amount", "note"], [["QAT", 3, "late"]]), "q2.csv"
    )
    u = creator_client.post(
        f"{API}/datasets",
        json={
            "name": "Sales H1",
            "source": {"union": {"datasets": [q1["id"], q2["id"]], "source_column": "Quarter"}},
        },
    )
    assert u.status_code == 201, u.text
    u = u.json()
    assert u["kind"] == "union" and u["row_count"] == 3
    assert {c["name"] for c in u["columns"]} == {"region", "amount", "note", "Quarter"}
    pv = creator_client.get(f"{API}/datasets/{u['id']}/preview").json()
    i = pv["columns"].index("Quarter")
    assert sorted(r[i] for r in pv["rows"]) == ["Sales Q1", "Sales Q1", "Sales Q2"]
    assert (
        creator_client.post(
            f"{API}/datasets", json={"name": "x", "source": {"union": {"datasets": [q1["id"]]}}}
        ).status_code
        == 422
    )


def test_field_values(creator_client):
    ds = upload_dataset(creator_client, "Orders", ORDERS)
    r = creator_client.get(f"{API}/datasets/{ds['id']}/values", params={"field": "region"}).json()
    assert sorted(r["values"]) == ["KSA", "QAT", "UAE"] and r["truncated"] is False
    r = creator_client.get(
        f"{API}/datasets/{ds['id']}/values", params={"field": "order_id", "limit": 5}
    ).json()
    assert len(r["values"]) == 5 and r["truncated"] is True
    assert (
        creator_client.get(f"{API}/datasets/{ds['id']}/values", params={"field": "nope"}).status_code == 422
    )
