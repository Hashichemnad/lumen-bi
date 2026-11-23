"""Query engine: dimensions, measures, filters, table calculations, totals, sorting, limits, export."""

import csv
import io

import pytest

from app.models import Role
from tests.conftest import Client, make_user
from tests.test_model import add_calc, upload_dataset

API = "/api/v1"

SALES = (
    ["id", "region", "product", "amount", "qty", "order_date", "note"],
    [
        [1, "UAE", "A", 100.0, 1, "2026-01-05", "ok"],
        [2, "KSA", "A", 200.0, 2, "2026-01-20", "ok"],
        [3, "UAE", "B", 300.0, 3, "2026-02-03", "=HYPERLINK(1)"],
        [4, "QAT", "B", 400.0, 4, "2026-02-14", "ok"],
        [5, "KSA", "B", 500.0, 5, "2026-03-01", ""],
        [6, "UAE", "A", 600.0, 6, "2026-03-09", "it's"],
    ],
)


@pytest.fixture
def ds(creator_client):
    return upload_dataset(creator_client, "Sales", SALES)


def q(client, ds_id, **spec):
    return client.post(f"{API}/query", json={"dataset": ds_id, **spec})


def ok(client, ds_id, **spec) -> dict:
    r = q(client, ds_id, **spec)
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ basics
def test_dimension_and_default_aggregation(creator_client, ds):
    out = ok(creator_client, ds["id"], dimensions=["region"], measures=["amount"])
    assert out["rows"] == [["KSA", 700.0], ["QAT", 400.0], ["UAE", 1000.0]]
    region, amount = out["columns"]
    assert region["key"] == "region" and region["role"] == "dimension" and region["label"] == "Region"
    assert amount["key"] == "sum:amount" and amount["agg"] == "sum" and amount["label"] == "Sum of Amount"
    assert out["restricted"] is False and out["truncated"] is False


def test_aggregations_and_row_count(creator_client, ds):
    out = ok(
        creator_client,
        ds["id"],
        measures=[
            {"field": "amount", "agg": "avg"},
            {"field": "amount", "agg": "median"},
            {"field": "amount", "agg": "min"},
            {"field": "amount", "agg": "max"},
            {"field": "region", "agg": "count_distinct"},
            {"field": "amount", "agg": "percentile", "p": 0.5},
            {"field": "*", "agg": "count"},
        ],
    )
    assert out["rows"] == [[350.0, 350.0, 100.0, 600.0, 3, 350.0, 6]]
    assert out["columns"][-1]["label"] == "Number of rows"


def test_date_grain_and_part(creator_client, ds):
    out = ok(
        creator_client, ds["id"], dimensions=[{"field": "order_date", "grain": "month"}], measures=["qty"]
    )
    assert out["rows"] == [["2026-01-01", 3], ["2026-02-01", 7], ["2026-03-01", 11]]
    assert (
        out["columns"][0]["key"] == "order_date:month" and out["columns"][0]["label"] == "Order Date (Month)"
    )
    out = ok(
        creator_client, ds["id"], dimensions=[{"field": "order_date", "part": "quarter"}], measures=["qty"]
    )
    assert out["rows"] == [[1, 21]]


def test_calculated_fields_in_queries(creator_client, ds):
    assert add_calc(creator_client, ds["id"], "Unit price", "[amount] / [qty]").status_code == 201
    assert add_calc(creator_client, ds["id"], "Avg ticket", "SUM([amount]) / COUNT([id])").status_code == 201
    assert (
        add_calc(
            creator_client, ds["id"], "Big", "IF [amount] >= 400 THEN 'Big' ELSE 'Small' END"
        ).status_code
        == 201
    )
    out = ok(
        creator_client,
        ds["id"],
        dimensions=["Big"],
        measures=["Avg ticket", {"field": "Unit price", "agg": "max"}],
    )
    assert out["rows"] == [["Big", 500.0, 100.0], ["Small", 200.0, 100.0]]
    assert out["columns"][1]["key"] == "Avg ticket" and out["columns"][1]["agg"] is None


def test_parameters_can_be_overridden(creator_client, ds):
    r = creator_client.post(
        f"{API}/datasets/{ds['id']}/parameters",
        json={"name": "Rate", "data_type": "number", "default_value": 2},
    )
    assert r.status_code == 201, r.text
    add_calc(creator_client, ds["id"], "Converted", "SUM([amount]) * @Rate")
    assert ok(creator_client, ds["id"], measures=["Converted"])["rows"] == [[4200.0]]
    assert ok(creator_client, ds["id"], measures=["Converted"], params={"Rate": 3})["rows"] == [[6300.0]]
    r = q(creator_client, ds["id"], measures=["Converted"], params={"Rate": "x'); DROP TABLE users; --"})
    assert r.status_code == 400 and "number" in r.json()["detail"]


# ------------------------------------------------------------------ filters
@pytest.mark.parametrize(
    "flt, expected",
    [
        ({"field": "region", "op": "eq", "value": "UAE"}, 1000.0),
        ({"field": "region", "op": "neq", "value": "UAE"}, 1100.0),
        ({"field": "region", "op": "in", "values": ["UAE", "QAT"]}, 1400.0),
        ({"field": "region", "op": "in", "values": []}, None),
        ({"field": "region", "op": "not_in", "values": ["UAE"]}, 1100.0),
        ({"field": "amount", "op": "gt", "value": 300}, 1500.0),
        ({"field": "amount", "op": "gte", "value": "300"}, 1800.0),
        ({"field": "amount", "op": "lt", "value": 300}, 300.0),
        ({"field": "amount", "op": "between", "values": [200, 400]}, 900.0),
        ({"field": "amount", "op": "between", "values": [None, 200]}, 300.0),
        ({"field": "order_date", "op": "between", "values": ["2026-02-01", "2026-02-28"]}, 700.0),
        ({"field": "order_date", "op": "gte", "value": "2026-03-01", "grain": "month"}, 1100.0),
        ({"field": "order_date", "op": "in", "values": [2], "part": "month"}, 700.0),
        ({"field": "note", "op": "contains", "value": "HYPER"}, 300.0),
        ({"field": "note", "op": "not_contains", "value": "ok"}, 1400.0),
        ({"field": "note", "op": "starts_with", "value": "it"}, 600.0),
        ({"field": "note", "op": "is_null"}, 500.0),
        ({"field": "region", "op": "eq", "value": "x' OR '1'='1"}, None),
    ],
)
def test_filters(creator_client, ds, flt, expected):
    out = ok(creator_client, ds["id"], measures=["amount"], filters=[flt])
    assert out["rows"] == [[expected]]


def test_relative_date_filter(creator_client, ds):
    # Today is in 2026 in these tests' data window only if the clock says so; compare with a direct check
    out = ok(
        creator_client,
        ds["id"],
        measures=["amount"],
        filters=[{"field": "order_date", "op": "relative", "unit": "year", "n": 100}],
    )
    assert out["rows"] == [[2100.0]]
    out = ok(
        creator_client,
        ds["id"],
        measures=["amount"],
        filters=[{"field": "order_date", "op": "relative", "unit": "day", "n": 1}],
    )
    assert out["rows"] == [[None]]


def test_top_n_and_aggregate_filters(creator_client, ds):
    top = {"field": "region", "op": "top", "n": 2, "by": {"field": "amount", "agg": "sum"}}
    out = ok(creator_client, ds["id"], dimensions=["region"], measures=["amount"], filters=[top])
    assert out["rows"] == [["KSA", 700.0], ["UAE", 1000.0]]
    bottom = {**top, "op": "bottom", "n": 1}
    out = ok(creator_client, ds["id"], dimensions=["region"], measures=["amount"], filters=[bottom])
    assert out["rows"] == [["QAT", 400.0]]
    # Top N is ranked after the other filters (product B only)
    out = ok(
        creator_client,
        ds["id"],
        dimensions=["region"],
        measures=["amount"],
        filters=[{"field": "product", "op": "eq", "value": "B"}, {**top, "n": 1}],
    )
    assert out["rows"] == [["KSA", 500.0]]
    having = {"field": "amount", "agg": "sum", "op": "gt", "value": 500}
    out = ok(
        creator_client, ds["id"], dimensions=["region"], measures=["amount"], filters=[having], totals=True
    )
    assert out["rows"] == [["KSA", 700.0], ["UAE", 1000.0]]
    assert out["totals"] == [None, 1700.0]  # totals cover only the groups shown


# ------------------------------------------------------------------ table calculations
def test_table_calculations_along_dates(creator_client, ds):
    month = {"field": "order_date", "grain": "month"}
    calcs = ["running_total", "difference", "pct_change", "moving_avg", "pct_total", "rank"]
    measures = [{"field": "amount", "calc": {"type": c, "window": 2}} for c in calcs]
    out = ok(creator_client, ds["id"], dimensions=[month], measures=["amount", *measures])
    jan, feb, mar = out["rows"]
    assert [r[1] for r in out["rows"]] == [300.0, 700.0, 1100.0]
    assert [r[2] for r in out["rows"]] == [300.0, 1000.0, 2100.0]  # running total
    assert [jan[3], feb[3], mar[3]] == [None, 400.0, 400.0]  # difference
    assert jan[4] is None and feb[4] == pytest.approx(4 / 3)  # % change
    assert [jan[5], feb[5], mar[5]] == [300.0, 500.0, 900.0]  # moving average of 2
    assert sum(r[6] for r in out["rows"]) == pytest.approx(1.0)  # % of total
    assert [r[7] for r in out["rows"]] == [3, 2, 1]  # rank (highest first)
    cols = {c["key"]: c for c in out["columns"]}
    assert cols["sum:amount:pct_total"]["format"]["type"] == "percent"
    assert cols["sum:amount:running_total"]["label"] == "Running total of sum of Amount"


def test_table_calculation_partitions(creator_client, ds):
    out = ok(
        creator_client,
        ds["id"],
        dimensions=["region", "product"],
        measures=[
            {"field": "amount", "key": "share", "calc": {"type": "pct_total", "partition_by": ["region"]}},
            {"field": "amount", "key": "all", "calc": {"type": "pct_total", "partition_by": []}},
        ],
    )
    rows = {(r[0], r[1]): r[2:] for r in out["rows"]}
    assert rows[("UAE", "A")][0] == pytest.approx(0.7) and rows[("UAE", "B")][0] == pytest.approx(0.3)
    assert rows[("UAE", "A")][1] == pytest.approx(700 / 2100)
    r = q(
        creator_client,
        ds["id"],
        dimensions=["region"],
        measures=[{"field": "amount", "calc": {"type": "rank", "along": "nope"}}],
    )
    assert r.status_code == 400


def test_top_level_table_calc_applies_to_all_measures(creator_client, ds):
    out = ok(
        creator_client,
        ds["id"],
        dimensions=["region"],
        measures=["amount", "qty"],
        table_calc={"type": "pct_total"},
    )
    assert sum(r[1] for r in out["rows"]) == pytest.approx(1) and sum(
        r[2] for r in out["rows"]
    ) == pytest.approx(1)


# ------------------------------------------------------------------ sort, limit, totals, rows
def test_sort_limit_and_totals(creator_client, ds):
    out = ok(
        creator_client,
        ds["id"],
        dimensions=["region"],
        measures=["amount"],
        sort=[{"key": "sum:amount", "dir": "desc"}],
        limit=2,
        totals=True,
    )
    assert out["rows"] == [["UAE", 1000.0], ["KSA", 700.0]]
    assert out["truncated"] is True and out["totals"] == [None, 2100.0]
    r = q(creator_client, ds["id"], dimensions=["region"], sort=[{"key": "amount; DROP TABLE x"}])
    assert r.status_code == 400 and "not in the query" in r.json()["detail"]


def test_dimensions_only_gives_distinct_combinations(creator_client, ds):
    out = ok(creator_client, ds["id"], dimensions=["product", "region"])
    assert len(out["rows"]) == 5


def test_rows_mode(creator_client, ds):
    out = ok(
        creator_client,
        ds["id"],
        mode="rows",
        fields=["id", "region", "amount"],
        filters=[{"field": "region", "op": "eq", "value": "KSA"}],
        sort=[{"key": "amount", "dir": "desc"}],
    )
    assert out["rows"] == [[5, "KSA", 500.0], [2, "KSA", 200.0]]
    out = ok(creator_client, ds["id"], mode="rows", limit=1)
    assert [c["key"] for c in out["columns"]] == SALES[0] and out["truncated"] is True
    r = q(
        creator_client,
        ds["id"],
        mode="rows",
        filters=[{"field": "amount", "agg": "sum", "op": "gt", "value": 1}],
    )
    assert r.status_code == 400


@pytest.mark.parametrize(
    "spec, message",
    [
        ({"dimensions": ["nope"]}, "Unknown field"),
        ({"measures": [{"field": "region", "agg": "sum"}]}, "needs a number"),
        ({"dimensions": [{"field": "region", "grain": "month"}]}, "not a date"),
        ({"dimensions": ['region"); DROP TABLE users; --']}, "Unknown field"),
        ({}, "Add at least one field"),
        ({"dimensions": ["region", "region"]}, "twice"),
        ({"measures": [{"field": "amount", "agg": "none"}]}, "how to total"),
    ],
)
def test_invalid_queries_are_refused_clearly(creator_client, ds, spec, message):
    r = q(creator_client, ds["id"], **spec)
    assert r.status_code == 400 and message in r.json()["detail"], r.text


def test_results_are_cached_and_refreshed_by_a_new_sync(creator_client, ds):
    first = ok(creator_client, ds["id"], measures=["amount"])
    again = ok(creator_client, ds["id"], measures=["amount"])
    assert first["cached"] is False and again["cached"] is True and again["rows"] == first["rows"]
    header, rows = SALES
    from tests.test_model import csv_bytes

    r = creator_client.put(
        f"{API}/connections/{ds['connection']['id']}/file",
        files={"file": ("data.csv", csv_bytes(header, rows + [[7, "UAE", "A", 1.0, 1, "2026-03-10", ""]]))},
    )
    assert r.status_code == 200, r.text
    after = ok(creator_client, ds["id"], measures=["amount"])
    assert after["cached"] is False and after["rows"] == [[2101.0]]


def test_unsynced_dataset(creator_client, db):
    from app.models import Dataset

    later = Dataset(name="Later")
    db.add(later)
    db.commit()
    r = q(creator_client, later.id, measures=[{"field": "*", "agg": "count"}])
    assert r.status_code == 400 and "no data" in r.json()["detail"]


# ------------------------------------------------------------------ export and catalog
def test_export_csv(creator_client, ds):
    r = creator_client.post(
        f"{API}/query/export",
        json={"dataset": ds["id"], "mode": "rows", "fields": ["id", "note"], "sort": [{"key": "id"}]},
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert 'filename="Sales ' in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text.lstrip("﻿"))))
    assert rows[0] == ["Id", "Note"] and len(rows) == 7
    assert rows[3] == ["3", "'=HYPERLINK(1)"]  # formulas are neutralised for spreadsheet apps
    r = creator_client.post(
        f"{API}/query/export", json={"dataset": ds["id"], "dimensions": ["region"], "measures": ["amount"]}
    )
    assert list(csv.reader(io.StringIO(r.text.lstrip("﻿"))))[1] == ["KSA", "700.0"]


def test_catalog_and_field_values(creator_client, ds):
    add_calc(creator_client, ds["id"], "Secret formula", "[amount] * 2")
    names = [d["name"] for d in creator_client.get(f"{API}/catalog/datasets").json()]
    assert names == ["Sales"]
    detail = creator_client.get(f"{API}/catalog/datasets/{ds['id']}").json()
    assert "Secret formula" in [f["name"] for f in detail["fields"]]
    vals = creator_client.get(
        f"{API}/datasets/{ds['id']}/values", params={"field": "region", "order": "value"}
    ).json()
    assert vals["values"] == ["KSA", "QAT", "UAE"] and vals["counts"] == [2, 1, 3]
    vals = creator_client.get(
        f"{API}/datasets/{ds['id']}/values", params={"field": "region", "search": "a"}
    ).json()
    assert vals["values"] == ["UAE", "KSA", "QAT"]


def test_viewer_catalog_hides_formulas(creator_client, ds):
    add_calc(creator_client, ds["id"], "Secret formula", "[amount] * 2")
    creator_client.put(f"{API}/datasets/{ds['id']}/access", json={"everyone": True})
    make_user("viewer@test.com", Role.viewer)
    with Client(creator_client.app, base_url="http://testserver") as viewer:
        viewer.login("viewer@test.com")
        detail = viewer.get(f"{API}/catalog/datasets/{ds['id']}").json()
        secret = next(f for f in detail["fields"] if f["name"] == "Secret formula")
        assert "formula" not in secret and "config" not in secret
        # Viewers can query but not export
        assert (
            viewer.post(f"{API}/query", json={"dataset": ds["id"], "measures": ["amount"]}).status_code == 200
        )
        r = viewer.post(f"{API}/query/export", json={"dataset": ds["id"], "measures": ["amount"]})
        assert r.status_code == 403


def test_query_requires_login(client):
    assert client.post(f"{API}/query", json={"dataset": 1, "measures": ["x"]}).status_code == 401
    assert client.get(f"{API}/catalog/datasets").status_code == 401
